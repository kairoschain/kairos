# Command-line interfaces

The Core archive contains three executables:

| Executable | Role |
|---|---|
| `kairos` | Full node, wallet backend and optional internal proof/mining pipeline |
| `kairos-cli` | Thin JSON-RPC client for a running node |
| `kairos-miner` | Separate Poseidon2b nonce-search worker |

## Core daemon

Run an ordinary node with no mode argument:

```sh
kairos
```

Public daemon options are:

| Option | Meaning |
|---|---|
| `-c, --config FILE` | TOML configuration path |
| `--mode node` | Ordinary non-mining node |
| `--mode miner` | Node with internal proof and PoW pipeline |
| `--mode extminer` | Node-owned proof pipeline with external nonce worker |
| `--miner` | Shorthand for `--mode miner` |
| `--extminer` | Shorthand for `--mode extminer` |
| `--miner-address ADDRESS` | Mining payout address; active wallet owner is the default |
| `--cpu-threads N` | Shared internal-miner thread budget |
| `--data-dir PATH` | Chain, wallet and runtime data directory |
| `--p2p-listen HOST:PORT` | P2P listener; default `0.0.0.0:9700` |
| `--rpc-listen HOST:PORT` | JSON-RPC listener; default `127.0.0.1:9701` |
| `--seed ENDPOINT` | Add a bootstrap endpoint; repeatable |
| `--log LEVEL` | Tracing filter such as `error`, `warn`, `info` or `debug` |
| `--mining-key TOKEN` | External-mining bearer token; retained for compatibility |
| `--mining-key-file FILE` | Read the external-mining bearer token from an owner-only file |
| `--operator-key TOKEN` | Separate bearer token for the fixed pool accounting/payout RPC scope |
| `--operator-key-file FILE` | Read the pool operator token from an owner-only file |
| `--allow-custom-coinbase` | Permit an authenticated external worker to request its payout |
| `--v2-large-blocks` | Permit Large production for internal mining or external templates; Small is default, all nodes verify both |
| `--purge-state` | Clear the complete chain database and synchronize it again from peers |
| `--check-hardware` | Report production CPU support and exit without touching node data |

Mode-specific options are rejected when they cannot apply. `--cpu-threads`
belongs to internal miner mode. External-miner mode requires either
`--mining-key` or `--mining-key-file`; `--allow-custom-coinbase` requires
external-miner mode and a configured key.

`--purge-state` is a repair and upgrade tool, not a routine start option. It
removes headers, chain indexes, retained blocks, undo data and Live State from
the chain database. Wallet files, receipts and peer identity are stored
separately and remain; the node then authenticates the chain and State again
from peers.

Examples:

```sh
# Ordinary node
kairos --data-dir ~/.kairos/data

# Internal miner using 12 logical CPUs
kairos --mode miner --cpu-threads 12

# Node with a local external nonce worker
kairos --mode extminer --mining-key-file ~/.kairos/mining.key
```

Keep RPC on loopback unless it is protected by a private or authenticated transport. A bearer key authenticates requests but does not encrypt them. The operator credential can authorize `walletSend`; prefer `--operator-key-file`, use a distinct token from the mining credential, and firewall the listener. Core refuses to start a non-loopback RPC listener unless at least one scoped credential is configured. The GUI and CLI may connect from the actual TCP loopback address without a key even when scoped credentials are configured. RPC supports HTTP only and rejects WebSocket upgrades.

## External miner

`kairos-miner` requests an already-proved, immutable template and searches
only its 128-bit nonce.

| Option | Default | Meaning |
|---|---|---|
| `--rpc URL` | `http://127.0.0.1:9701` | Node JSON-RPC endpoint |
| `--key TOKEN` | — | Bearer token matching the node; retained for compatibility |
| `--key-file FILE` | — | Read the bearer token from an owner-only file |
| `--threads N` | `0` | PoW threads; zero uses every visible logical CPU |
| `--coinbase ADDRESS` | — | Custom `o1…` payout when the node explicitly permits it |
| `--poll-ms MS` | `500` | Delay before requesting another template |
| `--rpc-timeout SECONDS` | `180` | Time allowed per RPC, including proven-template construction; range 1–3,600 |
| `--blocks N` | `0` | Stop after N accepted blocks; zero runs continuously |
| `--log LEVEL` | `info` | Worker log filter |
| `--check-hardware` | — | Report production CPU support and exit |

Typical local use:

```sh
kairos-miner \
  --rpc http://127.0.0.1:9701 \
  --key-file ~/.kairos/mining.key \
  --threads 12
```

See [External miner](../operate/external-miner.md) for the remote transport and
custom-payout boundaries.

## Node client

`kairos-cli` is a thin client for a running Core node. It holds no separate key
and performs wallet operations through local JSON-RPC.

### Global options

```text
-r, --rpc URL   RPC endpoint
-j, --json      raw JSON output
--rpc-timeout SECONDS   per-request timeout, default 60, range 1–3600
```

The default endpoint is `http://127.0.0.1:9701`. Environment variable
`NOID_RPC` changes it. `--rpc` takes precedence.

Amounts entered by CLI wallet commands are in KAI with up to six decimal
places:

```text
1 KAI = 1,000,000 μKAI
```

### Node and chain

| Command | Arguments | Result |
|---|---|---|
| `status` | — | Tip, hash, difficulty and active State summary |
| `block-hash` | `HEIGHT` | Permanent nonce-bearing block ID |
| `block-header` | `HEIGHT` | Structured permanent header |
| `header` | `HEIGHT` | Raw canonical 212-byte header hex |
| `block` | `HEIGHT` | Raw full block hex when retained |
| `history-step-terminal` | — | Current local terminal hex |
| `tx` | `TXID` | Permanent confirmation location |
| `slot` | `INDEX` | Current slot contents |
| `utxos-of` | `o1…` | Current UTXOs owned by an address |
| `state` | — | Capacity, occupancy and encoded State size |
| `epoch` | — | Current 144-block user transaction anchor |

Examples:

```sh
kairos-cli status
kairos-cli block-header 420
kairos-cli block 420
kairos-cli slot 9700063
kairos-cli utxos-of k1...
```

`block` returns data only inside the 42-block body-retention window. `header`
and `block-header` remain available for every canonical height.

### Network and mining

| Command | Options | Result |
|---|---|---|
| `peers` | — | Connected peer count |
| `mining` | — | Difficulty, next reward and mining state |
| `block-template` | `--miner-addr o1…` | Node-owned external mining template |
| `submit-block` | `TEMPLATE_ID NONCE_HEX` | Submit one 16-byte little-endian nonce |

The external-mining commands are low-level. `kairos-miner` handles template
refresh and nonce encoding automatically.

### Fees and mempool

```sh
kairos-cli estimate-fee 2 --inputs 1
kairos-cli mempool
kairos-cli mempool-tx TXID
```

`estimate-fee` reports the current node-accepted minimum for the declared live
input and output counts. Mempool rows are logical `PagedSpend` intents, not
individual physical pages.

### Address validation

```sh
kairos-cli validate k1...
```

The command reports validity, canonical bech32m form and raw 32-byte payload.

### Wallet addresses

```sh
kairos-cli address
kairos-cli address --list
kairos-cli address --new
kairos-cli address --index 3
kairos-cli address --use 3
```

`--new`, `--index` and `--use` are mutually exclusive actions. A generated
address is inactive until selected. Sends use only the active owner's UTXOs
and return change to that owner.

### Balance and UTXOs

```sh
kairos-cli balance
kairos-cli utxos
kairos-cli scan
```

`balance` separates confirmed, reserved outbound, pending incoming and
spendable values. `scan` reloads the active owner from the verified persistent
owner index.

### Send

Preview:

```sh
kairos-cli send k1... 10.5 --dry-run
```

Submit with automatic fee:

```sh
kairos-cli send k1... 10.5
```

Specify an exact fee in KAI only when needed:

```sh
kairos-cli send k1... 10.5 --fee 0.012
```

The returned ID names the complete logical spend. Automatic fee selection is
recommended because occupancy and relay floor can change.

### History and receipts

```sh
kairos-cli history
kairos-cli history --last 20
kairos-cli history --address k1...
```

Export a confirmed outgoing receipt:

```sh
kairos-cli receipt TXID > receipt.hex
```

Verify it:

```sh
kairos-cli verify "$(tr -d '\n' < receipt.hex)"
```

Receipt export works only for a locally saved different-owner payment.

### Stop

```sh
kairos-cli stop
```

This requests graceful daemon shutdown. It is equivalent to the GUI's normal
exit path, not a process kill.

### Scripting

Use `--json` and check the process exit status:

```sh
height="$(
  kairos-cli --json status |
    jq -er '.height'
)"
```

Human-readable output and colors are presentation interfaces. Scripts should
consume JSON.

### Contracts

`kairos-cli contract` provides `protocol`, `payment`, `vault`, `allowance`,
`budget`, `recurring`, `vesting`, `create`, `fund`, `watch`, `status`, `instances`,
`call`, `receipt`, `verify` and `restore`. Amounts are KAI. `call --preview`
reviews an exact instance; `--expected-txid` binds the reviewed body.
See [complete workflow](../contracts/api.md#cli-workflow) and each command
with `--help`. Contract API access uses local owner scope.
