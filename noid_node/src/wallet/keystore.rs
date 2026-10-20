// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Paranoid Zero.
// Modified by the Kairos Chain contributors (rebrand to Kairos Chain).

//! Plaintext key file: master_secret → disk.
//!
//! Format: `noid_plain_key_1` (16 bytes magic) + secret (32 bytes) = 48 bytes.
//!
//! Security model: the file is stored at `~/.kairos/data/wallet.key` with
//! permissions 0o600 (owner-only). No encryption — the OS filesystem is the
//! security boundary during development. Future versions will derive the
//! master secret from a user-chosen file (photo, document, etc.) instead of
//! generating random bytes.

#[cfg(unix)]
use std::fs::File;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use rand::RngCore;
use zeroize::{Zeroize, ZeroizeOnDrop, Zeroizing};

use noid_poseidon2b::primitives::{Address, SpendSecret};

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum KeystoreError {
    #[error("wallet already exists at {0}")]
    AlreadyExists(PathBuf),
    #[error("wallet file not found at {0}")]
    NotFound(PathBuf),
    #[error("invalid wallet file format")]
    InvalidFormat,
    #[cfg(unix)]
    #[error("wallet file permissions are insecure: expected 0600, got {mode:04o}")]
    InsecurePermissions { mode: u32 },
    #[cfg(unix)]
    #[error("wallet file belongs to uid {actual}, expected current uid {expected}")]
    WrongOwner { actual: u32, expected: u32 },
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    #[error("wallet artifact: {0}")]
    Artifact(String),
}

// ---------------------------------------------------------------------------
// On-disk format (plaintext)
// ---------------------------------------------------------------------------

const PLAIN_MAGIC: &[u8; 16] = b"kai_plain_key_v1";
const SECRET_LEN: usize = 32;
const PLAIN_FILE_LEN: usize = 16 + SECRET_LEN; // 48 bytes

// ---------------------------------------------------------------------------
// MasterSecret
// ---------------------------------------------------------------------------

/// The decrypted master secret (zeroized on drop).
///
/// The field and the type are private to the wallet module. There is no raw
/// getter, formatter, clone, comparison, hash, or serialization surface.
#[derive(Zeroize, ZeroizeOnDrop)]
pub(super) struct MasterSecret([u8; SECRET_LEN]);

impl MasterSecret {
    /// Derive the spending secret for address index `n`.
    ///
    /// `spend_secret_n = Poseidon2b(master_secret, n, domain_tag)`
    ///
    /// The derived secret is used only by the wallet's witness-hiding authorization prover.
    /// It NEVER leaves the daemon.
    pub(super) fn derive_spend_secret(&self, index: u32) -> SpendSecret {
        use noid_core::{Block128, TowerField};
        use noid_poseidon2b::native::compression::Poseidon2bSponge;

        let mut sponge = Poseidon2bSponge::with_iv([Block128::ZERO; 2]);
        let mut master_fields = Zeroizing::new([
            Block128::from(u128::from_le_bytes(self.0[..16].try_into().unwrap())),
            Block128::from(u128::from_le_bytes(self.0[16..].try_into().unwrap())),
        ]);
        sponge.absorb(master_fields[0]);
        sponge.absorb(master_fields[1]);
        master_fields.zeroize();
        sponge.absorb(Block128::from(index as u128));
        sponge.absorb(Block128::from(0x6E6F69642D64657269_u128)); // "noid-deri"
        let mut digest = sponge.finalize();
        let secret = SpendSecret::from_bytes(digest);
        digest.zeroize();
        secret
    }

    /// Derive the public address for index `n` (safe to share).
    pub(super) fn derive_address(&self, index: u32) -> Address {
        let secret = self.derive_spend_secret(index);
        noid_poseidon2b::primitives::derive_address(&secret)
    }
}

// ---------------------------------------------------------------------------
// Keystore
// ---------------------------------------------------------------------------

/// Manages the plaintext wallet key file on disk.
pub(super) struct Keystore {
    path: PathBuf,
}

struct TemporaryKeyFile {
    path: PathBuf,
    armed: bool,
}

impl TemporaryKeyFile {
    fn new(path: PathBuf) -> Self {
        Self { path, armed: true }
    }

    fn remove(mut self) -> std::io::Result<()> {
        std::fs::remove_file(&self.path)?;
        self.armed = false;
        Ok(())
    }
}

impl Drop for TemporaryKeyFile {
    fn drop(&mut self) {
        if self.armed {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

impl Keystore {
    pub(super) fn new(path: impl AsRef<Path>) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
        }
    }

    pub(super) fn exists(&self) -> bool {
        self.path.exists()
    }

    /// Create a new wallet with a randomly generated secret.
    /// Writes `magic[16] + secret[32]` = 48 bytes with mode 0o600.
    /// Fails if the file already exists.
    pub(super) fn create_plain(&self) -> Result<MasterSecret, KeystoreError> {
        if self.exists() {
            return Err(KeystoreError::AlreadyExists(self.path.clone()));
        }
        let mut secret = Zeroizing::new([0u8; SECRET_LEN]);
        rand::thread_rng().fill_bytes(&mut *secret);

        let mut buf = Zeroizing::new(Vec::with_capacity(PLAIN_FILE_LEN));
        buf.extend_from_slice(PLAIN_MAGIC);
        buf.extend_from_slice(&*secret);

        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        // Create a unique owner-only inode before the first secret byte is
        // written. `create_new` and O_NOFOLLOW reject a pre-existing temp path
        // or symlink. Linking that inode into the final name is atomic and,
        // unlike rename on Unix, cannot replace a wallet created concurrently.
        let tmp = self
            .path
            .with_extension(format!("tmp.{:032x}", rand::random::<u128>()));
        let temporary = TemporaryKeyFile::new(tmp.clone());
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options
                .mode(0o600)
                .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW);
        }
        let mut file = options.open(&tmp)?;
        file.write_all(&buf)?;
        file.sync_all()?;
        drop(file);

        match std::fs::hard_link(&tmp, &self.path) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                return Err(KeystoreError::AlreadyExists(self.path.clone()));
            }
            Err(error) => return Err(error.into()),
        }
        // The final link is already durable and safe to use. Temp cleanup is
        // best-effort: its guard retries once on failure, and any orphan keeps
        // the same owner-only mode rather than turning successful wallet
        // creation into a misleading externally-visible error.
        let _ = temporary.remove();

        #[cfg(unix)]
        if let Some(parent) = self.path.parent() {
            File::open(parent)?.sync_all()?;
        }

        let master = MasterSecret(*secret);
        secret.zeroize();
        Ok(master)
    }

    /// Load a plaintext wallet key file.
    pub(super) fn load_plain(&self) -> Result<MasterSecret, KeystoreError> {
        let data = self.read_plain_bytes()?;
        let mut secret = Zeroizing::new([0u8; SECRET_LEN]);
        secret.copy_from_slice(&data[16..]);
        let master = MasterSecret(*secret);
        secret.zeroize();
        Ok(master)
    }

    /// Export only the 32-byte master secret as lowercase hexadecimal.
    ///
    /// The on-disk magic is an implementation detail and is deliberately not
    /// exposed to the user-facing import/export surface.
    pub(super) fn export_master_secret_hex(&self) -> Result<Zeroizing<String>, KeystoreError> {
        let data = self.read_plain_bytes()?;
        Ok(Zeroizing::new(hex::encode(&data[PLAIN_MAGIC.len()..])))
    }

    /// Build the private on-disk key artifact from one validated master
    /// secret. Callers keep the returned bytes zeroized and install them
    /// through the wallet's atomic replacement path.
    pub(super) fn encode_plain_file(master_secret: &[u8; SECRET_LEN]) -> Zeroizing<Vec<u8>> {
        let mut encoded = Zeroizing::new(Vec::with_capacity(PLAIN_FILE_LEN));
        encoded.extend_from_slice(PLAIN_MAGIC);
        encoded.extend_from_slice(master_secret);
        encoded
    }

    fn read_plain_bytes(&self) -> Result<Zeroizing<Vec<u8>>, KeystoreError> {
        if !self.exists() {
            return Err(KeystoreError::NotFound(self.path.clone()));
        }
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW);
        }
        let file = options.open(&self.path)?;
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err(KeystoreError::InvalidFormat);
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            let mode = metadata.permissions().mode() & 0o777;
            if mode != 0o600 {
                return Err(KeystoreError::InsecurePermissions { mode });
            }
            let actual = metadata.uid();
            // SAFETY: `geteuid` has no preconditions and does not dereference
            // pointers or mutate process state.
            let expected = unsafe { libc::geteuid() };
            if actual != expected {
                return Err(KeystoreError::WrongOwner { actual, expected });
            }
        }
        let mut data = Zeroizing::new(Vec::with_capacity(PLAIN_FILE_LEN + 1));
        file.take((PLAIN_FILE_LEN + 1) as u64)
            .read_to_end(&mut data)?;
        if data.len() != PLAIN_FILE_LEN {
            return Err(KeystoreError::InvalidFormat);
        }
        if &data[..16] != PLAIN_MAGIC.as_ref() {
            return Err(KeystoreError::InvalidFormat);
        }
        Ok(data)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn create_and_load_plain() {
        let dir = TempDir::new().unwrap();
        let ks = Keystore::new(dir.path().join("wallet.key"));
        let secret = ks.create_plain().unwrap();
        let loaded = ks.load_plain().unwrap();
        assert_eq!(secret.derive_address(0), loaded.derive_address(0));
        assert_eq!(secret.derive_address(99), loaded.derive_address(99));
    }

    #[test]
    fn double_create_fails() {
        let dir = TempDir::new().unwrap();
        let ks = Keystore::new(dir.path().join("wallet.key"));
        ks.create_plain().unwrap();
        assert!(matches!(
            ks.create_plain(),
            Err(KeystoreError::AlreadyExists(_))
        ));
    }

    #[test]
    fn load_missing_fails() {
        let dir = TempDir::new().unwrap();
        let ks = Keystore::new(dir.path().join("wallet.key"));
        assert!(matches!(ks.load_plain(), Err(KeystoreError::NotFound(_))));
    }

    #[test]
    fn address_derivation_deterministic() {
        let dir = TempDir::new().unwrap();
        let ks = Keystore::new(dir.path().join("wallet.key"));
        let secret = ks.create_plain().unwrap();
        assert_eq!(secret.derive_address(0), secret.derive_address(0));
        assert_ne!(secret.derive_address(0), secret.derive_address(1));
    }

    #[test]
    fn spend_secret_differs_per_index() {
        let dir = TempDir::new().unwrap();
        let ks = Keystore::new(dir.path().join("wallet.key"));
        let master = ks.create_plain().unwrap();
        assert_ne!(master.derive_address(0), master.derive_address(1));
    }

    #[test]
    fn master_secret_traits_and_explicit_zeroize_are_pinned() {
        static_assertions::assert_not_impl_any!(
            MasterSecret: Copy,
            Clone,
            std::fmt::Debug,
            PartialEq,
            Eq,
            std::hash::Hash,
            serde::Serialize,
            serde::de::DeserializeOwned
        );
        fn assert_zeroize<T: Zeroize + ZeroizeOnDrop>() {}
        assert_zeroize::<MasterSecret>();

        let mut secret = MasterSecret([0xA6; SECRET_LEN]);
        secret.zeroize();
        assert_eq!(secret.0, [0u8; SECRET_LEN]);
    }

    #[cfg(unix)]
    #[test]
    fn key_is_private_before_and_after_load() {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};

        let dir = TempDir::new().unwrap();
        let path = dir.path().join("wallet.key");
        let ks = Keystore::new(&path);
        ks.create_plain().unwrap();

        let metadata = std::fs::symlink_metadata(&path).unwrap();
        assert!(metadata.file_type().is_file());
        assert_eq!(metadata.permissions().mode() & 0o777, 0o600);
        assert_eq!(metadata.uid(), unsafe { libc::geteuid() });
        ks.load_plain().unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn load_rejects_public_permissions_and_symlinks() {
        use std::os::unix::fs::{symlink, PermissionsExt};

        let dir = TempDir::new().unwrap();
        let path = dir.path().join("wallet.key");
        let ks = Keystore::new(&path);
        ks.create_plain().unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(matches!(
            ks.load_plain(),
            Err(KeystoreError::InsecurePermissions { mode: 0o644 })
        ));

        let link = dir.path().join("wallet-link.key");
        symlink(&path, &link).unwrap();
        let linked = Keystore::new(link);
        assert!(matches!(linked.load_plain(), Err(KeystoreError::Io(_))));
    }
}
