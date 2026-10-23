# V03 REPORT — contract versioning, ruling-neutral core

Repo: `SuperInstance/quilt-engine`, branch `v03-versioning`, base `main @ 1336adf`.
Governing design: `DESIGN-V03-contract-versioning.md`. This build
implements its **ruling-neutral core only**: the parts whose shape does
not depend on the design's §8 open questions. Those questions stay
open — each has a named seam in the code (mapped below), and nothing
anywhere decides one.

## What was built

**1. Version as a derived hash (§1, §6.4).** A contract reference is
`(contract_id, contract_version)` where the version is the SHA-256 of
the contract's **canonical statement** — computed, never typed.
`statement_version` / `canonical_statement_bytes`
(`quilt_engine/core/contract.py`) pin the canonical form (canonical
JSON for data; UTF-8 + NFC + LF + no trailing whitespace for prose).
`AlgorithmContract` now takes `statement` (plus display-only `label`)
and *derives* `version`; a hand-typed version is unrepresentable. Same
statement → same version on both sides; any change → new version;
labels are outside the hashed bytes.

**2. The citation travels (§6.1, §6.2, §6.7).** `TransmissionEnvelope`
gains `contract_id` + `contract_version` (round trip and `refreshed()`
carry them; a v0.1 envelope still parses). `LedgerEntry` carries the
same pair: the sender books what it *cited*, the receiver books the
version *it applied* — a fact about each book, not a shared constant.
The v0.2 flat `contract` field (the payload's own governing id, e.g.
`route-plan@1`) is unchanged; the new pair is the **seam-contract**
citation. The seam's v1 statement is a pinned document,
`quilt_engine/seams/handshake_v1.json`, so the demo's H1 is a real
content hash (`7f8c6f…`) rather than the literal `1.2`. `JevPair` takes
a contract object (`SEAM_CONTRACT_V1`) and stamps both sides per §6.7;
`PatchManifest` declares `contract_id` + `contract_version` (§6.5), and
the example manifest cites the derived H1.

**3. Revision as a crossing (§2, §6.3).** New module
`quilt_engine/core/versioning.py`. A proposal travels as an ordinary
envelope (`contract/revision`) whose payload is self-sufficient:
`{contract_id, version, label, supersedes, statement}`; the proposal's
*own* citation is the version in force when it was made. Receipt is not
adoption: the peer books RECEIVE for the crossing **and** a position
record (`RevisionEntry`, a third JSONL kind in `StateStore`), then
answers with an adoption ack (`contract/adoption-ack`) that crosses
back; the proposer books its own adoption only on receiving it
(`send_proposal` / `receive_proposal` / `receive_ack`). The revision
governs when both books hold it. Forks are refused loudly
(`adopt_revision`, §5.2), and adopting one successor implicitly refuses
this book's competing pending proposals with the recorded reason (§5.1).

**4. Superseded citations (§2.5, §3.1).** `resolve_citation` is the
§3.1 table as a pure function over the receiver's own fold: the
receiver's current → apply; superseded but held, not revoked → **apply
under the cited version** and record `detail.superseded_citation`;
revoked → refuse, book the receiver's current; unknown → refuse, book
`contract_version = null`, and answer with a clarify/narrow **statement
request** (`make_statement_request` — reuse of the existing response
type). Refusals log a first-class `version_divergence` seam event;
nothing is ever re-judged under a version the crossing did not cite.

**5. Version-aware agreement (§6.6).** `agreed_crossings` keys on
`(transmission_id, payload_hash, contract_version, side)` and returns
**three** categories — agreed, **skewed** (id + hash match, versions
differ: the books disagree about what governed the crossing), unagreed.
A refused citation therefore surfaces as skewed, never as plain
unagreed, and is never averaged into an "effective version" (§3.4).
Four existing call sites were updated mechanically for the third slot.

**6. Per-book folds (§4).** `contract_state(revisions, base_version)`
folds one book into `{proposals_seen, adopted, revoked, superseded,
current, fork}` — `current` is a set operation over the `supersedes`
chain (no clock, no counter; revoked tip → nearest live ancestor,
version-local). `held_statements` retains every statement the book may
be asked to honour. `version_report` is §4.3's report: bookings and
settlements per version, which sit under since-superseded versions, and
the version in force now. Nothing re-folds history; correcting a
settlement would be a compensating entry (not built; the fold makes the
regimes visible instead).

**7. Demo (`examples/two_process_demo.py`).** New
`--scenario versioning` (also the last leg of `all`): a payload
**settled under v1** (V0, §4.3); a clean v1 crossing (V1); the
revision **proposed mid-run** (V2); a crossing **in flight across the
adoption boundary** — sent under H1, received after the receiver
adopted H2 — honoured under H1 on both sides, agreed, flagged, the skew
in the seam log (V3, the skewed-adoption case: receiver adopts first,
sender only when the ack returns); a clean v2 crossing (V4); a **stale
v1 citation** after both adopted, honoured and never upgraded (V5); a
citation of a statement that **never crossed** — refused, skewed
distinctly, divergence logged, statement requested back (V6). The
demo's verifier checks all of it over both books, including that the
v1 settlement stands after v2 lands.

## Test counts

**85 tests, all green** — the 60 existing tests pass unchanged in
behaviour (four unpack sites and two constructor sites adapted to the
sanctioned API changes) plus 25 new in `tests/test_versioning.py`
covering: version derivation (same statement → same version, any change
→ new, label outside the hash, prose normalisation, pinned-statement
hash); adoption requiring both books (the §2.3 sequence, folds during
the skewed window, refused peer with reason, fork refusal, competing
pending proposals, JSONL round trip); in-flight honouring; superseded
citation logged and not upgraded; unknown and revoked citations refused
(statement request; revocation version-local); skewed as a distinct
category (and v0.1 unversioned crossings still agreeing); v1
settlements standing after v2 with `version_report` marking them;
envelope/pair/manifest citation shapes; and the full two-process
versioning scenario end-to-end.

Suite tail (fresh run, `python3 -m unittest discover -s tests`):

```
test_scenario_exhibits_the_full_versioning_story (test_versioning.TestVersioningScenario.test_scenario_exhibits_the_full_versioning_story) ... ok

----------------------------------------------------------------------
Ran 85 tests in 44.267s

OK
```

Demo (`--scenario all`, i.e. proposals + payload + versioning) exits 0
and prints, among the seam events, `revision_received … position:
'adopted'`, the `superseded_citation` event for the in-flight and stale
crossings (`cited_version` H1, `receiver_current` H2), and
`version_divergence … 'statement not held'` for the never-crossed
citation; final lines `DEMO OK / PAYLOAD OK / VERSIONING OK`.

## Where each §8 open question's seam lives

| §8 | ruling needed | seam in the code |
|---|---|---|
| 1 | owner consent for a revision | `RevisionEntry.owner_consent` (`core/state.py`) — recorded verbatim by `propose_revision`/`adopt_revision` (`core/versioning.py`), enforced nowhere. If the ruling is "yes", the adoption cycle grows a step at `receive_proposal`'s `adopt` policy point. |
| 2 | who arbitrates a genuine fork | `adopt_revision` refuses to fork (raises `fork refused: P already superseded by V`); `contract_state` surfaces `"fork"` and keeps the seam on the base. Detection only — no resolution route is chosen; an arbitrator lands beside these two. |
| 3 | label in identity | `AlgorithmContract.label` and `RevisionEntry.label` are display-only; `statement_version` (`core/contract.py`) hashes the statement and nothing else. The design's hash-only stance is kept, not decided. |
| 4 | are settlement rules immutable | `version_report` reports settlement regimes; nothing re-folds, so "settlements stand" holds by construction. An immutability ruling would be a validity check at the `receive_proposal`/`adopt_revision` policy point (refuse revisions that touch settlement clauses). |
| 5 | multi-party contracts | Adoption is pairwise throughout (`receive_ack` completes on *one* ack). A quorum rule would replace that completion check; no N-party shape exists to remove. |
| 6 | revocation cause / descendants | `revoke_version(reason=None)` — cause optional, recorded; the chain walk in `contract_state` steps over a revoked link only at the tip, so descendants stand (revoking H2 leaves H3 current). Both halves of the question are one ruling away. |

## Deviations from the design as written (and why)

1. **`RevisionEntry` carries `statement`**, beyond §6.3's printed field
   list. §2.5's "statements are retained" cannot be implemented without
   it: ledger entries hold payload *hashes*, not payloads, so the
   receiver cannot recover H2's bytes from the proposal's booking.
   `statement_hash` (== `version`) keeps the binding checkable, and
   `StateStore.book_revision` refuses a record whose version is not the
   hash of its statement.
2. **`agreed_crossings` returns a 3-tuple** `(agreed, skewed,
   unagreed)`, against §6.8's "return shape does not change". The build
   brief explicitly requires mismatched-version bookings to surface as
   a distinct *skewed* category; the brief overrides the design's
   minimality note here. Four call sites adapted; assertions unchanged.
3. **The design was written against v0.1 @ 9752895; the base is v0.2.**
   The v0.2 envelope/ledger `contract` field (the payload-transfer
   governing id) coexists with the new citation pair rather than being
   replaced — §6.2's split applies to the *seam* citation, and rewriting
   the payload path's semantics was not in the design's field diff. The
   legacy flat `DEFAULT_CONTRACT_ID` ("…@1.2") remains as the unversioned
   path's default, documented as a label, not a hash (§6.2's legacy
   note) — this is what lets the 60 existing tests pass with their
   behaviour intact.
4. **§2.3's table names no wire type or citation for the adoption ack.**
   Chose `contract/adoption-ack`, citing the version the *proposal*
   cited — the receiver's best evidence of the bilateral intersection
   while the seam is still on v1 (§2.4). A naming choice, not a ruling.
5. **§3.1's table has no row for a held version that is neither
   current, revoked, nor an ancestor** (e.g. a pending proposal's
   version cited by a peer). Generalised the table's own principle:
   honour any held, non-revoked citation under the cited version;
   `superseded_citation` is set exactly when the cited version is in
   the fold's superseded set.
6. **`settleable_crossings` (v0.2) remains payload-hash-based**, as in
   v0.2 — it was not in the design's field-level diff. Observed
   interaction, not changed: a payload offer whose seam citations skew
   across the adoption boundary can still be *settleable* even though
   the offer crossing is not *agreed*. If that should gate, it is a
   one-line extension once the author rules.

Nothing in the design proved unimplementable; the six items above are
merges, namings, and one brief-mandated shape change, each recorded
where it lives in the code. Stdlib only; no new transport, store,
registry, or clock.
