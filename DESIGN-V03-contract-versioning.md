# Design — v0.3: contract versioning (gap 3, deepened)

**Design only.** No code changes here. This note is the design for the
next deepening of beam-quilt's gap 3 — *contract identity bound into each
ledger entry* — in the shape gap 3 actually needs when algorithms get
revised.

- **Repo:** `SuperInstance/quilt-engine` @ `9752895` (v0.1, 42 tests green)
- **Spec of record:** beam-quilt `SPEC.md` v1.2 (P1–P6, T1–T3, §3, G1–G3, C1–C3)
- **Doctrine:** beam-quilt `FOUNDATIONS.md` — "A contract is an algorithm: the
  agreed algorithm governing a crossing is itself an early ledger entry, an
  agreement about future sends and receives."

Confidence tags used below, as in `ARCHITECTURE.md`: **Settled** (normative
in the SPEC), **Proposed** (this note's design, buildable), **Speculative**
(do not build).

---

## 0. Where v0.1 actually stands — and the specific hole

v0.1 stamps every crossing with the governing contract's identity. That is
gap 3 in its smallest honest form and it is already in the code:

- `LedgerEntry.contract: str` — one flat string, e.g. `"beam-quilt/seam-handshake@1.2"`
  (`quilt_engine/core/state.py:176`).
- `AlgorithmContract` carries `name` and `version` with `identity` = `"name@version"`
  (`quilt_engine/core/contract.py:38-44`).
- `PatchManifest.contract: str` — the same flat string.
- `DEFAULT_CONTRACT_ID = "beam-quilt/seam-handshake@1.2"` in
  `quilt_engine/seams/pair.py:40`, stamped into every SEND and every RECEIVE.

Three observations, in the order they matter.

**1. The version is a label, not a claim.** `1.2` is a human-written string. It
is not derived from anything, so nothing can detect that two sides claiming
`1.2` mean different algorithms. Worse, the label currently *tracks the SPEC
version* — a SPEC revision is a different number, and the SPEC is not the
contract. The seam's contract and the seam's implementation of the SPEC are
not the same thing, and only the former is what the ledger should bind.

**2. The two books "agree" on the contract by construction, not by agreement.**
Both processes in `examples/two_process_demo.py` import the same
`DEFAULT_CONTRACT_ID` literal (lines 105, 126, 225, 252). Each book stamps the
version it was *coded with*, not the version that actually governed the
crossing. Under P5's rule — *only entries both sides agree on count as shared*
— the contract agreement in v0.1 is a shared module constant, which is a
shared assumption wearing the clothes of a shared record. That is exactly the
thing the programme distrusts elsewhere ("there is no God's-eye view, only
perspectives that learn from each other").

The fix is small and it is the seed of everything else: **the version that
governed a crossing must travel in the envelope.** The receiver then stamps the
version it *applied*, which is a fact about the receiver, not a constant it
happens to share with the sender.

**3. `AlgorithmContract` has no runtime role.** The class is exported and
tested, but the seam never instantiates it — the seam stamps a string. So
v0.1 has a contract *type* and a contract *string* and no binding between
them. The deepening has to make the type the thing that governs.

**The question this note answers:** what is a contract *version*, how is a
revision agreed between two books that may learn of it at different times, and
what does the ledger say about crossings that straddle the boundary.

---

## 1. Identity — what a version is

### 1.1 The choice

A contract reference becomes `(contract_id, contract_version)`:

- **`contract_id`** — a stable name for the *family* of the contract:
  `"beam-quilt/seam-handshake"`. Not content-derived. A revision is a new
  version **of that same id**.
- **`contract_version`** — the SHA-256 hash of the contract's **canonical
  statement**, in lowercase hex. This is the version's identity. It is
  *computed*, never typed.

So: **a version is a hash of the contract's canonical statement, and nothing
else.** A monotonic counter is not part of a version's identity.

The canonical statement is a machine-checkable description of the algorithm:
for the seam-handshake contract, the ordered handshake, the decision types,
the ending rules, the divergence rule — the shape of the thing, not its
calibrated parameters. Band and yaw-time are the receiver's *context*,
context-set and learnable (SPEC §3); they are readings, not the contract.
A contract whose version changed every time a band was recalibrated would be
a contract that changed constantly and meant nothing.

### 1.2 Why not a monotonic counter

A counter needs an authority to mint it. There is none. Two sovereign books
that each counted "the next revision" independently produce two different
`v2`s — the mechanism *manufactures* the disagreement it is supposed to
resolve. And "which is later" is not recoverable from a counter either,
because the counters are minted by the parties, not by a clock.

Worse: two books mint `v2` for two different statements and the string
comparison says "equal". The counter hides the fork behind a coincidence of
naming.

### 1.3 Why not "both" — and what the counter is actually good for

The brief offers "both" (hash *and* counter). The honest answer is: **both
fields exist, but only the hash decides.**

The counter survives as a **display label** — `"v2"`, so a human reading the
ledger does not have to memorise hashes. It is explicitly *not* identity and
*not* an ordering authority. Its one real job: **a label collision with
differing hashes is a fork tripwire.** Two books that both call their
different statements "v2" have, in one glance, surfaced a divergence that
the hash comparison would have found anyway. The label makes the loud thing
legible; it never resolves it.

### 1.4 Ordering comes from the chain, not from the counter and not from the hash

This is the part that makes the two-books problem tractable. A hash **cannot
be ordered** — "the higher hash wins" is meaningless, and "the newer
timestamp wins" needs a shared clock, which the doctrine refuses (cross-room
time is itself an agreement, not a clock; there is no God's-eye view).

So ordering is carried by an explicit pointer inside the revision record:

```
supersedes: <contract_version of the predecessor> | null
```

The revisions form a **chain**: `H1 ← H2 ← H3`. Order is derived from chain
links, both sides can derive it independently from entries they hold, and
two books holding different prefixes of the chain still agree about the order
of what they share. Nothing needs a sequencer.

### 1.5 Why this survives the two-books-learn-at-different-times case

Three properties, each load-bearing:

1. **Identity is derivable independently.** Both sides compute the same hash
   from the same bytes. The revision's statement *travels* (as the payload of
   the proposal crossing), so there is nothing to look up and no store to
   consult. A side that has never heard of the revision simply has no entry
   for it — it is not wrong, it is behind.
2. **Order is derivable independently.** The chain lives in the entries, not
   in a clock.
3. **A stale side is not a wrong side.** Being behind on a version is not a
   defect of the ledger; it is the normal state of two sovereign books. The
   design's job is to make being behind *safe and visible*, not to prevent it.
   That safety comes from §3's rule and nowhere else.

### 1.6 What the hash actually binds — and what it does not

Be honest about this, because it is the load-bearing limit of the whole design.

**The version binds an agreement, not a binary.** It hashes the *statement*.
It does not attest that the code executing at either end matches the
statement. Two builds with identical statements and different code produce
identical versions and behave differently. The ledger cannot tell.

That is not a defect to be papered over; it is what a contract is in this
programme. "A contract is an algorithm" means the *agreed* algorithm — the
shape the parties agreed to — not a cryptographic attestation of a running
process. The ledger records the agreement. The program is honest about the
difference.

Two mitigations are in reach and one is not:

- **Optional `code_digest` inside the statement.** A sender may include a
  digest of its implementation (module hash, weights hash). The receiver
  compares it *if it holds the same artifact*; a mismatch logs a
  `code_digest_mismatch` event. This is a field in the statement, not a new
  mechanism. It does not make the version attest the code — it makes a
  *disagreement about* the code visible.
- **Statement dependencies pinned by hash.** The statement may cite its
  inputs — e.g. the SPEC clause the deadband comes from. Those citations must
  be **content hashes, not mutable pointers**. `"spec": "beam-quilt/SPEC#v1.2"`
  is a lie the moment v1.3 lands; `"spec_clause_hash": "sha256:..."` is a
  fact. This is why the seam's version label currently tracking the SPEC
  version was a smell: it made the SPEC's mutability look like the contract's
  identity.
- **Not in reach:** a witness that re-checks version claims over time
  (Patchwork-experts proposes it; it is not built). The SPEC's G3 drift-watch
  is the nearest shape — a cheap watcher on spot-check duty — and it is
  **proposed**, not settled.

### 1.7 Canonicalization

The hash must be identical on both sides, so the canonical form must be
pinned, not merely "some JSON". The rule:

- UTF-8, NFC-normalised, LF line endings, no trailing whitespace;
- JSON: keys sorted, no insignificant whitespace — the discipline
  `canonical_json` already uses in `quilt_engine/core/envelope.py:45`;
- statements are **data**, not free prose, wherever the shape allows, so two
  implementations of the same algorithm can be compared. A prose statement is
  still hashable (normalise the whitespace) but two spellings of one
  algorithm will hash differently. That is not fixable here; see §7.

`constraint-theory-core`'s exact-snap is the cited prior art for
cross-machine canonicalization on the shelf (Lane C, "cite, don't import").
We do not depend on it; we copy the discipline.

---

## 2. Revision as a crossing

### 2.1 A proposed revision is a ledger entry, and it is also a crossing

The doctrine: a contract is *an early ledger entry — an agreement about future
sends and receives*. So a revision is booked in the ledger. But the revision
also has to *reach* the other side, and the only thing that crosses a seam is
an envelope. So it is both:

- **As a crossing:** the proposal travels as an ordinary envelope of type
  `contract/revision`, is profiled, is picked, is booked SEND by the proposer
  and RECEIVE by the peer, like any other crossing.
- **As an early ledger entry:** both sides additionally book a **revision
  record** — the ex-ante half of the ledger. (FOUNDATIONS: "the two halves of
  the ledger — contracts ex ante, movements ex post — compose.")

The proposal's payload carries the statement, so the proposal is
self-sufficient: one round trip delivers the whole revision.

```
proposal payload (type: "contract/revision")
{
  "contract_id":   "beam-quilt/seam-handshake",
  "version":       "<sha256 of the canonical statement>",   # identity
  "label":         "v2",                                    # display only
  "supersedes":    "<sha256 of the current version>" | null, # ordering
  "statement":     { ...canonical statement... }            # the bytes that were hashed
}
```

Note what the proposal's *own* crossing cites: `(contract_id, H1)` — the
version **in force when the proposal was made**. The proposal is made under
the current contract; the thing it proposes is a *new* version, carried in the
payload. Conflating the two is the first mistake this design has to avoid.

### 2.2 Who may propose

- **A party to the seam may propose a revision of that seam's contract.**
  A contract is an agreement between parties; only a party can propose to
  change it. A third party proposing over the seam is refused — no standing.
- **A patch's own unilateral algorithm is revised by its owner** (C1:
  enrolment and departure are the owner's act). A unilateral contract is not
  a bilateral one; there is nothing to agree with, so its version is the
  owner's to move. It still gets a version and still gets stamped on the
  crossings it governs.
- **A new algorithm is not a revision.** If the proposal changes the contract's
  subject rather than its version, it is a new `contract_id` and a fresh
  agreement — a different act, not a revision.

### 2.3 Who must book RECEIVE — and why RECEIVE is not enough

This is the trap in the question. **Receipt is not adoption.**

- The **peer must book a RECEIVE** for the proposal crossing — the proposal
  happened, and P5 requires every crossing to be booked by both sides.
- The peer must *additionally* book an **adoption** of the version. That is
  a separate booking, with its own reason field, in the revision-record
  half of the ledger.
- The **revision governs** only when *both books hold it* — the same
  bilateral rule that governs crossings (P5). The proposer learns of the
  peer's adoption when the adoption ack crosses back and the proposer books
  its RECEIVE of that ack; only then does the proposer's book also hold the
  record.

So the sequence for a successful revision is three bookings on each side, not
one:

| step | proposer books | peer books |
|---|---|---|
| proposal crossing | SEND (`contract/revision`) | RECEIVE (`contract/revision`) |
| revision record | `position: "proposed"` | `position: "adopted"` (or `"refused"` + reason) |
| adoption ack crossing | RECEIVE (of the ack) | SEND (of the ack) |
| revision record (final) | `position: "adopted"` | (already holds it) |

### 2.4 What "adoption" means when the two sides adopt at different times

**Local adoption is local. The governing version is a bilateral fact.**

A side that adopts v2 has not thereby changed the seam's contract; it has
recorded its own position. The seam's current version is the version both
books hold — the intersection. Until the peer's adoption comes back, the
seam is still governed by v1, even on the side that has already said yes.

This is the honest form of the two-books problem: **there is no moment at
which "the contract changed" that both sides see.** There is a moment on each
side, and they differ. So the design must never *require* both sides to
agree before a crossing can proceed — only before a crossing can count as
*shared*.

Which is exactly why §3's rule is the hinge: **a crossing is conducted under
the version it cites.** A side that is behind does not get its crossings
reinterpreted by a peer that is ahead; it gets them honoured under the
version it cited, and flagged. Being behind is safe.

### 2.5 The crossing in flight across the adoption boundary

Concretely: A cites v1 on a crossing at T1. A learns of v2's adoption at T2.
The crossing arrives at B at T3, after B has adopted v2.

- **B honours the citation.** B holds H1 (statements are retained), applies
  v1's rules to that crossing, and books RECEIVE stamping `contract_version
  = H1`. A stamped `H1` too. The two agree — the crossing is **agreed and
  shared**.
- B records `detail.superseded_citation = true`, because B's own current is
  v2 and it knows v1 is superseded.
- B's adoption of v2 does **not** retroactively re-judge that crossing. See
  §4.

The asymmetry in the other direction: A cites v2, B has not adopted v2 and
does not hold H2's statement. B cannot apply v2. B fails closed: it refuses
the crossing and answers with a **clarify/narrow response carrying the
missing statement** — the seam already has that response type, so this is
reuse, not invention. B books RECEIVE with `contract_version = null` and
`detail.citation_refusal = "statement not held"`. The two books' version
fields differ, so the crossing is **not agreed** and does not mint shared
state, and a `version_divergence` event is logged. The remedy is the
statement arriving; the cost is one round trip on the boundary.

---

## 3. Superseded citations

### 3.1 The rule

> **Honour the version the crossing cites. Record what you know about it.
> Never re-judge a crossing under a version it did not cite.**

Concretely, three cases, decided by the receiver at booking time:

| the cited version is… | the receiver… | books RECEIVE with | crossing is… |
|---|---|---|---|
| the receiver's current version | applies it | that version | agreed, shared |
| **superseded** but held, not revoked | **applies it** | the **cited** version | **agreed, shared**, `detail.superseded_citation = true` |
| **revoked** | does not apply it — refuses | the receiver's current version | **unagreed**, `detail.citation_refusal = "revoked"` |
| **unknown** to the receiver | cannot apply it — refuses, asks for the statement | `contract_version = null` | **unagreed**, `detail.citation_refusal = "statement not held"` |

Two words carry the design: **superseded is not revoked.** A superseded
version was validly agreed and merely overtaken — crossings under it are
honoured. A revoked version was withdrawn — crossings under it are refused.
Revocation is the safety valve; supersession is ordinary life.

### 3.2 The cost, stated plainly

1. **The shared set can contain crossings conducted under a version the peer
   has since moved past.** The ledger records it and the fold surfaces it,
   but nothing in the mechanism makes such a crossing *not*-shared. A reader
   of the fold must look at `superseded_citation` counts, not just the
   agreement flag.
2. **The flag is noisy during the adoption window.** Every crossing from a
   peer that has proposed v2 but not yet received the acceptance is flagged,
   because the receiver cannot know the peer is still legitimately on v1.
   The window is short; the noise is proportional to it.
3. **No self-healing.** A stale peer keeps producing shared crossings under
   an old version until someone proposes, adopts, and notifies. There is no
   push mechanism — there is no store and no God's-eye view. The only
   notification path is a crossing.
4. **The receiver knowingly applies rules it has moved past.** It is a
   choice, and it is a choice made in the receiver's favour: the sender
   proposed an algorithm, the receiver evaluates on its own terms, and the
   *record of what was applied* is exact.

### 3.3 The alternative I rejected

**Rejected: re-judge under the current version** — the receiver applies its
own current version to a crossing that cites an older one, regardless of the
citation.

Why rejected, in order of severity:

1. **It falsifies the record.** The ledger would show a crossing settled
   under v1 when it was settled under v2. That breaks "record primary, state
   derived" (the 2026-10-12 hinge) at the root: the record would no longer
   be a faithful account of what was decided.
2. **It makes agreement checks vacuous.** Every crossing during the adoption
   window would be booked under *different* versions by construction, so
   almost nothing would be agreed, so almost nothing would be shared. The
   seam would stop working for exactly the peer that is behind.
3. **It is the sender's algorithm being replaced by the receiver's.** The
   receiver is sovereign over *whether to accept*; it is not sovereign over
   *what the sender ran*. Substitution is not sovereignty.
4. **It needs the receiver to hold the newer statement** — so the side that
   proposes and runs ahead is punished until the other side catches up.

Also rejected: **treat any non-current citation as a dispute and refuse
outright.** Too blunt — a superseded version is not a wrong version, and
refusing would freeze the seam at the moment of any revision, for every peer
that has not yet caught up.

### 3.4 Where the disagreement gets logged

The SPEC's §3.4 discipline — *logged as a first-class event, never averaged
away* — extends to versions. The existing `DisagreementRecord` shape gains a
kind: `profile` (the existing sender-profile/receiver-measurement divergence)
and `version` (the two books naming different versions for the same
crossing). Both measurements are kept verbatim; a version disagreement is
never reconciled into a single "effective version".

---

## 4. Derived state

### 4.1 State is a fold of the ledger, per book

Each book folds its own ledger into:

```
proposals_seen   versions proposed to it or by it
adopted          versions it has agreed to
revoked          versions it has agreed to revoke
current          the tip of the adopted chain that is not revoked
```

`current` is a **set operation, not a timestamp comparison**: among the
versions this book has adopted and not revoked, the current one is the one
that no other adopted, non-revoked version supersedes. If the tip is revoked,
walk back to the nearest adopted, non-revoked ancestor. No clock is involved
at any point.

The *shared* fold — the one that mints shared state — is computed by
comparing two books, exactly as `agreed_crossings(entries_a, entries_b)`
already does. That comparison is owner-side audit, not live operation. A
running seam never needs to see the peer's book.

### 4.2 Going forward, not backward

> **A revision does not re-fold history. The fold becomes version-aware going
> forward.**

A revision's effect is scoped to crossings booked **after** the revision
becomes bilaterally effective. Crossings already booked keep the version
stamped in their entries and the decision recorded in their entries. The
fold of history does not change when v2 lands; the fold of *the future* does.

The reason is the programme's own hinge: record primary, state derived. If a
revision re-folded history, the ledger would stop being a faithful record
and become an argument about what the past meant.

### 4.3 Crossings settled under v1 when v2 changes the settlement rule

**They stand. They are settled under v1, and the ledger says so.**

If v2 changes the admissibility floor, the deviation units, the response
policy — anything — every crossing already acted on under v1 keeps its
recorded response and its recorded version. The fold reports:

- *N* crossings settled under v1, of which *M* were settled under a since-
  superseded version;
- the version each was settled under, and the version in force now.

Re-litigating a settlement is **not** a re-fold. It requires an explicit
**compensating entry** — a new ledger record that references the old
settlement and states the correction. This is the shelf's settled rule
(`quilt-chrono`): *a rewind does not erase — it appends.* The original
settlement stays byte-intact and readable; the correction sits beside it.

Two honest consequences:

- **"What would v2 have decided?" is not answerable from the ledger.** The
  ledger records the payload hash, not the payload; the inputs a v2 judge
  would need are not in it. Re-running history requires the payloads and the
  statements, which live elsewhere and age.
- **The seam accumulates settlement regimes.** Over time, the fold shows
  crossings settled under several versions with different rules. That is the
  price of a non-retroactive history, and it is the same price the programme
  already accepts for readings that age (P4).

### 4.4 The fold is per book; shared state is the agreed fold

Because each book folds only its own ledger, two books can hold different
current versions during the adoption window. That is not a bug — it is the
doctrine. What mints shared state is the *agreement* between books, and the
agreement for a crossing is extended in §6 to include the version. So:

- a crossing both sides booked under the same version is **shared**;
- a crossing the sides booked under different versions is **not shared**,
  and the divergence is logged;
- the *current* version of a contract, from the owner's point of view, is
  the latest version both books hold — computed by the same two-book
  comparison that computes agreed crossings.

---

## 5. Failure cases

### 5.1 A revision is proposed and never adopted by the other side

- The proposal **crossed**, so it is booked by both sides and is part of
  shared history. Agreement on the proposal ≠ adoption of the version. These
  are different facts and the ledger keeps them apart.
- The revision record stays `position: "proposed"` on the proposer's side
  and `position: "refused"` (with a reason) or absent on the peer's side.
- **No effect on the seam.** The seam continues under v1. Pending proposals
  do not block traffic — the shelf's `quilt-rooms` rule, and the right rule
  here too: a pending revision must not freeze the crossings that are
  happening now.
- **The peer must answer.** The receiver books a position for every revision
  proposal it receives — adopted or refused, with a reason. Without that, the
  proposer cannot distinguish "not yet answered" from "refused", and the
  ambiguity is worse than the refusal. A peer that dies before answering
  leaves the proposal pending forever; that is the peer's absence showing
  through, which is honest.
- **Withdrawal.** The proposer may book `position: "withdrawn"` on its own
  pending proposal. Withdrawn proposals have no effect and are not refused
  records.
- **One adoption refuses the competing pending proposals.** Adopting a
  successor of P implicitly refuses any pending proposal that proposes a
  *different* successor of P — the recorded reason is "superseded by the
  adoption of H2". Otherwise the peer would be left holding two live claims
  about the same slot.

### 5.2 Both sides propose different v2s concurrently

Two different statements, two different hashes, both superseding H1. This is a
**fork in the chain**, and it is the case the design most has to get right.

**The rule: neither wins. The seam stays on H1 and the fork is recorded
loudly.**

- Both proposals are valid revisions. Both are booked; both are pending.
- **A book may not adopt a version that forks the chain.** If the book
  already holds an adopted V with `supersedes = P`, adopting W with
  `supersedes = P` (W ≠ V) is refused with reason `fork: P already
  superseded by V`. Only one successor of P can be in force.
- Neither side can adopt while the other's proposal is outstanding, because
  adopting either forks against the other's *pending* claim on the same
  predecessor. So the honest state is: both proposals pending, seam on H1.
- **The crossing set keeps working.** Crossings continue, each citing and
  applying H1, and remain agreed and shared. A contract dispute must not
  stop the seam — that is the whole reason the seam is separate from the
  contract dispute.

**How it resolves.** Four routes, and the design does not pick a favourite
because three of them are genuinely the author's call:

1. **Withdrawal.** One side notices the fork (the conflict notification is a
   crossing, so it can be) and withdraws its proposal. The other's is then
   unopposed and can be adopted.
2. **The owner arbitrates.** C1 makes enrolment and departure the owner's
   act; the owner is the obvious authority over *what contract its patch
   speaks*. Not implied by the doctrine though — see §8, open question 2.
3. **It was never a conflict: two contracts.** If the two statements are
   genuinely different algorithms, they are two contracts and one of them
   has the wrong id. The repair is to propose it under a new `contract_id`.
4. **Chain forward.** A later proposal that supersedes one branch (say H3
   superseding H2a) resolves the fork in that branch's favour when adopted;
   the abandoned branch can then be withdrawn or refused as superseded.

**The limit, stated plainly:** the mechanism can *detect* a fork — two
different hashes claiming one id — but it cannot decide whether the two
statements are the same algorithm written two ways. That is a
canonicalization question, not a versioning question (§7). What it can do is
make the fork loud instead of silent, which is the whole of what
`edge-ledger`'s loud fork rejection contributes here.

### 5.3 A crossing in flight across the adoption boundary

Covered in §2.5. In short: the cited version governs the crossing, both sides
stamp the same version, the crossing is agreed, and the receiver records that
the citation was superseded. A crossing citing a version the receiver does not
hold is refused and asked for; a crossing citing a revoked version is refused.

### 5.4 Rollback of a bad revision

> **Rollback is not a v3. Rollback is a distinct act: a revocation.**

A v3 would be a new statement that has to travel, be proposed, be adopted,
be acknowledged — and it would imply the bad v2 was validly in force, which
it was not. A revocation says something different and simpler: *this version
is withdrawn; the chain falls back to its nearest live ancestor.*

**A revocation is a bilateral act, like any other revision.** It crosses as
an envelope (`contract/revocation`), is booked SEND/RECEIVE by both sides,
and both sides book a revocation record. It is not a unilateral local edit —
one side revoking alone would leave the two books disagreeing about whether
v2 governs, which is precisely the disagreement the whole mechanism exists
to make visible.

**Rollback is version-local.** Revoking H2 does not void H3, if H3 was later
adopted on top of it. The chain and the revocation set are separate facts;
the fold reports both. (Whether a revocation should void its descendants is
§8, open question 6.)

**Settlements under a revoked version stand.** This is the erised scar rule
and the quilt-chrono compensating-entry rule, arriving at the same place:
rewind does not erase. Crossings settled under H2 keep their recorded
decision. The fold reports them as *settled under a revoked version* — the
scar is visible, the history is intact. To correct a wrong settlement, book
a compensating entry that references it.

**Two rollback cases, which are different things:**

- **Withdraw before adoption** — the proposal is withdrawn. No effect on the
  seam; the record stays, marked withdrawn. Trivial.
- **Revoke after adoption** — the bilateral revocation above. The seam falls
  back to the nearest live ancestor; H1 becomes current again without anyone
  re-proposing it, because H1 was never revoked.

**A re-proposal after a revocation is a new statement, not a restoration.** If
the proposer wants to fix the bad revision rather than abandon it, they
propose a new statement S2′ with a new hash H2′ that supersedes H1. It is a
fresh adoption cycle with the same machinery, and it carries a different hash
— so the ledger distinguishes "the fixed v2" from "the reverted v2" without
any extra field.

### 5.5 Other cases worth naming

- **A peer that never learns of the revision.** No push, no store, no
  God's-eye view. The peer keeps citing the old version; the receiver honours
  it and flags it; the fold shows the drift. The only repair is the
  notification crossing that the adoption ack already is. This is the
  standing cost of §3, and it is the normal case, not an edge case.
- **A proposal from a non-party.** Refused — no standing to revise an
  agreement you are not party to.
- **A unilateral contract revised by its owner.** One-sided: the owner books
  the revision record; no ack, no bilateral intersection; the version moves
  when the owner moves it. The crossings governed by it are stamped with the
  version in force at booking time.
- **A revision that changes only a label.** If the statement's *bytes* do not
  change, the version does not change — a label is not a revision. Labels
  live outside the hashed bytes (§1.3).
- **A statement whose dependencies moved** (the SPEC clause it pins changed).
  The version does **not** move, because the pinned dependency is a content
  hash. What moves is the *claim* that the statement is still appropriate —
  and that is the G3 drift-watcher's job, which is **proposed**, not built.

---

## 6. Minimality — field-level changes to v0.1's shapes

Shapes, not code. Every field below is either a rename-with-split, one new
optional field, or one new record kind. Nothing in the envelope's existing
tuple moves.

### 6.1 `TransmissionEnvelope` — one new optional pair

```
  payload, type, ending, cost_budget, sender_profile,
  transmission_id, provenance, copy_authorized_by
+ contract_id:      str | None = None      # the contract family
+ contract_version: str | None = None      # the version the sender applied
```

- `to_dict`/`from_dict` gain the two keys; missing keys read as `None`, so a
  v0.1 envelope still parses.
- `refreshed()` carries them along — a re-send is the same crossing under the
  same version.
- The version travels **in the envelope** rather than being looked up. This
  is the whole fix for "the two books agree on the contract because they
  share a module constant."
- **Considered and rejected:** putting the citation in
  `provenance.attributes` — zero new fields. Rejected: a load-bearing field
  hidden in a free-form bag is not typed, not checkable, and does not survive
  the "the contract type must make a bare version unrepresentable" discipline
  the repo already applies to P2.

### 6.2 `LedgerEntry` — the flat contract string becomes two fields

```
- contract: str            # "name@version", flattened
+ contract_id: str
+ contract_version: str | None   # the version THIS side applied
```

- The value is **not** a display string any more; it is the hash the side
  actually used. The old flattened form is still *readable* (split on `@`)
  but its version half is a **label, not a content hash** — a legacy ledger
  is not trustworthy about versions, and the reader should say so rather
  than silently accept it.
- `detail` (already present) carries the extras without new top-level
  fields: `superseded_citation`, `citation_refusal`, `version_divergence`,
  `in_reply_to`.

### 6.3 New record kind: `RevisionEntry`

```
kind: "revision"
contract_id:   str
version:       str          # content hash
label:         str | None   # display only
supersedes:    str | None   # predecessor version hash
statement_hash: str         # == version; explicit for the reader
proposed_by:   str
proposed_at:   float
position:      "proposed" | "adopted" | "refused" | "withdrawn"
reason:        str | None   # why refused
related_crossing: str | None   # the proposal crossing's transmission id
```

plus a revocation record, same shape with `position: "revoked"` and the
target version in `version`.

`StateStore` grows a `revisions` list and a `book_revision()`; `load()`
reads the new `kind`. The JSONL file already carries a `kind` discriminator
(`reading` / `ledger` / `event`), so this is a third kind, not a new file.

### 6.4 `AlgorithmContract` — the version becomes derived

```
  name: str
- version: str                    # supplied
+ statement: dict | str           # the canonical statement
  fn: Callable[[Any], ContractResult]
+ version: str                    # DERIVED = sha256(canonical_json(statement))
```

- `version` becomes a **computed** field, not a supplied one. This makes
  "a hand-typed version" unrepresentable — the same discipline the repo
  already applies to P2's "a bare score is non-conforming" (`contract.py:23`).
- `identity` stays `"name@version"` and keeps working.
- The constructor signature changes
  (`AlgorithmContract(name="echo", version="1", fn=...)` →
  `AlgorithmContract(name="echo", statement={...}, fn=...)`). One call site in
  the tests; it is the right change.
- **The contract object gets a runtime role.** The seam takes a contract
  object, not a string constant, and derives the version it stamps from it.

### 6.5 `PatchManifest`

```
- contract: str = ""
+ contract_id: str = ""
+ contract_version: str = ""
```

The manifest (P1: human-readable without opening the patch) declares which
contract **version** the patch implements — a patch that implements v2 and
says `v1` in its manifest is a bug the manifest now makes visible.

### 6.6 `agreed_crossings` — the one-line change that carries the whole design

```
  key = (transmission_id, payload_hash, side)
+ key = (transmission_id, payload_hash, contract_version, side)
```

Agreement now requires the two books to have applied the **same version**.
Everything in §3 falls out of this: a crossing the receiver could not apply
under the cited version produces mismatched version fields, so the crossing
is not agreed, so it does not mint shared state, and the divergence is
logged rather than averaged.

### 6.7 `seams/pair.py` and the demo

- `JevPair` takes a contract **object** (`contract_id` + derived version)
  instead of the flattened `contract_id` string.
- The sender stamps the envelope's `contract_id`/`contract_version` before
  crossing; the receiver stamps **the version it applied** into its RECEIVE.
- `DEFAULT_CONTRACT_ID` splits into a contract id and a contract object whose
  statement is a pinned document in the repo (e.g. a JSON block describing
  the handshake), so the demo's version is a real content hash rather than
  the literal `"1.2"`.
- **The two-process demo stops importing a shared version constant as the
  thing that governs.** The sender's envelope carries the version; the
  receiver applies and stamps what it applied. If the demo's receiver is
  pointed at a different statement, the crossing becomes unagreed — and that
  is now *demonstrable* rather than invisible. This is the smallest test
  that would prove gap 3 is closed.

### 6.8 What does not change

- The envelope's existing tuple, the ending rules (T1–T3), the deadband, the
  decision types, the disagreement record shape, the reading model, the
  GC rules, the store's JSONL form, `agreed_crossings`'s return shape, and
  the demo's file-drop transport.
- No new file format, no new transport, no shared store, no registry, no
  clock.

---

## 7. What this design does not solve

1. **It does not certify that the code matches the statement.** The version
   binds an agreement about an algorithm, not a running binary. Two builds,
   one version, different behaviour.
2. **It cannot tell that two statements are the same algorithm written two
   ways.** Canonicalization drift looks exactly like a fork. The mechanism
   makes it loud; it cannot resolve it.
3. **It gives no retroactive re-judgement.** "What would v2 have decided?" is
   unanswerable from the ledger, because the ledger holds the payload hash,
   not the payload.
4. **It gives no shared clock and no global order.** Order is per-contract
   chain order. Two contracts have no relative order, and there is no
   programme-wide "now".
5. **It does not arbitrate a genuine dispute.** The design can hold a fork
   open and record both positions. Deciding which side is right is not
   mechanical, and this note does not pretend otherwise.
6. **It does not push.** A peer that is behind stays behind until a crossing
   tells it otherwise. There is no store, no registry, no God's-eye view.
7. **It is pairwise.** Every agreement rule here is bilateral. A contract
   binding three or more parties would need an adoption rule this design
   does not have.
8. **It does not specify the transport for fetching a missing statement.**
   That is the transport's business, not the seam's.
9. **It does not make the receiver's *reason* for refusing a revision
   resolvable.** The fold shows both positions with their reasons. It does
   not adjudicate them.

### And the SPEC dependency

This note changes what P5 means. P5 currently says a crossing is booked "under
the contract (algorithm) that governed the crossing" — which the ledger
cannot currently say, because the contract has no version to say it with.

**The SPEC amendment lands in beam-quilt first** (the standing discipline:
the SPEC is the citation; this repo follows it). The amendment is small:

> P5 — *...each crossing MUST be booked by both sides — a SEND by the sender,
> a RECEIVE by the receiver — under the contract (algorithm) **and the
> version of that contract** that governed the crossing...*

plus a new clause for the revision record (the ex-ante half of the ledger),
and a note in §3 that the version travels in the envelope rather than being
agreed separately. Nothing in this note should be built before that
amendment lands.

---

## 8. Open questions for the author

These are genuine rulings, not design choices. Each one changes what gets
built, and each is the author's to make.

1. **Does a contract revision need the owner's consent, or is bilateral
   adoption enough?** C1 makes enrolment and departure the owner's act. A
   revision that changes the *settlement rule* changes what a patch will do
   with an owner's data — arguably a change of the kind C1 covers. If yes,
   the revision record gains an owner-consent field and the adoption cycle
   gains a step. *Consequence: three-party acceptance versus two.*
2. **Who arbitrates a genuine contract dispute?** Neither side withdraws,
   no third party, no store. Options: the owner (consistent with C1), a
   designated tiebreak station in the quilt, or nobody — the seam stays on
   the old version indefinitely and the fork simply persists as visible
   damage. The design supports all three and picks none. *Consequence: §5.2
   has no default resolution.*
3. **Is the human label part of the identity?** This note says no: the label
   is display only, outside the hashed bytes, and a label collision with
   differing hashes is a fork tripwire. If labels should be part of the
   identity, two books with different label conventions will disagree
   cosmetically forever, and the hash stops being the tiebreaker. *Consequence:
   what "the version" means when a human reads it.*
4. **How much of a contract is revisable?** If v2 may change the settlement
   rule, the ledger accumulates settlement regimes and §4's "settlements
   stand" becomes a property of the mechanism rather than a policy of it.
   Should settlement rules be immutable, with only the non-settlement parts
   (profiling, typing, endings) revisable? *Consequence: whether §4.3's
   split is a law or a convention.*
5. **Can a contract bind three or more parties?** The entire design is
   pairwise — adoption, revocation, fork refusal all assume two books. If a
   contract can bind a fleet, the adoption rule needs a quorum or a
   unanimity decision, and §2.3's table changes shape. *Consequence: whether
   this note is the whole story or the two-party case of a larger one.*
6. **Does a revocation need a cause, and does it void its descendants?**
   The scar rule (the erised precedent) says a failure record survives a
   rewind and must be addressed — should a revocation record carry a
   mandatory reason, and is revoking an ancestor enough to void the
   descendants built on it, or must each descendant be revoked separately?
   *Consequence: whether "rollback" is one act or a chain of them.*

---

## Appendix A — a worked timeline

Two books, `A` (sender) and `B` (receiver). Contract `seam-handshake`, v1 =
`H1`. The seam is running under H1 at T0.

| when | A books | B books | seam state |
|---|---|---|---|
| T0 | — | — | current = H1, both sides |
| T1 | SEND `contract/revision` (proposes H2, supersedes H1) | RECEIVE of the same crossing | current = H1. The proposal is shared history; the version is not in force. |
| T1.5 | revision record: `position: "proposed"` | revision record: `position: "adopted"`, reason null | B's local current = H2. B's bilateral current = H1. B honours H1 citations meanwhile. |
| T2 | RECEIVE of the adoption ack; revision record: `position: "adopted"` | SEND of the adoption ack | current = H2 on both sides. Bilateral. |
| T3 | SEND crossing citing `(seam-handshake, H2)` | RECEIVE applying H2, `contract_version = H2` | Agreed. Shared. Clean. |
| T4 | SEND crossing citing `(seam-handshake, H1)` — A restarted with an old statement and has not learned of H2 | RECEIVE applying **H1** (B holds it), `contract_version = H1`, `detail.superseded_citation = true` | Agreed — both applied H1. Shared, **flagged**. The fold shows the drift. |
| T5 | SEND crossing citing `(seam-handshake, H2)` — B, meanwhile, has revoked H2 | RECEIVE: B does not apply H2, refuses; books `contract_version = null`, `detail.citation_refusal = "revoked"` | **Unagreed.** Not shared. Logged as a version divergence. Never averaged into a single effective version. |
| T6 | — | revocation record: `position: "revoked"` (H2), after A accepts the revocation | A's current = H1 again (nearest live ancestor). No re-proposal needed: H1 was never revoked. |

Two things to read off the table:

- **T4 is the answer to the brief's central question.** A crossing SENT under
  v1 and RECEIVEd after v2 was adopted is **governed by v1**, on both sides,
  and it counts as agreed and shared. B's adoption of v2 changes what
  happens next, not what happened.
- **T5 is the answer to "which version governs a crossing that cites one the
  receiver considers superseded?"** — for a *revoked* version, neither: the
  receiver refuses, the versions disagree, and the crossing does not mint
  shared state. For a merely *superseded* version, the cited one governs.
  The two words are the whole rule.

---

## Appendix B — the one-paragraph version

A contract reference is `(id, version)`, where the version is the hash of the
contract's canonical statement and nothing else; a counter survives only as
a display label, and ordering comes from an explicit `supersedes` chain
rather than from a clock or a number. A revision is a crossing that carries
the new statement, plus an early ledger entry on both sides; it governs when
both books hold it, and until then it is pending and blocks nothing. A
crossing is governed by the version it cites: superseded citations are
honoured and flagged, revoked or unknown citations are refused and leave the
crossing unagreed, because agreement now includes the version and the
divergence is logged rather than averaged. The fold becomes version-aware
going forward; settlements already made stand, and correcting one is a
compensating entry, not a re-fold. Rollback is a revocation, not a v3:
bilateral, version-local, forward-only, and it leaves a visible scar on
settlements made under the bad version. And the parts it does not solve —
code attestation, canonicalization drift, retroactive re-judgement, dispute
arbitration, push, and non-pairwise contracts — are listed above rather than
handed over as problems the mechanism has quietly solved.
