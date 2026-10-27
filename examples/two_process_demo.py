"""Two-process demonstration — BUILD-BRIEF deliverable 3.

Two OS processes, **no shared memory**, passing transmissions through
the Jev-pair arbitrator over a **file-drop transport** (builder's
choice, stated here per the brief): the sender writes envelope JSON
files into ``inbox/``; the receiver writes response envelopes into
``responses/``. Each process holds only its *own* StateStore book
(sender_book.jsonl / receiver_book.jsonl); the seam log
(seam_log.jsonl) is written by the receiver, which hosts the seam's
decision point. The only things that cross are envelopes (SPEC P4).

Roles (spawned by the orchestrator at the bottom, or by tests):

    python3 two_process_demo.py --role receiver --dir DIR [--until S]
    python3 two_process_demo.py --role sender   --dir DIR
    python3 two_process_demo.py --dir DIR        # orchestrator + verify

Scenario (times in seconds, scaled by $DEMO_TIME_SCALE, default 1).
``--scenario proposals`` (below), ``--scenario payload`` (v0.2),
``--scenario versioning`` (v0.3), or ``--scenario all`` (the default:
proposals, then payload, then versioning):

    A  telemetry, merit 0.55, refreshed briefly — inside the band
       -> **Hold**. Its sender profile also overstates completeness
       against the receiver's own questions -> a **disagreement
       record** at the seam (SPEC §3.4).
    B  course-proposal, merit 0.95, sent once — an excursion outside
       the band whose reading goes stale before the yaw-time expires
       -> **HoldWatch, then Hold**, never Act.
    D  course-proposal, merit 0.97, refreshed past the yaw-time
       -> **Act(accept-best)**.
    E  course-proposal, merit 0.10, refreshed past the yaw-time
       -> **Act(clarify/narrow)**: a further typed question crosses
       back to the sender.
    F  course-proposal, merit -0.20, refreshed past the yaw-time
       -> **Act(refuse)**: the refusal profile crosses back (C2).

Payload scenario (v0.2 — gap 2, deepened: the agreement protocol
carrying an actual payload transfer, two phases, dispute path):

    P1 route-plan (JSON), settled end-to-end: offer crosses, receiver
       verifies hash + declared size, books RECEIVE(offer), receipt
       crosses back, both sides book SETTLE, both settled-payloads
       views contain the plan.
    P2 charts snapshot (bytes), offered and left **open**: both books
       show the phase-1 bookings, neither books SETTLE (the receiver
       settles only contracts it holds standing orders for; the sender
       offered speculatively), neither derived state contains it —
       open forever, no timeout magic.
    P3 cargo manifest, **tampered in transit** (content mutated, the
       declared hash untouched): receiver's computed hash differs ->
       RECEIVE-DISPUTED with the computed hash, a payload/dispute
       envelope crosses back (booked verbatim on the sender's side),
       P3 never settles.
    P3b the corrected manifest: same true content, **new transmission
       id** — settles cleanly end-to-end.

Versioning scenario (v0.3 — DESIGN-V03: contract versioning, the
ruling-neutral core; H1 = sha256 of the pinned handshake statement,
H2 = sha256 of its revision):

    V0 route-plan payload **settled under v1** — when v2 lands later,
       this settlement stands, byte-intact (§4.3: a revision does not
       re-fold history).
    V1 a crossing under v1, agreed cleanly (both books stamp H1).
    V2 the sender **proposes a revision** mid-run: a
       ``contract/revision`` envelope citing H1 (the version in force
       when the proposal was made), carrying the whole new statement
       in its payload (§2.1).
    V3 a crossing **in flight across the adoption boundary**: sent
       under H1 one breath after the proposal, received *after* the
       receiver adopted H2 — honoured under the version it cites
       (H1), both books stamp H1, agreed and shared, the receiver
       records ``superseded_citation`` and the seam log shows the
       skew (§2.5). Adoption is skewed: receiver first, sender only
       after the adoption ack crosses back (§2.3/§2.4).
    V4 a crossing under v2 after both books adopted — agreed cleanly.
    V5 a **stale v1 citation** after both sides adopted (a sender
       restarted with the old statement): honoured under the cited
       version, logged verbatim, never silently upgraded (§3.1).
    V6 a citation of a statement that **never crossed** (v0.3's third
       statement): the receiver fails closed — refuses, books
       ``contract_version = null``, a version_divergence is logged,
       and a clarify/narrow **statement request** crosses back. The
       crossing is *skewed*: matched on id and hash, mismatched on
       version — a category of its own, not plain unagreed (§6.6).

Receiver parameters: expected course 0.50, band 0.15, yaw-time 1.0 s,
candidate readings go stale after 0.5 s (P4: readings age, and the
pool is made of readings). Receiver settle policy: standing orders
exist for route-plan@1 and cargo-manifest@1, not for charts@2.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quilt_engine.core import (
    ADOPTION_ACK_TYPE,
    REVISION_TYPE,
    StateStore,
    TransmissionEnvelope,
    agreed_crossings,
    book_dispute_receive,
    book_receipt_receive,
    book_seam_receive,
    book_seam_send,
    book_settle,
    contract_state,
    current_version,
    disputes,
    make_revision_proposal,
    make_statement_request,
    receive_ack,
    receive_proposal,
    resolve_citation,
    seam_context,
    send_proposal,
    settled_payloads,
    statement_version,
    version_report,
)
from quilt_engine.core.envelope import Ending
from quilt_engine.core.payload import (
    DISPUTE_TYPE,
    PAYLOAD_TYPE,
    RECEIPT_TYPE,
    book_dispute_send,
    book_disputed_receive,
    book_offer_receive,
    book_offer_send,
    book_receipt_send,
    make_dispute,
    make_payload_offer,
    make_receipt,
    open_offers,
    read_offer,
    settleable_crossings,
)
from quilt_engine.seams import (
    Deadband,
    HANDSHAKE_STATEMENT_V1,
    ReceiverJev,
    SEAM_CONTRACT_V1,
    SenderJev,
)

SENDER = "vessel-a"
RECEIVER = "vessel-b"
EXPECTED = 0.50
BAND = 0.15
# Receiver policy (phase 2 is a choice, not an automation): contracts
# this receiver holds standing orders to settle under.
SETTLE_CONTRACTS = {"route-plan@1", "cargo-manifest@1"}
CHARTS = b"marine-chart-tile-blob-0001" * 8  # payload carried as bytes
MANIFEST = {"manifest": ["bol-001", "bol-002", "bol-003"]}

# v0.3: the seam's contract family and the pinned v1 statement. The
# version each side stamps is derived from the statement — H1 is a
# content hash, not the "1.2" label of the v0.1 path.
SEAM_FAMILY = "beam-quilt/seam-handshake"
BASE_STATEMENT = HANDSHAKE_STATEMENT_V1
H1 = SEAM_CONTRACT_V1.version
# The revision the versioning scenario proposes: one clause of the
# statement changes (the pool tie-break becomes explicit), so H2 is a
# new content hash of the same contract family.
REVISION_STATEMENT_V2 = dict(
    HANDSHAKE_STATEMENT_V1,
    pool_rule=(
        "the comparative pick is the best merit in the pool; ties are "
        "broken by earliest arrival; a decided transmission id never "
        "re-enters the pool"
    ),
)
H2 = statement_version(REVISION_STATEMENT_V2)
# A third statement that never crosses the seam: its hash is a citation
# the receiver cannot honour — not wrong, just never proposed (§2.5).
NEVER_CROSSED_VERSION = statement_version(
    dict(HANDSHAKE_STATEMENT_V1, pool_rule="a third statement that never crossed")
)


def scale() -> float:
    return float(os.environ.get("DEMO_TIME_SCALE", "1.0"))


def yaw() -> float:
    return 1.0 * scale()


def pool_ttl() -> float:
    return 0.5 * scale()


# ------------------------------------------------------------------ sender

def make_envelope(payload: dict, type_: str, cost: float, jev: SenderJev) -> TransmissionEnvelope:
    env = TransmissionEnvelope(payload=payload, type=type_, cost_budget=cost)
    env.sender_profile = jev.project(env)
    return env


def run_sender(work: Path, scenario: str = "all") -> int:
    s = scale()
    inbox = work / "inbox"
    responses = work / "responses"
    inbox.mkdir(exist_ok=True)
    responses.mkdir(exist_ok=True)
    book = StateStore(SENDER, work / "sender_book.jsonl")
    jev = SenderJev(field_expectations={"telemetry": ["a", "b"]})
    seq = 0
    seen_responses: set[str] = set()
    sent_ids: list[str] = []
    payload_offers: dict[str, TransmissionEnvelope] = {}  # v0.2: id -> offer

    def seam_now() -> str:
        # The version of the seam contract this book currently holds —
        # a fold of its own revision records, never a shared constant.
        return current_version(book, BASE_STATEMENT, contract_id=SEAM_FAMILY)

    def applied_now(env: TransmissionEnvelope) -> str | None:
        state, held = seam_context(book, BASE_STATEMENT, contract_id=SEAM_FAMILY)
        return resolve_citation(
            env.contract_version, state=state, held=held
        ).applied_version

    def send(env: TransmissionEnvelope, *, cite: str | None = None) -> None:
        """Drop one envelope over the seam, citing the sender's current
        version (or an explicit override — the stale-citation probes).

        The version travels in the envelope (§6.1); the sender's book
        records what it cited, a fact about the sender."""
        nonlocal seq
        env.contract_id = SEAM_FAMILY
        env.contract_version = cite if cite is not None else seam_now()
        book_seam_send(book, env, RECEIVER)
        if env.transmission_id not in sent_ids:
            sent_ids.append(env.transmission_id)
        seq += 1
        (inbox / f"{seq:04d}-{env.transmission_id}.json").write_text(env.to_json())

    def book_receive(
        env: TransmissionEnvelope, detail: dict | None = None
    ) -> None:
        """Book a response crossing arriving back, honouring the version
        it cites against this book's own fold (§2.5)."""
        state, held = seam_context(book, BASE_STATEMENT, contract_id=SEAM_FAMILY)
        ruling = resolve_citation(env.contract_version, state=state, held=held)
        book_seam_receive(book, env, RECEIVER, ruling=ruling, detail=detail)

    def poll_responses() -> None:
        for f in sorted(responses.glob("*.json")):
            if f.name in seen_responses:
                continue
            seen_responses.add(f.name)
            env = TransmissionEnvelope.from_json(f.read_text())
            if env.type == ADOPTION_ACK_TYPE:
                # The peer answered the revision proposal. Booking this
                # ack is the third booking of the adoption cycle; only
                # now does this book adopt (§2.3) — the revision
                # governs when *both* books hold it.
                entry, record = receive_ack(
                    book, env, RECEIVER, base_statement=BASE_STATEMENT
                )
                book.log_event(
                    {
                        "event": "adoption_ack_received",
                        "version": env.payload.get("version"),
                        "position": env.payload.get("position"),
                        "sender_now_on": seam_now(),
                    }
                )
            elif env.type.endswith("/refusal") or env.type.endswith("/narrow"):
                book_receive(env)
                if env.type.endswith("/refusal"):
                    book.log_event({"event": "refusal_received", "profile": env.payload})
                else:
                    book.log_event({"event": "clarify_received", "question": env.payload})
            elif env.type == RECEIPT_TYPE:
                # Phase-1 evidence from the far side: book the receipt,
                # then (policy) consummate the transfer with SETTLE —
                # under the seam version in force now, which is what a
                # settlement records (§4.3).
                book_receipt_receive(
                    book, env, RECEIVER,
                    contract_id=SEAM_FAMILY, contract_version=applied_now(env),
                )
                offer = payload_offers.get(env.payload.get("about"))
                if (
                    offer is not None
                    and env.payload.get("payload_hash") == offer.hash
                    and offer.contract in SETTLE_CONTRACTS
                ):
                    book_settle(
                        book, offer, RECEIVER,
                        contract_id=SEAM_FAMILY, contract_version=seam_now(),
                    )
            elif env.type == DISPUTE_TYPE:
                book_dispute_receive(
                    book, env, RECEIVER,
                    contract_id=SEAM_FAMILY, contract_version=applied_now(env),
                )
                book.log_event(
                    {
                        "event": "payload_dispute_received",
                        "about": env.payload.get("about"),
                        "dispute": env.payload,
                    }
                )
            else:
                book_receive(env)

    def wait(dur: float) -> None:
        end = time.monotonic() + dur
        while time.monotonic() < end:
            poll_responses()
            time.sleep(0.05)

    def refresh(env: TransmissionEnvelope, dur: float) -> None:
        end = time.monotonic() + dur
        while time.monotonic() < end:
            send(env.refreshed())
            wait(0.25 * s)

    def drop_offer(env: TransmissionEnvelope, *, tamper: bool = False) -> None:
        """Book SEND(offer) and drop the envelope file (v0.2 phase 1).

        v0.3: the offer's envelope carries the seam citation too — the
        payload's own governing contract rides in ``contract`` (v0.2),
        the seam contract's version in the citation pair. ``tamper``
        simulates corruption in transit: the file carries a mutated
        payload while keeping the sender's declared hash and size, so
        the sender's book records what it *sent*, not what arrived.
        """
        nonlocal seq
        env.contract_id = SEAM_FAMILY
        env.contract_version = seam_now()
        book_offer_send(
            book, env, RECEIVER,
            contract_id=SEAM_FAMILY, contract_version=env.contract_version,
        )
        if env.transmission_id not in sent_ids:
            sent_ids.append(env.transmission_id)
        payload_offers[env.transmission_id] = env
        d = json.loads(env.to_json())
        if tamper:
            content = d["payload"]["content"]
            if isinstance(content, dict) and isinstance(content.get("manifest"), list):
                content["manifest"][-1] = f"{content['manifest'][-1]}-TAMPERED"
            else:
                d["payload"]["content"] = content + "XX=="
        seq += 1
        (inbox / f"{seq:04d}-{env.transmission_id}.json").write_text(
            json.dumps(d, sort_keys=True)
        )

    def drop_proposal() -> None:
        """V2: propose the revision mid-run. The proposal cites H1 —
        the version in force when it is made — and carries the whole
        new statement in its payload (§2.1). The version it *proposes*
        rides inside; the two must not be conflated."""
        nonlocal seq
        proposal = make_revision_proposal(
            REVISION_STATEMENT_V2,
            contract_id=SEAM_FAMILY,
            supersedes=seam_now(),
            cited_version=seam_now(),
            label="v2",
        )
        send_proposal(book, proposal, RECEIVER, proposed_by=SENDER)
        if proposal.transmission_id not in sent_ids:
            sent_ids.append(proposal.transmission_id)
        seq += 1
        (inbox / f"{seq:04d}-{proposal.transmission_id}.json").write_text(
            proposal.to_json()
        )

    hang = os.environ.get("DEMO_SENDER_HANG") == "1"

    if scenario in ("proposals", "all"):
        # A — inside the band (Hold); profile overstates completeness.
        a = make_envelope({"merit": 0.55, "a": 1, "b": 2}, "telemetry", 3.0, jev)
        send(a)
        if hang:
            # Killed mid-run by the harness: D has crossed and its excursion
            # is still open; nothing will ever resolve it from this side.
            d = make_envelope({"merit": 0.97, "proposal": "hold-course"}, "course-proposal", 2.0, jev)
            send(d)
            book.log_event({"event": "sender_hanging", "last_send": d.transmission_id})
            time.sleep(60)
            return 0
        refresh(a, 1.0 * s)
        # B — one excursion that goes stale inside the yaw-time.
        b = make_envelope({"merit": 0.95, "proposal": "hard-turn"}, "course-proposal", 2.0, jev)
        send(b)
        wait(1.1 * s)
        # D — persistent excursion, admissible: Act(accept-best). The
        # refresh window (1.6) ends, and the post-Act re-assertions go
        # stale, before a second excursion could persist for a full yaw.
        d = make_envelope({"merit": 0.97, "proposal": "hold-course"}, "course-proposal", 2.0, jev)
        send(d)
        refresh(d, 1.6 * s)
        wait(0.4 * s)
        # E — persistent excursion, weak pick: Act(clarify/narrow).
        e = make_envelope({"merit": 0.10, "proposal": "vague"}, "course-proposal", 2.0, jev)
        send(e)
        refresh(e, 1.6 * s)
        wait(0.4 * s)
        # F — persistent excursion, inadmissible pick: Act(refuse).
        fenv = make_envelope({"merit": -0.20, "proposal": "impossible"}, "course-proposal", 2.0, jev)
        send(fenv)
        refresh(fenv, 1.6 * s)
        wait(0.8 * s)
        poll_responses()

    if scenario in ("payload", "all"):
        # v0.2 payload scenario. P1 settles end-to-end; P2 is offered
        # speculatively and left open on both sides; P3 is tampered in
        # transit and disputed; P3b re-sends the true manifest under a
        # NEW transmission id and settles cleanly.
        p1 = make_payload_offer(
            {"waypoints": [[61.1, 6.6], [62.0, 6.2]], "eta_min": 42},
            contract="route-plan@1",
            ending=Ending.COPY,
            copy_authorized_by="fleet-owner-standing-order-3",
            cost_budget=1.0,
        )
        drop_offer(p1)
        p2 = make_payload_offer(
            CHARTS,
            contract="charts@2",
            ending=Ending.COPY,
            copy_authorized_by="fleet-owner-standing-order-3",
            cost_budget=1.0,
        )
        drop_offer(p2)
        p3 = make_payload_offer(
            MANIFEST,
            contract="cargo-manifest@1",
            ending=Ending.COPY,
            copy_authorized_by="fleet-owner-standing-order-3",
            cost_budget=1.0,
        )
        drop_offer(p3, tamper=True)
        wait(2.5 * s)  # bounded: receipts and the dispute cross back
        # The corrected payload is a NEW transmission (same true
        # content, new id): the disputed id is dead forever.
        p3b = make_payload_offer(
            MANIFEST,
            contract="cargo-manifest@1",
            ending=Ending.COPY,
            copy_authorized_by="fleet-owner-standing-order-3",
            cost_budget=1.0,
        )
        drop_offer(p3b)
        wait(2.5 * s)
        poll_responses()
        book.log_event(
            {
                "event": "session_end",
                "role": SENDER,
                "phase": "payload",
                "sends": sent_ids,
                "payload_settled": sorted(settled_payloads(book.ledger)),
                "payload_open": open_offers(book.ledger),
                "payload_disputed": [d["transmission_id"] for d in disputes(book.ledger)],
            }
        )
        if scenario == "payload":
            (work / "done.flag").write_text("done")
            return 0

    if scenario in ("versioning", "all"):
        # v0.3 versioning scenario — see the module docstring (V0–V6).
        # V0: a payload settled under v1. When v2 lands, this stands.
        v0 = make_payload_offer(
            {"route": "settled-under-v1", "stands": "when v2 lands"},
            contract="route-plan@1",
            ending=Ending.COPY,
            copy_authorized_by="fleet-owner-standing-order-3",
            cost_budget=1.0,
        )
        drop_offer(v0)
        wait(2.5 * s)
        # V1: a crossing under v1 — agreed cleanly, both books stamp H1.
        send(make_envelope({"merit": 0.5, "note": "clean v1 crossing"}, "version-probe/v1", 1.0, jev))
        wait(1.0 * s)
        # V2: propose the revision (cites H1). V3: one breath later, a
        # crossing in flight citing H1 — the sender cannot know of any
        # adoption yet (there is none bilaterally). File order
        # guarantees the receiver books the proposal, and adopts, before
        # the in-flight crossing lands: skewed adoption, honoured cite.
        drop_proposal()
        send(make_envelope({"merit": 0.5, "note": "in flight across adoption"}, "version-probe/in-flight", 1.0, jev))
        wait(2.5 * s)  # the adoption ack crosses back; the sender adopts
        # V4: both books now hold v2 — a clean v2 crossing.
        send(make_envelope({"merit": 0.5, "note": "clean v2 crossing"}, "version-probe/v2", 1.0, jev))
        wait(1.0 * s)
        # V5: a stale v1 citation (a sender restarted with the old
        # statement): honoured under the cited version, never upgraded.
        send(
            make_envelope({"merit": 0.5, "note": "stale v1 citation"}, "version-probe/stale-v1", 1.0, jev),
            cite=H1,
        )
        wait(1.0 * s)
        # V6: a citation of a statement that never crossed — the
        # receiver fails closed and asks for the statement.
        send(
            make_envelope({"merit": 0.5, "note": "never proposed"}, "version-probe/unknown", 1.0, jev),
            cite=NEVER_CROSSED_VERSION,
        )
        wait(2.5 * s)
        poll_responses()
        book.log_event(
            {
                "event": "session_end",
                "role": SENDER,
                "phase": "versioning",
                "sends": sent_ids,
                "seam_version_now": seam_now(),
                "settled_under_v1_standing": sorted(
                    tid
                    for tid, rec in settled_payloads(book.ledger).items()
                    if rec.get("contract_version") == H1
                ),
            }
        )
        (work / "done.flag").write_text("done")
        return 0

    poll_responses()
    book.log_event({"event": "session_end", "role": SENDER, "sends": sent_ids})
    (work / "done.flag").write_text("done")
    return 0


# ------------------------------------------------------------------ receiver

def run_receiver(work: Path, until: float | None) -> int:
    s = scale()
    inbox = work / "inbox"
    responses = work / "responses"
    inbox.mkdir(exist_ok=True)
    responses.mkdir(exist_ok=True)
    book = StateStore(RECEIVER, work / "receiver_book.jsonl")
    seam_path = work / "seam_log.jsonl"

    def seam(event: dict) -> None:
        event = {"at": time.time(), **event}
        with seam_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")

    receiver = ReceiverJev(
        Deadband(band=BAND, yaw_time=yaw(), context="open-water"),
        expected_course=EXPECTED,
        required_fields={"telemetry": ["a", "b", "c"]},
        pool_ttl=pool_ttl(),
    )
    seen_files: set[str] = set()
    first_seen: set[str] = set()
    last_decision: dict[str, str] = {}
    resp_seq = 0
    start = time.monotonic()
    done_at: float | None = None

    def write_response(env: TransmissionEnvelope, in_reply_to: str) -> None:
        """A response crossing back, citing the receiver's current
        version of the seam contract (§6.7)."""
        nonlocal resp_seq
        env.contract_id = SEAM_FAMILY
        env.contract_version = current_version(book, BASE_STATEMENT, contract_id=SEAM_FAMILY)
        book_seam_send(book, env, SENDER, detail={"in_reply_to": in_reply_to})
        resp_seq += 1
        (responses / f"{resp_seq:04d}-{env.transmission_id}.json").write_text(env.to_json())

    def handle_revision(text: str) -> None:
        """v0.3: a contract revision proposed over the seam (§2.3).

        Receipt is not adoption. The crossing is booked honouring the
        version it cites (H1); the book's *position* on the proposed
        version is a separate record — adopted here by policy, which is
        local: the seam is still bilaterally on v1 until the proposer
        books the ack (§2.4). The ack crosses back citing the version
        the proposal cited."""
        env = TransmissionEnvelope.from_json(text)
        entry, record, ack = receive_proposal(
            book, env, SENDER, base_statement=BASE_STATEMENT
        )
        write_response(ack, env.transmission_id)
        seam(
            {
                "event": "revision_received",
                "transmission_id": env.transmission_id,
                "contract_id": env.payload.get("contract_id"),
                "version": env.payload.get("version"),
                "label": env.payload.get("label"),
                "supersedes": env.payload.get("supersedes"),
                "position": record.position,
                "reason": record.reason,
                "cited_version": env.contract_version,
                "applied_version": entry.contract_version,
            }
        )
        book.log_event(
            {
                "event": "revision_position_booked",
                "version": env.payload.get("version"),
                "position": record.position,
                "receiver_now_on": current_version(book, BASE_STATEMENT, contract_id=SEAM_FAMILY),
            }
        )

    def handle_payload(text: str) -> None:
        """v0.2: an actual payload transfer, two phases, dispute path.

        Phase 1 ends here: verify hash + declared size against the
        envelope, book RECEIVE(offer) or RECEIVE-DISPUTED, and answer
        with a receipt or a dispute crossing. Phase 2 (SETTLE) is this
        receiver's own policy choice — a verified offer settles only
        under a contract it holds a standing order for. v0.3: each
        booking carries the seam citation, resolved per §3.1 — the
        version *this receiver applied* to the crossing."""
        reading = read_offer(text)
        env = reading.envelope
        state, held = seam_context(book, BASE_STATEMENT, contract_id=SEAM_FAMILY)
        ruling = resolve_citation(env.contract_version, state=state, held=held)
        if reading.ok:
            book_offer_receive(
                book, reading, SENDER,
                contract_id=SEAM_FAMILY, contract_version=ruling.applied_version,
            )
            receipt = make_receipt(reading)
            receipt.contract_id = SEAM_FAMILY
            receipt.contract_version = ruling.applied_version
            book_receipt_send(
                book, receipt, reading, SENDER,
                contract_id=SEAM_FAMILY, contract_version=ruling.applied_version,
            )
            nonlocal resp_seq
            resp_seq += 1
            (responses / f"{resp_seq:04d}-{receipt.transmission_id}.json").write_text(
                receipt.to_json()
            )
            if env.contract in SETTLE_CONTRACTS:
                book_settle(
                    book, env, SENDER,
                    contract_id=SEAM_FAMILY, contract_version=ruling.applied_version,
                )
            seam(
                {
                    "event": "payload_received",
                    "transmission_id": env.transmission_id,
                    "payload_hash": env.hash,
                    "contract": env.contract,
                    "size": env.payload_size,
                    "settled": env.contract in SETTLE_CONTRACTS,
                    "cited_version": env.contract_version,
                    "applied_version": ruling.applied_version,
                    "superseded_citation": ruling.superseded_citation,
                }
            )
        else:
            book_disputed_receive(
                book, reading, SENDER,
                contract_id=SEAM_FAMILY, contract_version=ruling.applied_version,
            )
            dispute = make_dispute(reading)
            dispute.contract_id = SEAM_FAMILY
            dispute.contract_version = ruling.applied_version
            book_dispute_send(
                book, dispute, reading, SENDER,
                contract_id=SEAM_FAMILY, contract_version=ruling.applied_version,
            )
            resp_seq += 1
            (responses / f"{resp_seq:04d}-{dispute.transmission_id}.json").write_text(
                dispute.to_json()
            )
            seam(
                {
                    "event": "payload_disputed",
                    "transmission_id": env.transmission_id,
                    "declared_hash": reading.declared_hash,
                    "computed_hash": reading.computed_hash,
                    "declared_size": reading.declared_size,
                    "measured_size": reading.measured_size,
                }
            )

    while True:
        now = time.monotonic()
        arrived: list[TransmissionEnvelope] = []
        for f in sorted(inbox.glob("*.json")):
            if f.name in seen_files:
                continue
            seen_files.add(f.name)
            text = f.read_text()
            try:
                d = json.loads(text)
            except json.JSONDecodeError:
                seam({"event": "unreadable_envelope", "file": f.name})
                continue
            if d.get("type") == PAYLOAD_TYPE:
                try:
                    handle_payload(text)
                except ValueError:
                    seam({"event": "unreadable_envelope", "file": f.name})
                continue
            if d.get("type") == REVISION_TYPE:
                try:
                    handle_revision(text)
                except ValueError:
                    seam({"event": "unreadable_envelope", "file": f.name})
                continue
            try:
                arrived.append(TransmissionEnvelope.from_dict(d))
            except ValueError:
                seam({"event": "unreadable_envelope", "file": f.name})
        decision = None
        for env in arrived:
            # v0.3: the receiver stamps the version *it applied* —
            # honouring the citation, flagging superseded ones,
            # refusing revoked/unknown ones (§2.5, §3.1). Agreement on
            # the version is then a fact about two bookings, not a
            # shared constant.
            state, held = seam_context(book, BASE_STATEMENT, contract_id=SEAM_FAMILY)
            ruling = resolve_citation(env.contract_version, state=state, held=held)
            entry = book_seam_receive(book, env, SENDER, ruling=ruling)
            book.record_reading(
                cell=SENDER,
                value={"transmission_id": env.transmission_id, "type": env.type},
                note="candidate received",
            )
            decision = receiver.evaluate(env)
            if env.transmission_id not in first_seen:
                first_seen.add(env.transmission_id)
                seam(
                    {
                        "event": "crossing",
                        "transmission_id": env.transmission_id,
                        "payload_type": env.type,
                        "profile": env.sender_profile,
                        "pick": decision.pick_id,
                        "ending": env.ending.value,
                        "cost": env.cost_budget,
                        "contract_id": env.contract_id,
                        "cited_version": env.contract_version,
                        "applied_version": entry.contract_version,
                        "superseded_citation": ruling.superseded_citation,
                    }
                )
                if ruling.superseded_citation:
                    # The skew, in the seam log, verbatim: this crossing
                    # was governed by a version the receiver has moved
                    # past — honoured, never re-judged (§2.5, §3.1).
                    seam(
                        {
                            "event": "superseded_citation",
                            "transmission_id": env.transmission_id,
                            "cited_version": env.contract_version,
                            "applied_version": entry.contract_version,
                            "receiver_current": state["current"],
                        }
                    )
                if ruling.divergence:
                    # The two books will disagree about the version: a
                    # first-class event, never averaged away (§3.4).
                    seam(
                        {
                            "event": "version_divergence",
                            "transmission_id": env.transmission_id,
                            "cited_version": env.contract_version,
                            "applied_version": entry.contract_version,
                            "citation_refusal": ruling.citation_refusal,
                        }
                    )
                    if ruling.citation_refusal == "statement not held":
                        request = make_statement_request(
                            env, contract_id=SEAM_FAMILY,
                            missing_version=env.contract_version or "",
                        )
                        write_response(request, env.transmission_id)
        if not arrived:
            decision = receiver.evaluate(None)
        for rec in receiver.disagreements:
            seam(
                {
                    "event": "disagreement",
                    "transmission_id": rec.transmission_id,
                    "sender_profile": rec.sender_profile,
                    "receiver_measurement": rec.receiver_measurement,
                    "divergence": rec.divergence,
                }
            )
        receiver.disagreements.clear()
        if decision is not None:
            key = f"{decision.kind}:{decision.pick_id}"
            prev = last_decision.get("current")
            if key != prev:
                last_decision["current"] = key
                entry = {
                    "event": "decision",
                    "kind": decision.kind,
                    "pick": decision.pick_id,
                    "deviation": round(decision.deviation, 4),
                }
                if decision.kind == "act":
                    entry["response"] = decision.response.kind
                    if decision.response.kind == "clarify_narrow":
                        write_response(decision.response.question, decision.pick_id)
                    elif decision.response.kind == "refuse":
                        refusal = TransmissionEnvelope(
                            payload=decision.response.profile,
                            type="course-proposal/refusal",
                            ending=Ending.PROJECT,
                            cost_budget=0.0,
                        )
                        write_response(refusal, decision.pick_id)
                    elif decision.response.kind == "accept_best":
                        seam({"event": "course_adjusted", "pick": decision.pick_id})
                seam(entry)
            if decision.pick_id:
                last_decision[decision.pick_id] = decision.kind
        if (work / "done.flag").exists():
            if done_at is None:
                done_at = now
            elif now - done_at > 0.8 * s:
                break
        if until is not None and now - start > until:
            break
        time.sleep(0.05)

    unresolved = sorted(
        tid for tid, kind in last_decision.items() if kind == "hold_watch"
    )
    book.log_event(
        {
            "event": "session_end",
            "role": RECEIVER,
            "received": sorted(first_seen),
            "unresolved_excursions": unresolved,
            "seam_version_now": current_version(book, BASE_STATEMENT, contract_id=SEAM_FAMILY),
            "revisions": [
                {
                    "version": r.version,
                    "label": r.label,
                    "position": r.position,
                    "reason": r.reason,
                }
                for r in book.revisions
            ],
        }
    )
    return 0


# ------------------------------------------------------------------ orchestrator

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def verify_payload(sender: StateStore, receiver: StateStore, scenario: str) -> list[str]:
    """v0.2 acceptance over the two books: settle / open / dispute."""
    problems: list[str] = []
    s_settled = settled_payloads(sender.ledger)
    r_settled = settled_payloads(receiver.ledger)
    # In the full run the versioning scenario adds one more settled
    # payload (V0, settled under v1 and standing after v2 lands).
    want_settled = 3 if scenario == "all" else 2
    if len(s_settled) != want_settled or set(s_settled) != set(r_settled):
        problems.append(
            f"settled views disagree: sender {sorted(s_settled)} vs "
            f"receiver {sorted(r_settled)} (want exactly the route plan, "
            "the corrected manifest, and — in the full run — the "
            "settled-under-v1 probe)"
        )
    for tid, rec in s_settled.items():
        if tid in r_settled and r_settled[tid].get("content") != rec.get("content"):
            problems.append(f"settled payload {tid} differs across the books")
    s_open, r_open = open_offers(sender.ledger), open_offers(receiver.ledger)
    if len(s_open) != 2 or s_open != r_open:
        problems.append(
            f"open offers disagree: sender {s_open} vs receiver {r_open} "
            "(want exactly the chart snapshot and the tampered manifest, "
            "open in both books)"
        )
    r_disp = [
        d for d in disputes(receiver.ledger) if d["seen_as"] == "receive_disputed"
    ]
    s_disp = [
        d for d in disputes(sender.ledger) if d["seen_as"] == "dispute_response"
    ]
    if (
        len(s_disp) != 1
        or len(r_disp) != 1
        or s_disp[0]["transmission_id"] != r_disp[0]["transmission_id"]
    ):
        problems.append("the tampered payload's dispute is not visible in both books")
    settleable, blocked = settleable_crossings(sender.ledger, receiver.ledger)
    for tid in s_settled:
        if tid not in settleable:
            problems.append(f"settled payload {tid} is not settleable (broken agreement)")
    for d in r_disp:
        if d["transmission_id"] not in blocked:
            problems.append("the disputed payload is not blocked from settlement")
    return problems


def verify_versioning(
    sender: StateStore, receiver: StateStore, seam: list[dict]
) -> list[str]:
    """v0.3 acceptance over the two books + the seam log."""
    problems: list[str] = []
    # 1. Adoption required both books (§2.3): three bookings on each
    #    side, and both folds end on v2 with v1 superseded.
    s_state = contract_state(sender.revisions, H1, contract_id=SEAM_FAMILY)
    r_state = contract_state(receiver.revisions, H1, contract_id=SEAM_FAMILY)
    if s_state["current"] != H2:
        problems.append(
            f"sender's fold is not on v2 (got {s_state['current'][:12]}…)"
        )
    if r_state["current"] != H2:
        problems.append(
            f"receiver's fold is not on v2 (got {r_state['current'][:12]}…)"
        )
    if [r.position for r in sender.revisions if r.version == H2] != [
        "proposed",
        "adopted",
    ]:
        problems.append("sender never booked proposed -> adopted for the revision")
    if [r.position for r in receiver.revisions if r.version == H2] != ["adopted"]:
        problems.append("receiver never booked its adoption")
    if H1 not in s_state["superseded"] or H1 not in r_state["superseded"]:
        problems.append("v1 is not superseded in both folds")

    # 2. The crossing in flight across the adoption boundary: honoured
    #    under the version it cites, agreed, flagged (§2.5).
    inflight = [
        e for e in receiver.ledger if e.payload_type == "version-probe/in-flight"
    ]
    if len(inflight) != 1:
        problems.append("the in-flight probe crossing is missing")
    else:
        e = inflight[0]
        if e.contract_version != H1 or not e.detail.get("superseded_citation"):
            problems.append(
                "in-flight crossing not honoured under the cited v1 with the skew flagged"
            )
        s_send = [
            x
            for x in sender.ledger
            if x.transmission_id == e.transmission_id and x.side == "SEND"
        ]
        if not s_send or s_send[0].contract_version != H1:
            problems.append("sender's booking of the in-flight crossing does not cite v1")

    # 3. A clean v2 crossing once both books adopted.
    v2e = [e for e in receiver.ledger if e.payload_type == "version-probe/v2"]
    if len(v2e) != 1 or v2e[0].contract_version != H2 or v2e[0].detail.get(
        "superseded_citation"
    ):
        problems.append("post-adoption crossing not applied cleanly under v2")

    # 4. The stale v1 citation after adoption: honoured under the cited
    #    version, logged verbatim, never silently upgraded (§3.1).
    stale = [e for e in receiver.ledger if e.payload_type == "version-probe/stale-v1"]
    if len(stale) != 1 or stale[0].contract_version != H1 or not stale[
        0
    ].detail.get("superseded_citation"):
        problems.append(
            "superseded citation was not honoured under the cited version (upgraded?)"
        )

    # 5. The unknown citation: refused, skewed (not plain unagreed),
    #    divergence logged, statement requested back (§2.5, §6.6).
    unk = [e for e in receiver.ledger if e.payload_type == "version-probe/unknown"]
    if len(unk) != 1:
        problems.append("the unknown-version probe crossing is missing")
    else:
        e = unk[0]
        if e.contract_version is not None or e.detail.get(
            "citation_refusal"
        ) != "statement not held":
            problems.append(
                "unknown-version citation not refused with 'statement not held'"
            )
        if not any(
            ev.get("event") == "version_divergence"
            and ev.get("transmission_id") == e.transmission_id
            for ev in seam
        ):
            problems.append("the refused citation's version divergence was not logged")
        if not any(
            x.payload_type == "version-probe/unknown/narrow" for x in sender.ledger
        ):
            problems.append("the statement request never crossed back to the sender")

    # 6. Settlements under v1 stand after v2 lands (§4.3): no re-fold.
    s_settled = settled_payloads(sender.ledger)
    r_settled = settled_payloads(receiver.ledger)
    v1_settled = [
        tid for tid, rec in r_settled.items() if rec.get("contract_version") == H1
    ]
    if not v1_settled:
        problems.append("no settlement recorded under v1")
    for tid in v1_settled:
        if tid not in s_settled or s_settled[tid].get("contract_version") != H1:
            problems.append(f"the v1 settlement {tid} does not stand in both books")
    rep = version_report(receiver.ledger, receiver.revisions, H1, contract_id=SEAM_FAMILY)
    if rep["current"] != H2:
        problems.append("version_report does not show v2 in force now")
    if not set(v1_settled) <= set(rep["settlements_under_superseded"]):
        problems.append("v1 settlements not reported as under a superseded version")

    # 7. The seam log showed the skew, verbatim.
    if not any(ev.get("event") == "superseded_citation" for ev in seam):
        problems.append("the seam log never showed the superseded citation")
    if not any(ev.get("event") == "revision_received" for ev in seam):
        problems.append("the revision proposal never appeared at the seam")

    # 8. Categories: the refused citation is skewed, everything else
    #    that agreed is agreed (agreement includes the version, §6.6).
    agreed, skewed, unagreed = agreed_crossings(sender.ledger, receiver.ledger)
    if unk:
        if unk[0].transmission_id not in skewed:
            problems.append("the refused-citation crossing is not in the skewed category")
    for tid in skewed:
        e = next(
            (x for x in receiver.ledger if x.transmission_id == tid and x.side == "RECEIVE"),
            None,
        )
        if e is None or not e.detail.get("citation_refusal"):
            problems.append(f"skewed crossing {tid} has no recorded citation refusal")
    return problems


def verify(work: Path, scenario: str = "all") -> list[str]:
    problems: list[str] = []
    seam = read_jsonl(work / "seam_log.jsonl")
    sender = StateStore.load(SENDER, work / "sender_book.jsonl")
    receiver = StateStore.load(RECEIVER, work / "receiver_book.jsonl")

    if scenario in ("proposals", "all"):
        kinds = [e.get("kind") for e in seam if e.get("event") == "decision"]
        acts = [
            (e.get("response"), e.get("pick"))
            for e in seam
            if e.get("event") == "decision" and e.get("kind") == "act"
        ]
        if "hold" not in kinds:
            problems.append("no Hold exhibited")
        if "hold_watch" in kinds:
            i = kinds.index("hold_watch")
            if "hold" not in kinds[i + 1:] and "act" not in kinds[i + 1:]:
                problems.append("HoldWatch never resolved")
            # The first HoldWatch (B) must resolve to Hold, not Act.
            nxt = next((k for k in kinds[i + 1:] if k in ("hold", "act")), None)
            if nxt != "hold":
                problems.append(f"transient excursion resolved as {nxt}, expected hold")
        else:
            problems.append("no HoldWatch exhibited")
        responses = [r for r, _ in acts]
        for want in ("accept_best", "clarify_narrow", "refuse"):
            if want not in responses:
                problems.append(f"no Act({want}) exhibited")
        if not any(e.get("event") == "disagreement" for e in seam):
            problems.append("no disagreement record at the seam")
        crossings = [e for e in seam if e.get("event") == "crossing"]
        for c in crossings:
            for fieldname in ("payload_type", "profile", "pick", "ending", "cost"):
                if fieldname not in c:
                    problems.append(f"crossing {c.get('transmission_id')} missing {fieldname}")
        if len(agreed_crossings(sender.ledger, receiver.ledger)[0]) < 6:
            problems.append("only few agreed crossings (A,B,D,E,F + 2 responses = 7)")
        sev = [e for e in read_jsonl(work / "sender_book.jsonl") if e.get("kind") == "event"]
        if not any(e.get("event") == "refusal_received" for e in sev):
            problems.append("sender never received the refusal profile")
        if not any(e.get("event") == "clarify_received" for e in sev):
            problems.append("sender never received the clarify question")

    if scenario in ("payload", "all"):
        problems += verify_payload(sender, receiver, scenario)

    if scenario in ("versioning", "all"):
        problems += verify_versioning(sender, receiver, seam)

    # Gap 2 across the whole session: the only crossings allowed to be
    # unagreed are payload offers whose dispute is on record in both
    # books (the tampered one). Everything else must match. v0.3: the
    # only crossings allowed to be *skewed* (matched id + hash,
    # mismatched version) are ones the receiver refused for cause —
    # a citation of a version it could not apply (§6.6).
    agreed, skewed, unagreed = agreed_crossings(sender.ledger, receiver.ledger)
    disputed_ids = {
        d["transmission_id"]
        for d in disputes(sender.ledger) + disputes(receiver.ledger)
    }
    if set(unagreed) != (disputed_ids & set(unagreed)):
        problems.append(f"unagreed crossings beyond the recorded disputes: {unagreed}")
    refused_ids = {
        e.transmission_id
        for e in receiver.ledger
        if e.detail.get("citation_refusal")
    }
    if set(skewed) != (refused_ids & set(skewed)):
        problems.append(
            f"skewed crossings beyond recorded citation refusals: {skewed}"
        )
    if scenario in ("proposals", "payload") and skewed:
        problems.append(f"unexpected skewed crossings: {skewed}")
    # Main's corrected arithmetic (92d2527) is kept in the proposals
    # verifier above, whose agreed-crossing check now reads
    # "(A,B,D,E,F + 2 responses = 7)"; the tail len/sev checks from
    # main are superseded here because v0.2 moved them into that
    # verifier (payload-only runs must not require proposal crossings).
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["sender", "receiver"], default=None)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--until", type=float, default=None)
    ap.add_argument(
        "--scenario",
        choices=["proposals", "payload", "versioning", "all"],
        default="all",
        help="proposals (v0.1 deadband demo), payload (v0.2 transfer "
        "agreement), versioning (v0.3 contract versioning), or all "
        "(default: proposals, then payload, then versioning)",
    )
    args = ap.parse_args()
    work = Path(args.dir)
    work.mkdir(parents=True, exist_ok=True)

    if args.role == "sender":
        return run_sender(work, args.scenario)
    if args.role == "receiver":
        return run_receiver(work, args.until)

    here = Path(__file__).resolve()
    recv = subprocess.Popen(
        [sys.executable, str(here), "--role", "receiver", "--dir", str(work)]
    )
    time.sleep(0.3)
    send = subprocess.Popen(
        [
            sys.executable, str(here), "--role", "sender",
            "--dir", str(work), "--scenario", args.scenario,
        ]
    )
    send.wait(timeout=180)
    recv.wait(timeout=180)
    problems = verify(work, args.scenario)
    seam = read_jsonl(work / "seam_log.jsonl")
    print(f"demo workdir: {work}")
    for e in seam:
        if e.get("event") in (
            "decision", "disagreement", "course_adjusted",
            "payload_received", "payload_disputed",
            "revision_received", "superseded_citation", "version_divergence",
        ):
            print(" ", e)
    if problems:
        print("DEMO VERIFICATION FAILED:")
        for p in problems:
            print(" -", p)
        return 1
    print("DEMO OK: Hold, HoldWatch->Hold, Act(accept-best), Act(clarify), "
          "Act(refuse), disagreement, both-sides agreement all exhibited.")
    if args.scenario in ("payload", "all"):
        print("PAYLOAD OK: offered+settled end-to-end; offered+left-open in "
              "both books; tampered payload disputed and never settled; "
              "corrected payload settled under a NEW transmission id.")
    if args.scenario in ("versioning", "all"):
        print("VERSIONING OK: revision proposed mid-run and adopted by the "
              "receiver before the sender (skewed adoption, ack completes "
              "it); a crossing in flight across the boundary honoured under "
              "its cited v1 and flagged; stale v1 citation honoured, never "
              "upgraded; unknown citation refused, skewed distinctly, "
              "statement requested back; the v1 settlement stands after v2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
