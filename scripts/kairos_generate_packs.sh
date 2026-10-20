#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Kairos Chain contributors. New file (not present in upstream Parano1d).
#
# Regenerate ALL proof material for the Kairos Chain profile (v2 from block 1).
# Parano1d's frozen packs are bound to its own schedule (v2 at H210537) and
# genesis; they cannot be reused. Run on a Linux/macOS host or WSL with the
# full toolchain (C/C++ compiler, CMake, libclang). Expect several hours and
# ~64 GiB RAM for matrix generation.
#
# Usage: ./scripts/kairos_generate_packs.sh OUTPUT_ROOT
# Produces:
#   OUTPUT_ROOT/history-step-pack-v1/   legacy metadata root (+ pins.env)
#   OUTPUT_ROOT/v2-pack/                v2-runtime-metadata.bin, v2-small/v2-large matrices
#   OUTPUT_ROOT/v2-pins.env             NOID_V2_RELEASE_BANK + retirement key pins
set -Eeuo pipefail
OUT=${1:?usage: kairos_generate_packs.sh OUTPUT_ROOT}
ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd -P)"

echo "==> 1/3 legacy HistoryStep root pack (genesis fixtures, Kairos genesis)"
./scripts/generate_history_step_pack.sh "$OUT/history-step-pack-v1"
# shellcheck disable=SC1091
LEGACY_PIN=$(sed -n 's/^NOID_HISTORY_STEP_RUNTIME_METADATA_RELEASE_DIGEST=//p' "$OUT/history-step-pack-v1/pins.env")
[[ $LEGACY_PIN =~ ^[0-9a-f]{64}$ ]] || { echo "legacy pin not found in pins.env" >&2; exit 1; }

echo "==> 2/3 freeze the v2 bank under the Kairos schedule (v2 at height 1)"
cargo build --release --locked -p bench_prover --bin noid_v2_capacity
target/release/noid_v2_capacity joint-freeze-mainnet \
  "$OUT/history-step-pack-v1" "$LEGACY_PIN" "$OUT/v2-pack" \
  63 504 63 504 63 --large-pages=206

echo "==> 3/3 retirement keys (preprocessing keys for the legacy classes)"
cargo build --release --locked -p bench_prover --bin noid_retire_history
cat <<NOTE
Run 'target/release/noid_retire_history prepare' with:
  legacy pack  : $OUT/history-step-pack-v1
  legacy pin   : $LEGACY_PIN
  v2 metadata  : $OUT/v2-pack/v2-runtime-metadata.bin
  v2 bank pin  : (printed by step 2)
  legacy origin: the Kairos genesis (height 0)
  output       : $OUT/retirement-keys
then write $OUT/v2-pins.env with exactly:
  NOID_V2_RELEASE_BANK=<bank pin>
  NOID_RETIREMENT_KEY_0_PIN=<sha of class-0.key pin>
  NOID_RETIREMENT_KEY_1_PIN=<sha of class-1.key pin>
and build:
  ./scripts/build_release.sh --pack $OUT/history-step-pack-v1 \
    --v2-pack $OUT/v2-pack --v2-pins $OUT/v2-pins.env \
    --retirement-keys $OUT/retirement-keys
NOTE
