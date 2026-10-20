# Kairos ①

Kairos is a live proof-native L1 where the present proves the past. Proofs
establish validity. PoW sets canonical order. Signatureless authorization,
execution and recursion share one transparent end-to-end post-quantum stack. V2
adds proof-native contracts: programmable live rights verifiable after pruning.
Nodes verify state and rights from genesis without replay or trusted
checkpoints. Block bodies and transactions are pruned after retention. Receipts
remain verifiable. Mainnet is live.

## From live value to live rights

A payment proves the right to spend value. A v2 contract additionally commits
to **how that right may be exercised**: who may act, at which height, for how
much, and how counters change. A call proves the permitted transition into a
successor or closes the right. That proof becomes part of block validity.

Nodes keep live commitments instead of requiring a permanent contract execution
log. Participants keep public terms and portable receipts. This lets budgets,
prepaid services, delegated spending, delayed access and staged payments share
the same recursive validation model as ordinary transfers.

The bounded integer core offers 16 instructions, two persistent counters,
checked arithmetic, conditions and height access. All users can create programs
within this ABI without a separate circuit or matrix. Six templates and a
custom editor are available through GUI, CLI and RPC. Calls require authorized
transactions; consensus does not run background timers.

[Understand proof-native contracts](concepts/proof-native-contracts.md) ·
[Build and use contracts](contracts/index.md) · [API](contracts/api.md) ·
[GUI walkthrough](contracts/gui.md)

## What the node retains

State is an exact sparse vector of live outputs. Spent slots are cleared and
reused with fresh creation identifiers. Empty segments are virtual. Nodes
retain permanent compact headers, a recursive terminal, 42 recent bodies and
bounded undo data. A joining node authenticates State and the recent suffix
without replaying old execution.

Header validation still grows with chain height, and State transfer with the
live set. Public transactions can be archived by third parties. Zero knowledge
protects the spending secret; pruning is not concealment.

[Architecture](architecture/overview.md) · [Synchronization](architecture/synchronization.md)

## V2 profile

| Parameter | Value |
| --- | --- |
| Target interval / ASERT half-life | 30 s / 180 s |
| Small, m23 | 63 pages / 504 inputs / 63 calls |
| Large, m24 | 206 pages / 504 inputs / 63 calls |
| Large production | Server opt-in `--v2-large-blocks` |
| Hard finality / maximum reorg | 18 / 17 blocks |
| Terminal cap | 1,100,000 bytes |

The budgets apply together. Calls share pages with ordinary payments.
Large can hold 63 calls plus 143 one-page payments within the common input
budget. The primary coinbase is separate; an extra mandatory system page
consumes one effective page. Every node verifies both classes.

Hashpower alone cannot create a block: the producer proves the transition
before nonce search. See [mining](mining/index.md), [parameters](protocol/parameters.md)
and [actual measurements](reference/performance.md).

## Issuance and State efficiency

The exact subsidy schedule is **16 → 11.30 → 8 → 5.65 → 4 → 2.83 → 2 → 1.41 → 1
KAI**, one step per 1,051,200 blocks from v2. The 1 KAI tail continues
indefinitely. Occupancy independently prices new live slots; the growth fee is
burned and consolidation avoids it. [Network economics](protocol/economics.md)
explains the constants and why State usage no longer acts as the monetary clock.

## Proof stack and security

Poseidon2b and binary-field arithmetic connect wallet authorization, exact
State and recursive ancestry. [FROST-GKR](research/frost-gkr.md) batches public
hash work; the transparent stack requires no trusted setup.
The final v2 bank and retirement proof have source-linked Category 1 resource
accounting under explicit composition, cryptographic and preprocessing
premises. Read the [security model](protocol/security-model.md) for the exact
statement, assumptions and resource bounds.

## Start

- [Install the native wallet](getting-started/wallet.md).
- [Create a contract or open a shared file](contracts/gui.md).
- [Run Core](getting-started/core.md) or [mine](mining/index.md).
- [Integrate with JSON-RPC](reference/rpc.md).
- [Build from source](developers/build.md) and inspect the pinned proof material.

The source defines consensus behavior. The documentation explains those rules
and the application workflows built on them.
