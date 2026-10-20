# Kairos Chain ($KAI): fork record

Kairos Chain is a fork of **Parano1d** (Copyright 2026 Paranoid Zero), modified
by the Kairos Chain contributors.
Upstream: https://github.com/proof-native/parano1d @ `5302b3f` (v2.0.4),
Apache-2.0. `LICENSE` is unchanged. `NOTICE` keeps the original text, and the
fork credit is appended to it. Every modified Rust source keeps its SPDX and
copyright header and has a `Modified by the Kairos Chain contributors` line.
Kairos Chain is an independent network. It is not Parano1d or NOID and must
never be presented as either.

Status: **all source changes are made. Nothing is compiled yet.** This PC has
no MSVC Build Tools, CMake, libclang or WSL (see "Build").

## Identity

| Item | Parano1d | Kairos Chain |
|---|---|---|
| Ticker / unit | NOID / μNOID | **KAI / μKAI** (6 decimals) |
| Binaries | parano1d, parano1d-cli, parano1d-miner, parano1d-gui | **kairos, kairos-cli, kairos-miner, kairos-gui** |
| Address | bech32m `o1…` | bech32m **`k1…`** (`ADDRESS_HRP = "k"`) |
| Magic | `NOID` 4E4F4944 | **`KAIR` 4B414952** |
| P2P / RPC ports | 9600 / 9601 | **9700 / 9701** |
| Protocol id / topics | `/noid/mainnet/860e70453390bf81/…` | **`/kai/mainnet/<genesis-id-prefix>/{1,blocks/1,txs/1}`**, derived at runtime from the genesis block id |
| Devnet topics | `/noid/devnet/…` | `/kai/devnet/…` |
| libp2p identify | `/noid/1.0.0` | `/kairos/1.0.0` |
| DNS seeds | mseed1-4.parano1d.org | **seed1-4.kairoschain.org** (placeholders) |
| Network profile digest | `NOID/P2P/NETWORK-PROFILE/V7`, NPQ6/NPS6 | `KAI/P2P/NETWORK-PROFILE/V1`, KPQ1/KPS1 |
| Domain | parano1d.org | kairoschain.org |

## Changed files (main ones)

- `noid_chain/src/consensus/genesis.rs`: rewritten. Contains the new timestamp, the burn-address coinbase, a nonce mined at startup (`GENESIS_NONCE_PIN`) and the GENESIS MIGRATION hook.
- `noid_chain/genesis/kai-genesis-migration.txt`: new file. It is the migration allocation and is empty by default.
- `noid_chain/src/storage/mdbx_context.rs`: genesis now starts from `genesis_state()`, and migrated slots are persisted as dirty segments with the owner index rebuilt.
- `noid_chain/src/consensus/network.rs`: magic, ports, genesis-derived namespace and seeds.
- `noid_p2p/src/{behaviour,protocol,network_profile,object_protocol,network,identity_store}.rs`: identify string, gossip id domain, devnet topics, profile digest domain and magics, object domains, relay domains, P2P key magic.
- `noid_poseidon2b/src/primitives.rs`: HRP `o` → `k`.
- Key and object separation, so Kairos keys and objects can never collide with Parano1d's:
  - wallet KDF tag: unchanged (`noid-deri`, `keystore.rs`). Kept on purpose: it is a fixed derivation constant, and changing it would change every address.
  - plain key magic → `kai_plain_key_v1`
  - Photo Key blake3 contexts → `Kairos Chain master secret …` (`noid_gui/src/secret.rs`)
  - contract object magic `NOIDOBJ3` → `KAIROBJ1`, `NOIDV2TX` → `KAIV2TX1`
  - `NOIDWZK1` → `KAIRWZK1`, plus the wallet journal magics `KAIRORF1`, `KAIRRCJ1`, `KAIRHSJ1`
- `noid_chain/src/consensus/params.rs`: `V1_1_ACTIVATION_HEIGHT = 1`, `MAINNET_V2_ACTIVATION_HEIGHT = 1`.
- `noid_chain/src/consensus/development_allocation.rs`: allocation disabled; miners get 100%. The fund constants were renamed to `DISABLED_FUND_ADDRESS_A/B`, set to the burn address, and are unreachable.
- `noid_node/build.rs`: release builds no longer require retirement keys.
- `scripts/kairos_generate_packs.sh`: new proof-material pipeline.
- Rebrand across all crates, docs (en/ru/zh), scripts, GUI i18n, release packaging. The GUI icons and installer files were renamed `Kairos-*`, `xyz.kairoschain.wallet.*` and `Kairos.iss`.
  - Kept on purpose: crate names `noid_*`, build env vars `NOID_*`, internal `MICRONOID` identifiers, the proof-system transcript domains (`b"NOID/…"` in ivc/recursive/fri). The network is already separated by genesis, magic and the profile digest, and those domains are bound into the regenerated matrices anyway.
  - `research/` is left as the upstream historical record.
- All hardcoded `o1…` addresses in tests and docs were re-encoded to valid `k1…` bech32m.

## Genesis parameters

| Parameter | Value |
|---|---|
| `GENESIS_TIMESTAMP` | `1_793_491_200` = **2026-11-01 00:00:00 UTC (placeholder, one constant)** |
| Coinbase | `GENESIS_BURN_ADDRESS` = all-zero address (no known key) |
| Target | upstream `GENESIS_TARGET` (trivial) |
| `log_slots` | 24 |
| State root | empty-tree root `5c7f3712…a5c98492` when migration is empty; computed otherwise |
| `active_slot_count` / `alloc_counter` | number of migration entries |
| Nonce | `GENESIS_NONCE_PIN = None`: the lowest valid nonce is searched once at startup. Pin it before launch using `cargo test -p noid_chain --lib -- consensus::genesis::tests::print_new_genesis --ignored --nocapture` |

The protocol id and topics follow the genesis block id automatically. Any change
to the timestamp or migration file therefore produces a separate network.

## Consensus profile

- **v2 from block 1**: 30 s blocks and the v2 joint bank (Small/Large classes, m23/m24 sizing per `docs/developers/build.md`). Genesis is the only pre-v2 block and carries no proof.
- **Economics**: the v2 table is unchanged in shape and restarts at block 1. Rewards start at **16 KAI**, then 11.3 → 8 → 5.65 → 4 → 2.83 → 2 → 1.41, and settle on a **1 KAI tail**. The rate steps down every **1,051,200 blocks** (one 365-day year at 30 s). 100% goes to miners. There is no development allocation.
- Fees and the state-growth burn are unchanged from upstream.

## GENESIS MIGRATION file format

The file is `noid_chain/genesis/kai-genesis-migration.txt` and is compiled in with `include_str!`.

```text
# comment lines and blank lines are ignored
<k1 address> <amount in μKAI>
k1qqq…xyz 1500000
k1ppp…abc,250000000
```

- One holder per line. Fields are separated by whitespace or a single comma.
- Amounts are integer **μKAI** (1 KAI = 1,000,000 μKAI, the same 6 decimals as the pump.fun SPL token), so a 1:1 migration copies the raw SPL balance.
- Duplicate addresses, zero amounts and non-`k1` addresses fail the build/startup with a line number.
- Entries become UTXOs at slots `0..n` in file order, with creation ids `1..n`.
- Holders must first generate a Kairos `k1…` address (Photo Key wallet or `kairos-cli`). The snapshot is mapped SPL wallet → `k1` address off-chain before the file is frozen.
- Changing the file changes the genesis root, block id and network namespace.

## Build

Required on Windows: **Visual Studio 2022 Build Tools with the "Desktop development with C++" workload** (provides MSVC `link.exe` and the Windows SDK), **CMake**, and **LLVM** (libclang for `mdbx-sys` bindgen; set `LIBCLANG_PATH=C:\Program Files\LLVM\bin`). Run cargo from the "x64 Native Tools" prompt or PowerShell, not Git Bash, because Git Bash's `/usr/bin/link` shadows `link.exe`. Linux or WSL with `build-essential cmake clang libclang-dev` also works and is the better fit for matrix generation.

```sh
cargo check --workspace                              # source check
cargo test -p noid_chain --lib consensus::           # genesis/network/allocation tests
cargo test -p noid_chain --lib -- consensus::genesis::tests::print_new_genesis --ignored --nocapture
cargo build --release -p noid_node --bins            # kairos, kairos-cli (dev, no packs)
./scripts/kairos_generate_packs.sh ../kairos-artifacts   # hours; Linux/WSL
./scripts/build_release.sh --pack ../kairos-artifacts/history-step-pack-v1 \
  --v2-pack ../kairos-artifacts/v2-pack --v2-pins ../kairos-artifacts/v2-pins.env \
  --retirement-keys ../kairos-artifacts/retirement-keys
```

Devnet: `cargo run --release --example isolated_v2_node --features isolated-v2-fork-testnet -- …` (loopback-only profile), or a single mainnet-profile node mining against itself. Mined addresses print as `k1…`.

## Remaining work (needs a compiler)

1. Install the toolchain, then run `cargo check --workspace` and fix any compile errors. The edits were made without a compiler. The riskiest spots are `genesis.rs`, `network.rs` (`kai_namespace`), `mdbx_context.rs::persist_genesis` and the disabled `development_allocation.rs`.
2. Update the upstream tests that hardcode Parano1d values and will now fail as expected:
   - the dev-payout tests in `template.rs`, `validation.rs` and `action_surface.rs`
   - the fixed-digest tests touched by the new magics and domains
   - the v2 activation-height tests (H210537 → H1), plus the retired-history and origin tests
3. Regenerate the proof material with `scripts/kairos_generate_packs.sh` and finish the retirement-key step it prints. The step-3 arguments still need checking against `noid_retire_history prepare`.
4. Deeper cleanup, optional: physically delete the v1/v1.1 code paths, the `retired-history` feature, the origin-recovery and retirement machinery, and the legacy matrices. Today they are dormant because v2 is active from block 1. They were not deleted blind, because they are wired through `build.rs`, the recursion bank and P2P origin transport.
5. Wallet discovery of migrated UTXOs: the owner index is rebuilt at genesis, but the wallet scan of the genesis state should be checked end to end.
6. Pin `GENESIS_NONCE_PIN` and set the final `GENESIS_TIMESTAMP`. Fill the migration file from the $KAI snapshot. Point the `seed1-4.kairoschain.org` DNS at live nodes.
