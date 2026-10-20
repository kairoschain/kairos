# Contributing to Kairos

Bug reports and focused contributions are welcome. Kairos prioritizes
protocol correctness, security, and performance.

**Agree on the task first, then submit a pull request with a verifiable benefit.**

Report security vulnerabilities privately according to
[SECURITY.md](.github/SECURITY.md), never through a public issue or pull
request.

## Agree on the task

For non-security work, open an issue or join an existing one before starting
implementation. Describe the problem, the proposed approach, and how the
benefit can be verified. Wait for a maintainer to agree on the task and its
scope before opening a pull request. Opening an issue alone does not mean
the work has been accepted.

Cosmetic changes and bulk lint-only pull requests submitted without prior
agreement will be closed as not planned. Formatting, lint, and refactoring
work must address a specific agreed problem. Keep the implementation within
the agreed scope.

## Pull requests

Open contributor pull requests against `dev` and base new work on its latest
commit. This includes mainnet fixes and maintenance changes. Maintainers merge
reviewed changes from `dev` into `main` for releases.

Link the issue where the task and scope were agreed. Security fixes must
follow the private reporting and triage process in
[SECURITY.md](.github/SECURITY.md) before any public pull request is opened.

Keep each pull request limited to one logical change and describe:

- the problem and the chosen solution;
- the verifiable benefit, with a reproducer, test result, benchmark, or other
  evidence appropriate to the change;
- any consensus, proof, wire, storage, wallet, or network impact;
- the tests or live scenarios used to verify it;
- any user-facing documentation that changed.

Run `cargo fmt --all -- --check` and the focused tests for every affected
crate. Do not commit wallet data, node data, generated matrix packs, build
artifacts, credentials, or private logs.

By submitting a contribution, you agree that it is licensed under the
[Apache License 2.0](LICENSE).
