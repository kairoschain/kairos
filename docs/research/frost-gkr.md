# FROST-GKR

**FROST-GKR is research by Kairos Lab: a global committed-trace protocol for
batched Poseidon2b over `GF(2^128)`.**

The name expands to **Frobenius Reduction over Shifted Tables**. The protocol
turns an entire batch of width-four Poseidon2b executions into openings of
three committed multilinear polynomials.

[Read the paper](https://lab.kairoschain.org/papers/FROST_GKR.pdf) ·
[Open the Kairos Lab research article](https://lab.kairoschain.org/research/frost-gkr-global-trace-protocol/) ·
[Inspect the reference implementation](https://github.com/ignotusnemo/frost-gkr)

## The repeated-computation problem

Hash-based proof systems evaluate the same permutation many times. Inputs
change, but the round constants, S-box and linear maps do not. Treating each
execution as an independent circuit repeats columns and sumchecks that encode
the same structure.

FROST-GKR treats the batch as one object. Every permutation slot, round and
state lane becomes a coordinate of one Boolean product domain. The persistent
object is the committed execution trace; global relations are reduced directly
to openings of that commitment.

## One committed trace

For `B` live permutations, let `L = 2^s >= B` be the padded slot count.
Poseidon2b has four lanes and 66 nonlinear rounds. FROST-GKR reserves 128 round
positions, giving the Boolean domain

`(slot, round, lane) in {0,1}^s x {0,1}^7 x {0,1}^2`.

The domain has `n = s + 9` variables and `N = 2^n = 512L` cells. Its witness
contains only three columns:

| Column | Meaning |
|---|---|
| `z` | Poseidon2b state entering each round, including the terminal row |
| `s_in` | Input of every active `x^7` S-box |
| `s_out` | Output of every active `x^7` S-box |

The prover commits to these columns before any relation challenge is sampled.
Public selectors identify live slots, active rounds and active S-box lanes.

## Two structural reductions

One relation enforces the complete permutation batch:

| Stage | Rounds | Degree | Result |
|---|---:|---:|---|
| Commit | — | — | Fix the three witness polynomials |
| Global relation | `n` | 9 | Reduce every live round equation to 12 evaluations |
| Shift reduction | `n` | 2 | Return 11 derived-view claims to the three committed columns |
| Generic terminal batching | `3n` | 2 | Produce one opening claim per committed column |

The global zero-check covers the S-box relation, round constants, full and
partial linear layers, and round adjacency. Its terminal contains evaluations
of the original columns together with round-shifted and lane-projected views.

Binary round increment is not an affine transformation of multilinear
variables. FROST-GKR therefore proves those shifted views explicitly. One
degree-two sumcheck batches all 11 derived evaluations and returns them to
direct claims on `S_in`, `S_out` and `Z` at a new point.

After both reductions only four point-value claims remain: two on `Z` and one
on each S-box column. A commitment scheme with native multi-point openings can
open them directly; the paper also specifies a generic three-column terminal
batching layer.

## Why the binary field matters

The protocol is instantiated over `GF(2^128)`. In characteristic two,
Frobenius squaring is linear and

`x^7 = x^4 * x^3`, with `x^3 = x^2 * x`.

An implementation can therefore evaluate the direct S-box with two general
field multiplications and specialized squarings. This is an implementation
advantage; the formal sumcheck degree remains seven, and the masked global
relation has individual degree nine.

## Endpoint composition

The `z` column exposes the input and output row of every permutation slot.
Applications constrain those endpoints against the same commitment used by
the internal relation. Linear endpoint equations can express:

- independent hash calls;
- sequential permutation chains;
- Merkle trees;
- sponge constructions;
- state-transition graphs.

This separation is the integration interface. FROST-GKR certifies every
Poseidon2b execution once across the batch; the endpoint relation connects the
certified slots into the application graph. Evaluation binding prevents the
two relations from using different traces.

## Soundness and cost

For an evaluation-binding multilinear commitment and independently sampled
interactive challenges, the algebraic soundness error of the generic protocol
is bounded by

$$
\varepsilon_{\mathrm{alg}} \le \frac{18n+14}{\lvert\mathbb{F}\rvert}.
$$

For the paper's 15-variable instance over $\mathbb{F}=\mathrm{GF}(2^{128})$,
this is less than $2^{-119}$ before adding the soundness terms of the endpoint
relation and polynomial commitment. The paper gives the complete failure-event
ledger, including the main zero-check, shift reduction and terminal batching.

For fixed Poseidon2b width and round schedule, prover work is $O(N)$ field
operations and the committed witness contains $3N$ field elements. The two
structural constraint reductions use $2n$ sumcheck rounds. With full round
coefficient vectors and generic terminal batching, the algebraic transcript
contains $22n+18$ field elements before commitment openings and framing.

## Reference evaluation

The published artifact compares FROST-GKR with a preserved per-permutation
product-chain reduction on the same 59-permutation sequential workload, field,
native permutation and transcript channel.

| Metric | Product chain | FROST-GKR | Change |
|---|---:|---:|---:|
| Constraint sumchecks | 472 | 2 | 236.00x fewer |
| Constraint rounds | 4,248 | 30 | 141.60x fewer |
| All sumcheck rounds | 4,263 | 75 | 56.84x fewer |
| Algebraic transcript | 287,712 B | 5,568 B | 51.67x smaller |
| Median reduction prover | 1,605.931 ms | 150.218 ms | 10.69x faster |
| Median reduction verifier | 984.269 ms | 66.499 ms | 14.80x faster |

The timing run used an Intel Core i7-1365U, release-mode native CPU code, three
warm-ups and 20 interleaved samples. The transcript figures are uncompressed
field-element counts. Polynomial-commitment openings and serialization framing
are outside both columns, so the comparison isolates the reduction described
by the paper.

## Role in Kairos

The research originated in the proof system developed for Kairos. The
protocol supplies the batch-wide Poseidon2b reduction used inside the shared
binary proof stack. Application relations bind its committed endpoints to
wallet authorization, Merkle and State computations; the surrounding
FRI-Binius/BaseFold layer closes the resulting multilinear claims without a
trusted setup.

The committed trace and the FROST-GKR relation remain over `GF(2^128)`. The
production proof stack lifts Fiat-Shamir challenges, terminal claims and
recursive region authentication into `GF(2^256)`. This wider layer surrounds
the reduction; it does not change the three-column trace, the degree-nine
relation or the `GF(2^128)` benchmark reported in the paper.

FROST-GKR is therefore a reusable research result as well as a concrete part
of Kairos's proof architecture. It is research by **Kairos Lab**.
