# V02-REPORT — quilt-engine v0.2: payload-transfer agreement (gap 2, deepened)

Base: `main @ 9752895` (v0.1, 42 tests green). Work on branch
**`v02-payload-agreement`** (local; not pushed, per the brief).
Stdlib only; no new dependencies; no persistence changes beyond the
existing JSONL books.

## What was built

### 1. Payload crossing type (`quilt_engine/core/envelope.py`, `core/payload.py`)

A transmission of **type `payload`** whose envelope carries an actual
payload — bytes (base64 on the wire) or a JSON value (as-is) — plus
its hash (the existing `payload_hash`), a **declared size**
(`payload_size`, bytes of the carried content), and the **governing
contract identity** (`contract`, the identity v0 already stamps on
ledger entries, now declared on the envelope itself). Two optional
envelope fields were added; old envelopes and old JSON are unchanged.
`make_payload_offer(content, contract=...)` builds one; `read_offer`
is the receiver's arrival check — it parses tolerantly (see decisions)
and returns an `OfferReading` holding declared hash/size vs the
receiver's own computed/measured values, with `hash_ok`, `size_ok`,
`ok`.

### 2. Two-phase booking (`core/payload.py`)

- **Phase 1 — OFFER.** `book_offer_send` (side `SEND`) on the sender;
  `book_offer_receive` (side `RECEIVE`) on the receiver — the latter
  refuses unless `reading.ok` (hash + declared size both check out).
  The payload is in neither side's derived state.
- **Phase 2 — SETTLE.** `book_settle` (side `SETTLE`) is *permitted*
  only by a matching pair and *refused loudly* without this book's own
  evidence of the match: the receiver needs its verified RECEIVE; the
  sender needs its SEND **plus a returned receipt crossing**
  (`payload/receipt`) naming the same hash. Refuses double-settle and
  any disputed id, forever. The SETTLE entry carries the wire content,
  so **`settled_payloads(ledger)` — the settled-payloads view — is a
  true fold of the ledger** (record primary, state as fold). Open
  offers (`open_offers`) are phase-1 bookings with no SETTLE: visible
  in both books, forever — no timeout magic, no silent expiry.
  `settleable_crossings(a, b)` is the audit-time pairwise gate:
  matched SEND/RECEIVE pair on (id, hash) and no dispute touching the
  id in either book.

### 3. Dispute path

On hash or size mismatch the receiver books **`RECEIVE-DISPUTED` with
its own computed hash** as the entry's `payload_hash` (declared
hash/size kept verbatim in the detail — §3.4 discipline, never
averaged), and a `payload/dispute` envelope crosses back, booked
**verbatim** (payload embedded in the booking detail) into the
sender's book — so the sender's book shows the dispute when the
response crossing returns. Consequences, all enforced: the tampered
crossing is *unagreed* under v0's own `agreed_crossings`; the id can
never settle (`book_settle` raises; `settleable_crossings` blocks —
even if the same id later arrives intact); and a corrected payload is
a **NEW transmission id**.

### 4. Demo (`examples/two_process_demo.py`)

`--scenario payload` added (default `all`: proposals then payload),
same harness, same file-drop transport. Exhibits, across two
processes:

- **P1 route-plan** (JSON): offered, verified, receipted, **settled
  end-to-end** — both books fold the same content.
- **P2 charts snapshot** (bytes): offered and **left open** — receiver
  verifies and receipts but holds no standing order for `charts@2`;
  sender offered speculatively; both books show it open, neither
  settled view contains it.
- **P3 cargo manifest**: **tampered in transit** (content mutated, the
  declared hash/size untouched) — disputed with the computed hash,
  dispute booked verbatim on both sides, never settled.
- **P3b**: the true manifest re-sent under a **new id** — settles
  cleanly.

The demo's verifier asserts all of the above directly against the two
books, plus the session-wide rule that the *only* unagreed crossings
are disputed payload offers.

### 5. Tests

18 new (17 in `tests/test_payload.py` driving two sovereign books
through the real wire forms; 1 process-level demo scenario test in
`tests/test_demo.py`). All 42 pre-existing tests pass unchanged
(defaults preserved: strict parse, old envelope JSON, proposals demo).

```
$ python3 -m unittest discover -s tests -v 2>&1 | tail -20
test_crossing_records_a_refusal_too (test_projection.TestSeamLogAudit.test_crossing_records_a_refusal_too) ... ok
test_crossing_records_the_projection_it_used (test_projection.TestSeamLogAudit.test_crossing_records_the_projection_it_used) ... ok
test_projected_value_is_value_plus_trend_times_age (test_projection.TestTrendDerivation.test_projected_value_is_value_plus_trend_times_age) ... ok
test_same_instant_readings_derive_nothing (test_projection.TestTrendDerivation.test_same_instant_readings_derive_nothing) ... ok
test_second_reading_carries_the_vector (test_projection.TestTrendDerivation.test_second_reading_carries_the_vector) ... ok
test_trend_is_per_cell_and_numeric_only (test_projection.TestTrendDerivation.test_trend_is_per_cell_and_numeric_only) ... ok
test_trend_survives_a_persisted_book (test_projection.TestTrendDerivation.test_trend_survives_a_persisted_book) ... ok
test_same_payload_weak_pool_vs_strong_pool (test_receiver.TestComparativePick.test_same_payload_weak_pool_vs_strong_pool) ... ok
test_band_must_stay_inside_safety_corridor (test_receiver.TestDeadbandShape.test_band_must_stay_inside_safety_corridor) ... ok
test_persistent_excursion_acts (test_receiver.TestDeadbandTiming.test_persistent_excursion_acts) ... ok
test_persistent_inadmissible_pool_refuses (test_receiver.TestDeadbandTiming.test_persistent_inadmissible_pool_refuses) ... ok
test_persistent_weak_pool_clarifies_never_silently_decides (test_receiver.TestDeadbandTiming.test_persistent_weak_pool_clarifies_never_silently_decides) ... ok
test_transient_excursion_holdwatch_then_hold_never_act (test_receiver.TestDeadbandTiming.test_transient_excursion_holdwatch_then_hold_never_act) ... ok
test_divergence_beyond_tolerance_is_recorded_not_averaged (test_receiver.TestDisagreement.test_divergence_beyond_tolerance_is_recorded_not_averaged) ... ok
test_within_tolerance_no_record (test_receiver.TestDisagreement.test_within_tolerance_no_record) ... ok

----------------------------------------------------------------------
Ran 60 tests in 25.909s

OK
```

Required coverage, where it lives:

| Brief requirement | Test |
|---|---|
| settle-only-on-match | `test_settle_only_on_match`, `test_receiver_settles_on_verified_receive`, `test_sender_settles_only_after_receipt_crossing`, `test_receipt_with_wrong_hash_does_not_enable_settle` |
| open-offer exclusion from derived state | `test_open_offer_excluded_from_derived_state` + demo assertions |
| dispute blocks settlement | `test_dispute_blocks_settlement`, `test_disputed_id_never_settles_even_after_clean_re_receive` |
| dispute visible in both books | `test_dispute_visible_in_both_books`, `test_tampered_crossing_is_not_agreed` |
| corrected payload under a new id settles cleanly | `test_corrected_payload_new_id_settles_cleanly` + demo P3b |

## Design decisions where the brief was silent

1. **The receipt envelope (`payload/receipt`).** Books are sovereign
   and only envelopes cross, so the sender needs an envelope — not a
   peek at the receiver's book — as evidence that the far side booked
   RECEIVE(offer). The receipt is that crossing (booked both sides,
   reverse direction, like v0's refusal/clarify responses). It is
   *necessary* evidence for the sender's SETTLE; the receiver needs no
   extra envelope (its own verified RECEIVE is its evidence).
2. **Settlement is a permitted act, not an automatic one.** "Only when
   both bookings match does either side book SETTLE" is a permission.
   Nothing auto-settles on match; the demo's open case is exactly the
   permission going unexercised. Demo policies: receiver settles only
   under contracts it holds standing orders for; sender settles on
   receipt under the same allow-list.
3. **Ledger representation.** New side values `RECEIVE-DISPUTED` and
   `SETTLE` in the same append-only books (proposal crossings
   untouched); `detail.phase` marks offer/settle. The SETTLE entry
   embeds the wire content — the fold `settled_payloads` can then
   return decoded content from records alone.
4. **Bytes on the wire.** Envelopes are JSON, so bytes ride base64
   inside the payload (`{"content", "content_encoding"}`); JSON values
   ride as-is. Declared/measured size = `len(bytes)`, or the
   canonical-JSON encoding length for JSON values — deterministic on
   both sides by construction.
5. **Tolerant parse for the dispute path.** The strict
   `from_dict`/`from_json` still refuses hash mismatches on arrival
   (v0.1 behavior preserved); the payload receiver parses with
   `verify_payload_hash=False` so it can read the id, compare declared
   vs computed, and book the dispute. A tamper that also corrupts the
   wire beyond decoding measures size −1 → dispute, not a crash.
6. **Size is a claim like the hash.** Hash ok but declared size wrong
   (a lying sender, or a size-only tamper) → dispute. A `payload`
   envelope with no declared size fails the size check: the kind
   requires the declaration.
7. **What "never settle" means.** A disputed id is dead forever, even
   if the same id later arrives intact (tested); "no retries with a
   mutated payload under the same id" cuts both ways — no retries, no
   second chances.
8. **Endings for payload transfers.** The library default stays
   PROJECT; the demo's transfers use COPY with the owner's standing
   order attributed (`copy_authorized_by`), per T2 — a settled
   transfer does duplicate content into the receiver's state.
9. **Tamper simulation.** The demo sender writes the offer file with
   mutated content but its own declared hash/size: the sender's book
   records what was *sent*; the wire carries something else. Note the
   honest limit: a tamperer who recomputes the hash consistently is
   undetectable here — there are no signatures in scope.
10. **Contract stamping.** Payload-offer, receipt, and dispute
    bookings are stamped with the payload's own `contract`
    (route-plan@1 etc.); proposal crossings keep the seam-handshake
    contract. The envelope-declared identity is the one booked.

## Found underspecified (choices made, flagged for the next lane)

- **Evidence for settlement per side** — the brief fixes the pairwise
  condition but not what each side must hold to book SETTLE
  (receipt design above). If a later spec wants a single
  "both-sides-settled" fact, that is a new crossing kind, not a book
  peek.
- **Is a disputed offer "open"?** We say yes — it is visible in both
  books and will never settle; `disputes()` lists it separately so the
  two states are distinguishable.
- **Shared settlement vs per-book settlement** — each book folds its
  own settled view (sovereignty); "shared" is asserted at audit time
  by comparing the two folds plus `settleable_crossings`. The demo
  settles both sides for the settle case.
- **Receipt of a payload the receiver does not want** — the protocol
  has no refusal for payload offers (only dispute-on-mismatch or
  leaving it open). A "decline" response kind would be a natural v0.3
  addition; the brief's non-goals kept us from inventing it here.

## How to run

```
python3 -m unittest discover -s tests          # 60 tests
python3 examples/two_process_demo.py --dir /tmp/qd --scenario payload
python3 examples/two_process_demo.py --dir /tmp/qd --scenario all
```
