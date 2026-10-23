# tests

Acceptance tests for the v0 build are listed in ../BUILD-BRIEF.md.
They test against beam-quilt SPEC.md §§1-3 and §5 — comparative pick, one ending per transmission, disagreement records, visible seams.

Run (stdlib only): `python3 -m unittest discover -s tests` from the repo root.
`test_envelope.py` — envelope/T2/COPY/P2/manifest. `test_receiver.py` —
comparative pick, deadband timing (injected clock), disagreement.
`test_pair.py` — both-sides booking, agreement, C2 seam log, refusal
return, GC. `test_demo.py` — the two-process demo end-to-end plus
kill-mid-run and external-halt tests (these spawn real processes and
take ~15 s in total). `test_projection.py` — v0.1: trend derivation,
the horizon cap and its refusals, the fog scenarios (a stale reading
corrected by its own wake, in both directions), and the seam-log
audit of projections used and refused. `test_payload.py` — v0.2: the
two-phase payload transfer (offer/settle), the dispute path, the
settled/open/disputed folds. `test_versioning.py` — v0.3: derived
contract versions, revision-as-crossing with adoption across sovereign
timing, superseded/revoked/unknown citations, the skewed agreement
category, and per-book folds that never re-fold history (see
../DESIGN-V03-contract-versioning.md).
