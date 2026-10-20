// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Paranoid Zero.
// Modified by the Kairos Chain contributors: new genesis timestamp, runtime
// genesis nonce search and the GENESIS MIGRATION allocation hook.

//! Kairos Chain genesis block construction.
//!
//! The genesis block is hardcoded: same bytes on every node.
//! It has:
//! - The GENESIS MIGRATION allocation (empty by default, see below)
//! - Fixed Poseidon2b PoW target
//! - Coinbase to the burn address (no private key is known)
//! - no transported HistoryStep terminal; genesis is the built-in recursion boundary
//!
//! # Genesis migration
//!
//! `$KAI` launched first as a pump.fun SPL token. At genesis the holder
//! snapshot is migrated 1:1 by compiling the file
//! `noid_chain/genesis/kai-genesis-migration.txt` into the binary. Every node
//! therefore commits to the same allocation, and a node built with a different
//! file computes a different genesis state root (and block id) and can never
//! join the network. See `KAIROS-FORK.md` for the file format.
//!
//! With an empty migration file the genesis state is the exact sparse-Merkle
//! root of an empty UTXO tree.

use std::sync::OnceLock;

use crate::block_header::BlockHeader;
use crate::consensus::{
    params::{GENESIS_TARGET, LOG_SLOTS_GENESIS},
    pow::search_pow,
};
use noid_poseidon2b::primitives::Address;

/// Kairos Chain genesis timestamp. PLACEHOLDER: 2026-11-01 00:00:00 UTC.
/// Change this single constant before launch; the genesis nonce, block id,
/// protocol id and gossip topics are all derived from it.
pub const GENESIS_TIMESTAMP: u64 = 1_793_491_200;

/// The genesis burn address — coinbase recipient at height 0.
/// Uses a zero address; no private key is known.
pub const GENESIS_BURN_ADDRESS: Address = Address([0u8; 32]);

/// Optional pinned genesis nonce. `None` searches for the lowest valid nonce
/// once at startup (the genesis target is trivially satisfiable). After the
/// launch parameters are final, run
/// `cargo test -p noid_chain --lib -- consensus::genesis::tests::print_new_genesis --ignored --nocapture`
/// and pin the printed nonce here.
const GENESIS_NONCE_PIN: Option<u128> = None;

/// Raw GENESIS MIGRATION file, compiled in. Empty by default.
const GENESIS_MIGRATION_FILE: &str = include_str!("../../genesis/kai-genesis-migration.txt");

/// Smallest unit per KAI (6 decimals: 1 KAI = 1,000,000 μKAI).
pub const MICRO_KAI_PER_KAI: u64 = 1_000_000;

/// One GENESIS MIGRATION entry: a `k1…` owner and an amount in μKAI.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GenesisMigrationEntry {
    pub owner: Address,
    pub amount_micro_kai: u64,
}

/// Parse a GENESIS MIGRATION file.
///
/// Format: one entry per line, `<k1 address> <amount>`, separated by
/// whitespace or a single comma. `<amount>` is in μKAI (integer). Blank lines
/// and lines starting with `#` are ignored. Duplicate owners and zero amounts
/// are rejected so the file maps 1:1 onto the holder snapshot.
pub fn parse_genesis_migration(text: &str) -> Result<Vec<GenesisMigrationEntry>, String> {
    let mut entries = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for (index, raw) in text.lines().enumerate() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut parts = line
            .split(|c: char| c == ',' || c.is_whitespace())
            .filter(|part| !part.is_empty());
        let (Some(address), Some(amount), None) = (parts.next(), parts.next(), parts.next())
        else {
            return Err(format!("line {}: expected `<k1 address> <amount>`", index + 1));
        };
        let owner = Address::parse(address)
            .map_err(|error| format!("line {}: invalid address: {error}", index + 1))?;
        let amount_micro_kai: u64 = amount
            .parse()
            .map_err(|_| format!("line {}: amount must be an integer μKAI value", index + 1))?;
        if amount_micro_kai == 0 {
            return Err(format!("line {}: zero amount", index + 1));
        }
        if !seen.insert(owner) {
            return Err(format!("line {}: duplicate owner {address}", index + 1));
        }
        entries.push(GenesisMigrationEntry {
            owner,
            amount_micro_kai,
        });
    }
    Ok(entries)
}

/// The compiled-in GENESIS MIGRATION allocation (empty by default).
pub fn genesis_migration() -> &'static [GenesisMigrationEntry] {
    static ENTRIES: OnceLock<Vec<GenesisMigrationEntry>> = OnceLock::new();
    ENTRIES.get_or_init(|| {
        parse_genesis_migration(GENESIS_MIGRATION_FILE)
            .unwrap_or_else(|error| panic!("invalid genesis migration file: {error}"))
    })
}

/// Total μKAI created by the GENESIS MIGRATION allocation.
pub fn genesis_migration_total() -> u128 {
    genesis_migration()
        .iter()
        .map(|entry| u128::from(entry.amount_micro_kai))
        .sum()
}

/// Build the canonical genesis UTXO state: one live slot per migration entry,
/// in file order at slots `0..n`, with creation ids `1..=n`.
pub fn genesis_state() -> crate::state::ChainState {
    use crate::fri_state::SlotValue;
    let entries = genesis_migration();
    if entries.is_empty() {
        return crate::state::ChainState::new();
    }
    let slots: Vec<(u32, SlotValue)> = entries
        .iter()
        .enumerate()
        .map(|(index, entry)| {
            (
                index as u32,
                SlotValue::with_owner_fields(
                    entry.amount_micro_kai,
                    index as u64 + 1,
                    entry.owner.as_fields(),
                ),
            )
        })
        .collect();
    crate::state::ChainState::from_sparse_utxos(
        LOG_SLOTS_GENESIS as usize,
        &slots,
        entries.len() as u64,
    )
    .expect("genesis migration slots are unique, live and in range")
}

/// Build the canonical genesis block header.
///
/// Every node must produce byte-identical output from this function.
pub fn genesis_header() -> BlockHeader {
    static HEADER: OnceLock<BlockHeader> = OnceLock::new();
    *HEADER.get_or_init(|| {
        let mut header = genesis_header_template();
        header.nonce = match GENESIS_NONCE_PIN {
            Some(nonce) => nonce,
            None => find_genesis_nonce(),
        };
        header
    })
}

fn genesis_header_template() -> BlockHeader {
    let migrated = genesis_migration().len() as u64;
    BlockHeader {
        prev_block_hash: [0u8; 32],
        state_root: genesis_state_root(),
        tx_root: [0u8; 32],
        timestamp: GENESIS_TIMESTAMP,
        height: 0,
        miner_address: GENESIS_BURN_ADDRESS,
        nonce: 0,
        difficulty_target: GENESIS_TARGET,
        // Genesis is built in and has no attached HistoryStep terminal.
        log_slots: LOG_SLOTS_GENESIS,
        active_slot_count: migrated,
        alloc_counter: migrated,
    }
}

/// The canonical genesis state root.
pub fn genesis_state_root() -> [u8; 32] {
    static ROOT: OnceLock<[u8; 32]> = OnceLock::new();
    *ROOT.get_or_init(|| {
        if genesis_migration().is_empty() {
            EMPTY_GENESIS_STATE_ROOT
        } else {
            genesis_state().state_root()
        }
    })
}

/// Pre-computed root of the empty state. All 2^24 slots are zero.
const EMPTY_GENESIS_STATE_ROOT: [u8; 32] = [
    0x5c, 0x7f, 0x37, 0x12, 0x61, 0x1c, 0x83, 0x6a, 0x2c, 0x6a, 0xb0, 0x44, 0x8d, 0x32, 0xc2, 0x71,
    0xf0, 0x4e, 0x32, 0x13, 0x56, 0x0e, 0x8a, 0x53, 0x21, 0x33, 0x1c, 0xd6, 0xa5, 0xc9, 0x84, 0x92,
];

/// Find the lowest valid genesis nonce for the current parameters.
pub fn find_genesis_nonce() -> u128 {
    search_pow(&genesis_header_template(), 0, 2_000_000_000)
        .expect("genesis target is trivially satisfiable")
}

/// Short hex prefix of the genesis block id, used to namespace the p2p
/// protocol id and gossip topics.
pub fn genesis_id_prefix() -> &'static str {
    static PREFIX: OnceLock<String> = OnceLock::new();
    PREFIX.get_or_init(|| {
        crate::block_header::block_id(&genesis_header())[..8]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn genesis_header_is_deterministic() {
        assert_eq!(genesis_header(), genesis_header());
        assert_eq!(genesis_header().timestamp, GENESIS_TIMESTAMP);
    }

    #[test]
    fn genesis_header_fields() {
        let h = genesis_header();
        assert_eq!(h.height, 0);
        assert_eq!(h.prev_block_hash, [0u8; 32]);
        assert_eq!(h.difficulty_target, GENESIS_TARGET);
        assert_eq!(h.log_slots, LOG_SLOTS_GENESIS);
        assert_eq!(h.miner_address, GENESIS_BURN_ADDRESS);
    }

    #[test]
    fn empty_genesis_state_root_matches_computed() {
        let mut state = crate::state::ChainState::with_log_slots(24);
        assert_eq!(state.state_root(), EMPTY_GENESIS_STATE_ROOT);
    }

    #[test]
    fn migration_parser_accepts_documented_format() {
        let owner = Address([7u8; 32]).to_bech32();
        let text = format!("# comment\n\n{owner} 1500000\n");
        let entries = parse_genesis_migration(&text).unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].amount_micro_kai, 1_500_000);
        assert!(parse_genesis_migration(&format!("{owner} 1\n{owner} 2\n")).is_err());
        assert!(parse_genesis_migration(&format!("{owner} 0\n")).is_err());
        assert!(parse_genesis_migration("o1abc 5\n").is_err());
    }

    /// Print the genesis parameters for pinning.
    /// Run with: cargo test -p noid_chain --lib -- consensus::genesis::tests::print_new_genesis --ignored --nocapture
    #[test]
    #[ignore]
    fn print_new_genesis() {
        println!("GENESIS_TIMESTAMP: {GENESIS_TIMESTAMP}");
        println!("GENESIS_STATE_ROOT: {:02x?}", genesis_state_root());
        println!("GENESIS_NONCE: {}", find_genesis_nonce());
        println!("GENESIS_ID_PREFIX: {}", genesis_id_prefix());
        println!("MIGRATED: {} entries, {} μKAI", genesis_migration().len(), genesis_migration_total());
    }

    #[test]
    fn genesis_nonce_satisfies_pow() {
        use crate::consensus::pow::validate_pow;
        assert!(validate_pow(&genesis_header()).is_ok());
    }

    #[test]
    fn genesis_timestamp_is_kairos_placeholder() {
        // 2026-11-01 00:00:00 UTC
        assert_eq!(GENESIS_TIMESTAMP, 1_793_491_200);
    }
}
