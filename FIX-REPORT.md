# FIX-REPORT — quilt-engine v0.3.1: closing the CC1 audit findings

Branch `v031-audit-fixes` off `main @ 91a1b41` (v0.3 + CC1-AUDIT.md).
Governing text: `DESIGN-V03-contract-versioning.md`; findings:
`CC1-AUDIT.md` F1–F6. Suite: **96 tests, OK** (85 existing kept
passing + 11 new in `tests/test_audit_fixes.py`), stdlib only,
`python3 -m unittest discover -s tests`.

A note on the probes: CC1-AUDIT.md says the evidence was
`cc1_probe.py` "repo root, scratch file", but only `CC1-AUDIT.md` was
committed (91a1b41 touches one file) and the probe is not in the tree.
The regression tests were reconstructed from the audit's P1–P6
descriptions and the line references in its findings.

| finding | design § | fix | test |
|---|---|---|---|
| F2 concurrent forks self-resolve invisibly | §5.2 (vs §5.1) | `adopt_revision` refuses on an outstanding cross-proposer pending claim (`PendingSiblingError`); `receive_proposal` books a §5.2 stand-off (peer's proposal held pending, answered `position="proposed"`); `receive_ack` never raises — refusals are booked, stand-offs complete nothing; fold surfaces forks among *pending* claims | `test_audit_f2_concurrent_proposals_do_not_self_resolve`, `test_audit_f2_receive_ack_books_refusal_instead_of_raising` |
| fold fork fallback to base | §5.2 | `contract_state` falls back to the **common predecessor** of the fork's tips (walked back to the nearest live ancestor; the pinned base is the floor) | `test_audit_f2_fork_falls_back_to_common_predecessor` |
| F1 refused versions govern | §2.4, §3.1 | `contract_state` exposes `refused`; `resolve_citation` refuses citations of versions this book refused (`citation_refusal="refused"`, divergence) | `test_audit_f1_refused_version_never_governs` |
| F1 pending honouring silent | §2.4 (deviation 5) | a held-but-pending citation is still honoured under the cited version but now carries `divergence=True` — flagged, never silent | `test_audit_f1_pending_citation_honoured_but_flagged` |
| F3 state not per seam | §2.2, §5.5, §4.1 | `RevisionEntry.peer` (seam counterparty, stamped by the crossing path); `contract_state`/`seam_context`/`current_version`/`version_report` scope by `contract_id` (strict) and `peer` (lenient); crossing paths and the demo pass the family | `test_audit_f3_unrelated_family_does_not_fork_the_seam` |
| F3 ack from anyone | §2.2 | `receive_ack` matches the ack's peer against the SEND entry of the proposal the pending record names; a mismatch books the crossing, completes nothing, logs `ack_from_non_party` | `test_audit_f3_ack_from_non_party_completes_nothing` |
| F3 standing rule unbuilt | §2.2/§5.5 | `receive_proposal(..., parties=...)`: a proposal delivered by a peer outside the seam's party set is refused — "no standing" | `test_audit_f3_proposal_from_non_party_refused` |
| F4 revocation drops `supersedes` | §3.1, §5.4 | `revoke_version` preserves `supersedes` (and label/seam peer) from the version's prior record, so the chain walk through a revoked middle link keeps the root superseded and its citations flagged | `test_audit_f4_revocation_preserves_the_chain` |
| F5 re-adoption erases revocation | §5.4 (scar) | `adopt_revision` refuses a revoked hash ("a re-proposal after a revocation is a new statement, not a restoration"); the fold counts a version with *any* revocation record as revoked, so even a rogue re-adoption record cannot erase the scar | `test_audit_f5_revocation_not_erasable_by_readoption` |
| F6 revocation unilateral | §5.4 | `send_revocation` / `receive_revocation` book the `contract/revocation` crossing and the revocation record on **both** books; `revoke_version` docstring marked local-only with the bilateral path named as the seam-level API; tests route through the bilateral path | `test_audit_f6_revocation_is_bilateral` |

## F2 in detail — what the stand-off is

The v0.3 behaviour resolved a concurrent fork by order of arrival: each
side's `receive_proposal(adopt=True)` adopted the *peer's* proposal and
implicitly refused its own pending one (§5.1's rule applied
unconditionally), the books ended on different v2s, neither fold showed
a fork, and `receive_ack` raised `ValueError` *after* booking the ack's
RECEIVE — a half-booked book with the exception in flight.

v0.3.1 implements §5.2 as the specific rule for concurrent proposals:

- **`adopt_revision` blocks on an outstanding cross-proposer claim.**
  A pending record with the same `supersedes`, a different version, and
  a *different proposer* than the version being adopted raises
  `PendingSiblingError` (a `ValueError`, so direct callers keep their
  `assertRaises`). Nothing is booked.
- **`receive_proposal` books the stand-off.** On that error the peer's
  proposal is booked `position="proposed"` (received, held, no position
  taken — it is a live claim on the slot) and the ack answers
  `position="proposed"` with the §5.2 reason: the conflict notification
  is itself a crossing (§5.2, resolution route 1's trigger). §5.1's
  "the peer must answer" is honoured — the answer is *not yet*, said
  out loud, not a refusal that would kill a proposal its own book
  still holds pending.
- **`receive_ack` completes nothing on a stand-off answer** and never
  lets an exception escape: a fork/scar `ValueError` from the adoption
  attempt is caught and *booked* as this book's refusal (the RECEIVE
  is already booked — the record is the only honest place for the
  refusal); a concurrent block logs the stand-off and leaves the
  proposal pending.
- **The fold is fork-aware over pending claims.** Live claims are
  adopted versions *and* pending proposals; more than one claim tip is
  a fork, `fork` lists the tips, and `current` falls back to the
  **common predecessor** of the tips — not the base (an
  `H1 ← H2 ← {H3a, H3b}` fork stays on H2). Supersession and `current`
  remain adopted-only: a pending proposal never advances `current`
  (§2.4) and never flags citations as superseded (§5.1: pending
  proposals do not block traffic).

Probed end state (the regression test asserts all of it): neither book
holds any adoption; both books' folds show `fork = [H2a, H2b]`,
`pending = [H2a, H2b]`, `current = H1`; ordinary crossings under H1
continue to agree; both books log `concurrent_proposal_standoff`.

## The §5.1/§5.2 conflict — flagged for the author

The design's own texts conflict, exactly as the brief says:

- **§5.1:** "One adoption refuses the competing pending proposals.
  Adopting a successor of P implicitly refuses any pending proposal
  that proposes a *different* successor of P."
- **§5.2:** "Neither side can adopt while the other's proposal is
  outstanding, because adopting either forks against the other's
  *pending* claim on the same predecessor."

Applied to a pending sibling, §5.1 says *adopt and retire it*; §5.2
says *do not adopt at all*. v0.3 resolved the conflict silently in
§5.1's favour — which is the mechanism behind F2. v0.3.1 implements
§5.2 (the specific rule for concurrent proposals, per the build brief)
and keeps §5.1 for the case it actually describes: **same-proposer**
competing claims (a proposer that left two live claims, or a receiver
holding two proposals from one peer — adopting one retires the other
with reason "superseded by the adoption of …"). The discriminator is
`RevisionEntry.proposed_by` on the pending record versus the version
being adopted. This is a reconciliation the design does not make; the
author should either bless it or reword §5.1 to scope its implicit
refusal to same-proposer claims.

## F1 in detail — and where the §8.1 gate now sits

`resolve_citation` is now the §3.1 table plus the two rows the v0.3
build was missing:

- **refused by this book** → refuse, books the receiver's current,
  `citation_refusal="refused"`, divergence (§2.4: the governing version
  is the intersection of what both books hold; a refused version is
  not in it — a refusal must bind the refuser's crossing judgements,
  not just its adoption records);
- **held but pending** → still honoured under the cited version
  (deviation 5's generalisation kept), but `divergence=True` — never
  silent.

Ordering note: a version with both a refusal and a revocation on
record reads as *revoked* — revocation is the stronger, final act.

**§8.1 gate location (no policy built).** With `resolve_citation`
fixed, a "consent required" ruling has one place to land per side: the
**held-but-not-adopted branch of `resolve_citation`** — before
honouring a version this book never adopted, require recorded owner
consent on that version's records (e.g. the latest record's
`owner_consent`), else refuse. A consent ruling that must also gate
*adoption itself* lands symmetrically in `adopt_revision`, on the
adopting record. What remains unbuilt, unchanged from the audit: the
consent *field* is still carried only by the direct-write API — the
crossing path (`send_proposal` / `receive_proposal` / `receive_ack`)
does not transmit or stamp it, so a consent ruling would also need a
payload key and three stamps. Nothing here decides that; the seam is
named, not built.

## F3 in detail — what "per seam" means now, and its honest limit

- `RevisionEntry` gains `peer` (the seam counterparty; stamped by every
  crossing-path write, `None` on direct-write records).
- The fold (`contract_state`, and everything above it) scopes by
  `contract_id` **strictly** — an unrelated family's tips can never
  fork this seam's fold (P3's false fork is gone) — and by `peer` when
  records carry one. Peer scoping is **lenient**: unstamped records
  (direct-write API, pre-v0.3.1 ledgers) count on every seam of their
  family. Full per-seam isolation therefore holds for records written
  through the crossing path; a direct-write record is seam-anonymous by
  construction. That is the honest limit without a seam registry,
  which the design does not give us.
- `receive_ack` finds the pending record scoped by version *and*
  contract family (the audit's "scans for any proposed record" wart is
  closed as a side effect), then matches the ack's peer against the
  SEND entry of the proposal that pending record names — this book's
  own sovereign record of who it proposed to. Mismatch: crossing
  booked, `ack_from_non_party` logged, nothing completes; the genuine
  ack still completes afterwards.
- The §2.2/§5.5 standing rule is exposed as
  `receive_proposal(..., parties={...})`, opt-in by the seam host that
  knows its peers: a proposal delivered from outside the set is refused
  with "no standing: … not a party to the … seam". The core cannot
  derive party-ness from a book alone (a fresh book's first crossing
  may legitimately be a proposal), so the default is `None` = no
  check. That is a seam-level API, not a policy — the party set is the
  caller's statement of its seam, not a ruling.

## F4/F5/F6 in detail

- **F4.** `revoke_version` preserves `supersedes`/label/peer from the
  version's prior record. Revoking H2 in `H1 ← H2 ← H3` now leaves
  `superseded = {H1, H2}`, `current = H3`, H1 citations honoured and
  flagged, H2 citations refused as revoked.
- **F5.** Refusal implemented (not just surfaced):
  `adopt_revision` raises for a hash with any revocation record, citing
  §5.4's "a re-proposal after a revocation is a new statement, not a
  restoration". The fold's `revoked` set is any-record (the scar), so a
  rogue re-adoption record booked directly cannot hide it, and the
  scarred version is excluded from the fork check (a dead claim cannot
  block its fixed successor, which supersedes the same predecessor).
  Note the asymmetry with *refusal*: a refused-then-adopted version
  still governs again (latest position wins) — refusal is an answer
  (§5.1), revocation is the scar (§5.4). If the author wants refusals
  to scar too, that is a ruling to make, not a default to take.
- **F6.** `send_revocation` (SEND + the revoker's record) and
  `receive_revocation` (RECEIVE under the crossing's own citation — the
  version about to be withdrawn — + the peer's record, + a
  `revocation_received` event). Both books fall back to the nearest
  live ancestor on their own folds, together. `revoke_version` keeps
  working as the local write primitive both compose, and its docstring
  now says plainly that calling it directly is the unilateral act §5.4
  refuses, naming the bilateral path as the seam-level API. The
  regression test exercises the full bilateral path; the two-process
  demo does not grow a revocation leg (see below).

## Things CC1 missed (or got slightly wrong)

1. **`cc1_probe.py` is not in the repo.** The audit's housekeeping says
   it wrote the probe at repo root; the audit commit contains only
   `CC1-AUDIT.md`. Anyone re-running the audit's evidence has to
   reconstruct it — the 11 regression tests are that reconstruction,
   adapted and made asserting.
2. **The demo never exercises revocation at all.** The audit's F6 says
   "Tests *and the demo* use `revoke_version`"; in fact only the tests
   do — `examples/two_process_demo.py` contains no revocation, so §5.4
   had no demonstration anywhere. v0.3.1 demonstrates it bilaterally in
   `tests/test_audit_fixes.py`; extending the demo's versioning
   scenario (a V7 revocation leg) was deliberately not done — it would
   change the scenario's end state (both folds back on H1) and drag the
   demo's acceptance test with it, which is author-facing churn beyond
   the audit's findings. Recommended as a follow-up.
3. **The fork check in `adopt_revision` was family-unscoped too**, not
   just the fold: two families' records sharing a `supersedes` string
   (e.g. both pinning the same base statement) would spuriously refuse
   a legitimate adoption. Fixed by the same scoping as the fold (F3).
4. **`receive_ack`'s pending scan was version-only, across families** —
   an ack about family X could complete a pending proposal of family Y
   with the same version hash. Scoped by family + peer-matched now
   (F3).
5. **The old superseded walk added pointer targets with no record of
   their own** (the pinned base must land in `superseded`). Not a bug —
   an existing test depends on it — but it is the kind of quiet
   invariant the fold rewrite could have broken; it is preserved
   explicitly (`_ancestors` follows pointers past unrecorded versions,
   `_chain_of` stops at them — each walk uses the semantics its caller
   needs).

## Deliberately not changed

- **Skewed vs unagreed for refused citations (§3.1's table vs the
  build).** The design's table marks revoked/unknown citations
  "unagreed"; the build categories them "skewed". V03-REPORT discloses
  this as deviation 2 via a build brief not in the repo. Left as is —
  changing the category is a reporting-semantics decision for the
  author, and the audit's spot-check accepted the disclosure.
- **§8.2 arbitration.** The fork is now loud everywhere (folds, events,
  stand-off answers) and still not resolved by anything — the four
  §5.2 routes remain open, no default picked.
- **§8.6 cause/descendants.** `reason` stays optional; revoking an
  ancestor still does not void descendants (the fold now simply walks
  the preserved chain correctly).
- **`owner_consent`** is still recorded verbatim and enforced nowhere.
