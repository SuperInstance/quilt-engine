# CC1 AUDIT — quilt-engine v0.3 (a54bb51): were any §8 rulings smuggled in?

Referee: CC1 (independent, read-only on source). Subject: `main @ a54bb51`.
Governing texts read: `DESIGN-V03-contract-versioning.md` (all of §2–§8 and
the appendices), `V03-REPORT.md`, `BUILD-BRIEF.md`. **SPEC.md is not in this
repo.** README.md:15 cites beam-quilt's SPEC.md (GitHub), and there is no local
checkout on this machine. I audited against the design note, which quotes the
SPEC clauses it relies on (P5, C1, §3.4). The v0.3 *build brief* the report
cites for deviation 2 is not in the repo either, so that deviation's
justification could not be checked.

Evidence: `cc1_probe.py` (repo root, scratch file, imports the package and
never modifies it). Run it with `python3 cc1_probe.py`. Probe output is quoted
below as P1–P6.

## Summary

| §8 | question | verdict |
|---|---|---|
| 1 | owner consent vs bilateral adoption | **UNCLEAR**: inert at the claimed seam, but the seam isn't enough to hold a "yes" ruling (see F1) |
| 2 | who arbitrates a genuine dispute | **SMUGGLED**: a concurrent fork resolves itself by order of arrival, and the fork never shows (F2); a refused version still governs crossings (F1) |
| 3 | label in identity | **CLEAN** |
| 4 | how much is revisable | **CLEAN**, with the permissive default in force |
| 5 | 3+ parties | **SMUGGLED (latent)**: revision state is per book, not per seam, and adoption completes on an ack from any one peer (F3) |
| 6 | revocation cause / descendants | **UNCLEAR**: the descendant half follows the design's own §5.4. But the fold lets a plain re-adoption erase a revocation (F5), and revocation is unilateral in code (F6) |

Suite (my run): **85 tests, OK**, exit 0, 44.4 s
(`python3 -m unittest discover -s tests -v`). The count and result match the
report. But four of the defects below (F1–F4) pass a green suite, because no
test covers concurrent proposals, refused-then-cited versions, multi-family or
multi-peer books, or revoking a middle link.

---

## Per-question verdicts

### §8.1 Owner consent: UNCLEAR

What the report claims holds: `RevisionEntry.owner_consent`
(`quilt_engine/core/state.py:271`) is a free `str | None`. It is recorded by
`propose_revision` / `adopt_revision` (`core/versioning.py:256,268,277,315`)
and nothing ever reads it. P6 confirms `propose_revision` stores it verbatim.

Problems:

1. **The seam is in the wrong place, or only part of it is.** The report puts
   a "yes" ruling at `receive_proposal`'s `adopt` policy point. But
   `resolve_citation` (`versioning.py:550-553`) honours *any* held, non-revoked
   version, including one this book never adopted (F1). So a version that never
   got owner consent can still govern crossings without going through the
   adoption step. A consent ruling would also need a gate in `resolve_citation`.
2. **The field is wired only on the direct-call API, not on the crossing
   path.** The proposal payload has no consent key (P6: `['contract_id',
   'label', 'statement', 'supersedes', 'version']`), and none of these accept
   or carry it: `send_proposal` (`:393-411`), `receive_proposal` (`:440-445`),
   `receive_ack` (`:483-488`; it does not copy `pending.owner_consent`),
   `refuse_revision`, `withdraw_revision`, and the implicit refusal in
   `adopt_revision` (`:324-335`). "Recorded verbatim" is true only if you skip
   the cycle the report describes.
3. **A mild lean in the shape.** §8.1 says *"If yes, the revision record gains
   an owner-consent field."* That field already exists, typed as a string
   attribute, which assumes consent is an annotation rather than a booked act.
   It's inert, so this is a lean, not a ruling.

What runs today is bilateral-only adoption (`receive_proposal(adopt=True)` by
default). That is the design's core, not the builder's choice. **What would
settle it:** decide whether honouring a citation requires this book to have
*adopted* the version (F1). If it does, the claimed seam is enough.

### §8.2 Dispute arbitration: SMUGGLED

The report claims detection only: `adopt_revision` refuses forks and
`contract_state` surfaces `fork`. The mechanism doesn't behave that way:

**F2: a concurrent fork resolves itself by order of arrival and stays hidden
(P1).** A proposes H2a and B proposes H2b, both superseding H1. Each side's
`receive_proposal` default-adopts the *peer's* proposal. Three things combine:

- the fork check looks only at *adopted* siblings (`versioning.py:296-305`);
- adoption implicitly refuses the book's own pending sibling (`:318-335`);
- adoption is the default (`adopt=True`, `:416`).

Result:

```
A's position on B's H2b: adopted | B's position on A's H2a: adopted
A: current=5e2c3321 fork=[] (H2b)
B: current=994767d6 fork=[] (H2a)
A receive_ack RAISED ValueError: fork refused: … already superseded by 5e2c…
B receive_ack RAISED ValueError: fork refused: … already superseded by 9947…
A->B crossing citing 5e2c3321 … superseded_citation=False … crossing AGREED
```

§5.2 says *"neither wins. The seam stays on H1 and the fork is recorded
loudly"*, and *"Neither side can adopt while the other's proposal is
outstanding."* The build does the opposite:

- each side ends up on a different v2;
- neither book's fold shows a fork;
- later crossings count as agreed and unflagged.

Of the three dispute outcomes §8.2 lists, "nobody arbitrates; the fork persists
as visible damage" comes closest, but here the damage isn't visible. In
practice, order of arrival is the arbitrator. Also, `receive_ack`
(`:482-488`) has no handler around `adopt_revision`, so the fork exception
escapes *after* the ack's RECEIVE is already booked (`:473`). The book is left
half-booked: RECEIVE recorded, no position record. That's a correctness bug in
its own right.

**F1: a refused version still governs crossings (P2).** B refuses H2a with the
reason "disputed". B retains the statement, so `resolve_citation` applies a
later citation of H2a: `action='apply', applied_version=H2a,
superseded_citation=False, divergence=False`. The crossing is agreed and
unflagged. This comes from deviation 5 ("honour any held, non-revoked
citation"). The report presents it as a generalisation for *pending*
proposals, but it reaches *refused* ones too. In practice, refusing a revision
has no effect on the proposer's crossings, which answers part of §8.2: the
proposer's version governs what it sends, whatever the dispute. §2.4 says the
governing version is "the intersection", and a refused version isn't in it.

**Fix shape (not a ruling):**

- treat a held version this book has refused as a refusal in
  `resolve_citation`, or at least as a flagged divergence;
- make `adopt_revision` refuse when a *pending* sibling exists, per §5.2;
- catch the fork in `receive_ack` and book a refusal there.

### §8.3 Label in identity: CLEAN

- `statement_version` hashes only the canonical statement
  (`core/contract.py:34-56`).
- `AlgorithmContract.identity` is `name@version` with no label
  (`contract.py:88-94`).
- The pinned `seams/handshake_v1.json` has no version label in it. The
  demo's v2 statement (`examples/two_process_demo.py:175-182`) changes only
  `pool_rule` and carries no label.
- The folds key on `version` and never read `label`.

The design's "label collision is a fork tripwire" isn't built. Leaving it out
is the more neutral choice. Hash-only identity is the design's stated stance,
kept as such, and the docstring says so (`contract.py:12-15`).

### §8.4 Revisability of settlement rules: CLEAN (permissive default in force)

- Statements are opaque. Nothing separates settlement clauses, and any
  well-formed revision is accepted. A "settlement rules are immutable" ruling
  needs either structure in the statement or a validity hook, which is where
  the report says it would land.
- "Settlements stand" falls out of the append-only ledger plus a fold that
  never re-folds. That's the design's own §4.2/§4.3 rule, not a separate
  ruling.
- `version_report` (`versioning.py:620-655`) only *describes* the regimes.
- The demo's v2 changes the pool tie-break, not the settlement rule, so
  nothing shows a settlement-rule revision as working behaviour.
- SETTLE entries are stamped with the seam's current version
  (`two_process_demo.py:304`).

### §8.5 Three or more parties: SMUGGLED (latent)

The report claims adoption is pairwise and that "no N-party shape exists to
remove". At the API surface that's true. But once a book has more than one
peer, its behaviour is already defined, and it's one particular answer:

- **`RevisionEntry` has no peer or seam field, and `contract_state`
  (`:558-604`) folds every revision in the book.** It doesn't filter by peer,
  and it doesn't even filter by `contract_id`.
  - Adopting H2 with peer B moves this book's current for its seam with peer
    C too.
  - P3: adopting a revision of an *unrelated* family creates a false
    `fork = ['994767d6', 'c4f156ec']` and drops the seam back to H1. That is
    a plain bug, ruling or not.
- **`receive_ack` (`:459-496`) accepts an ack from any peer.** It never checks
  that the ack's peer is the one the proposal went to (P6: A adopts on an ack
  attributed to "C-not-a-party"). One ack from anyone completes adoption:
  a quorum of one.

Together, the rule actually in force is: "one bilateral cycle with any peer
binds this book's view of the contract for all its seams." **What would make it
neutral:** scope revision records and the fold by `(contract_id, seam/peer)`,
and match the ack's peer against the proposal's SEND peer. Then the "pairwise
only" claim holds structurally, not just because the demo has two processes.
The §2.2/§5.5 standing rule (proposals from non-parties are refused) also isn't
implemented anywhere, and the report doesn't say so.

### §8.6 Revocation cause / descendants: UNCLEAR

- **Cause.** `revoke_version(reason=None)` (`:372-388`) and
  `make_revocation(reason=None)` (`:132-148`) make the cause optional and
  record it. That's the least committed choice. A "mandatory" ruling adds one
  validation and turns existing `None` records into legacy records. Neutral.
- **Descendants.** "Revoking H2 leaves H3 current" holds (P4: current = H3).
  This follows design §5.4 (*"Rollback is version-local. Revoking H2 does not
  void H3"*), so it's the design's default, not the builder's ruling. It still
  answers half of §8.6 in running code, and the author should know it does.
- **F5: re-adopting a revoked version erases the revocation from the fold
  (P5).** `contract_state` reads the *latest record per version*
  (`:571-575`). An `adopt_revision` of the same hash after a revocation gives
  `current = H2a, revoked = []`. The revocation stays in the JSONL, but the
  fold and `version_report`'s `revoked` flags lose it. That's a functional
  answer to the scar question §8.6 raises ("a failure record survives a rewind
  and must be addressed"). It also contradicts §5.4, *"A re-proposal after a
  revocation is a new statement, not a restoration."* `adopt_revision` doesn't
  check for a prior revocation.
- **F4: a revocation record drops `supersedes` and breaks the chain walk
  (P4).** In H1 ← H2a ← H3, revoking H2a books a record with
  `supersedes=None` (`:384-388`), and it becomes `latest[H2a]`. The walk from
  H3 then stops at H2a, so `superseded = [H2a]` and **H1 is no longer
  superseded**. An H1 citation is applied with `superseded_citation=False`
  while H3 is current. That breaks §3.1's flag.

**What would settle it:** a ruling, or at least a stated design default, on
whether revocation is final in the fold. Plus fixing F4 so that "descendants
stand" doesn't silently change the superseded set.

---

## Spot-checks of V03-REPORT claims

| claim | result |
|---|---|
| **Version is a derived hash; hand-typed versions are unrepresentable** | **Holds for `AlgorithmContract`.** `version` is a property over `statement_version(statement)` (`contract.py:88-90`). H1 = `7f8c6f77…`, matching the report. `StateStore.book_revision` refuses records whose statement doesn't hash to their version (`state.py:429-435`). `receive_proposal` checks this too (`versioning.py:436`). **Overstated elsewhere:** `RevisionEntry.version` with no statement (every revocation), `LedgerEntry.contract_version`, and `PatchManifest.contract_version` are plain typed strings with no check. |
| **Three-booking adoption cycle; governs only when both books hold it** | **Holds on the happy path** (`send_proposal` → `receive_proposal` → ack → `receive_ack`; `test_versioning.full_adoption_cycle`). **Fails** under concurrent proposals (F2: crossed adoption plus an exception escaping) and doesn't check who sent the ack (F3). |
| **Superseded citations honoured under the cited version and flagged; never re-judged** | **Holds for a linear chain with no revocations** (`resolve_citation` `:539-553`, demo V3/V5). Never re-judged: confirmed, since nothing re-stamps an earlier entry. **Breaks** after revoking a middle link (F4: no flag). It is also **broader than reported**: refused versions get honoured too (F1). |
| **Skewed is a distinct category** | **Holds.** `agreed_crossings` (`state.py:492-537`) keys on `(tid, hash, version, side)`. It returns `skewed` when a SEND/RECEIVE pairing exists on the hash but every pairing names a different version, and `unagreed` when there's no pairing. Note that §3.1's table marks revoked and unknown citations as "unagreed", while the build calls them "skewed". The report discloses this (deviation 2), citing a build brief that isn't in the repo. |
| **The 60 existing tests are unchanged in behaviour** | **Holds.** The diff from 1336adf to a54bb51 in the old tests is four 3-tuple unpack sites (each adds `assertEqual(skewed, [])`) and two `AlgorithmContract` constructor sites. The `echo@1` identity assertion became `echo@<sha256>`, which is the change §6.4 sanctions. |
| **85 tests green** | **Holds** (my run, above). |

## Other things a referee should flag

- **F6: revocation is unilateral in code.** §5.4: *"A revocation is a
  bilateral act … It is not a unilateral local edit."* `make_revocation`
  builds the envelope, but nothing calls it outside the package exports. There
  is no `receive_revocation`. Tests and the demo use `revoke_version`, which
  is a local book write. The report doesn't list this as a deviation. It
  isn't a §8 question, but the §8.6 seam sits on top of it.
- **Two design statements conflict, and the builder resolved it silently.**
  §5.1 says one adoption implicitly refuses competing pending proposals. §5.2
  says pending siblings block adoption. The build picked §5.1 and didn't
  record that as a deviation. That choice is what causes F2.
- **When the fold finds a fork, it falls back to the base, not the common
  predecessor.** `contract_state` sets `current = base_version` (`:592-595`)
  whenever there is more than one tip. §5.2 says the seam stays on the
  predecessor (H1 in its example). For a fork at H2 → {H3a, H3b}, the code
  drops to H1, not H2. Two tips from different families (P3) trigger the same
  fallback.
- **`receive_ack` scans for *any* `proposed` record, not the latest per
  version** (`:475-479`). That's how it finds an "already refused" proposal
  in F2 and then raises.
- **Builder honesty.** The report's six deviations are real and accurately
  described as far as they go. The problems are omissions and side effects
  between features, not misstatements. Deviation 5's reach (refused versions,
  not only pending ones) is understated.

## Housekeeping

- No source file modified. My writes: `CC1-AUDIT.md`, `cc1_probe.py` (scratch,
  repo root). I removed the `__pycache__/` directories my test runs created.
- Nothing committed or pushed.
