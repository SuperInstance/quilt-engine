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
``--scenario proposals`` (below), ``--scenario payload`` (v0.2), or
``--scenario all`` (the default: proposals, then payload):

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

from quilt_engine.core import StateStore, TransmissionEnvelope, agreed_crossings
from quilt_engine.core.envelope import Ending
from quilt_engine.core.payload import (
    DISPUTE_TYPE,
    PAYLOAD_TYPE,
    RECEIPT_TYPE,
    book_dispute_receive,
    book_dispute_send,
    book_disputed_receive,
    book_offer_receive,
    book_offer_send,
    book_receipt_receive,
    book_receipt_send,
    book_settle,
    disputes,
    make_dispute,
    make_payload_offer,
    make_receipt,
    open_offers,
    read_offer,
    settleable_crossings,
    settled_payloads,
)
from quilt_engine.seams import Deadband, ReceiverJev, SenderJev
from quilt_engine.seams.pair import DEFAULT_CONTRACT_ID

SENDER = "vessel-a"
RECEIVER = "vessel-b"
EXPECTED = 0.50
BAND = 0.15
# Receiver policy (phase 2 is a choice, not an automation): contracts
# this receiver holds standing orders to settle under.
SETTLE_CONTRACTS = {"route-plan@1", "cargo-manifest@1"}
CHARTS = b"marine-chart-tile-blob-0001" * 8  # payload carried as bytes
MANIFEST = {"manifest": ["bol-001", "bol-002", "bol-003"]}


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

    def send(env: TransmissionEnvelope) -> None:
        nonlocal seq
        book.book(
            "SEND",
            transmission_id=env.transmission_id,
            peer=RECEIVER,
            payload_type=env.type,
            payload_hash=env.hash,
            ending=env.ending.value,
            cost=env.cost_budget,
            contract=DEFAULT_CONTRACT_ID,
        )
        if env.transmission_id not in sent_ids:
            sent_ids.append(env.transmission_id)
        seq += 1
        (inbox / f"{seq:04d}-{env.transmission_id}.json").write_text(env.to_json())

    def poll_responses() -> None:
        for f in sorted(responses.glob("*.json")):
            if f.name in seen_responses:
                continue
            seen_responses.add(f.name)
            env = TransmissionEnvelope.from_json(f.read_text())
            if env.type.endswith("/refusal") or env.type.endswith("/narrow"):
                book.book(
                    "RECEIVE",
                    transmission_id=env.transmission_id,
                    peer=RECEIVER,
                    payload_type=env.type,
                    payload_hash=env.hash,
                    ending=env.ending.value,
                    cost=env.cost_budget,
                    contract=DEFAULT_CONTRACT_ID,
                )
                if env.type.endswith("/refusal"):
                    book.log_event({"event": "refusal_received", "profile": env.payload})
                else:
                    book.log_event({"event": "clarify_received", "question": env.payload})
            elif env.type == RECEIPT_TYPE:
                # Phase-1 evidence from the far side: book the receipt,
                # then (policy) consummate the transfer with SETTLE.
                book_receipt_receive(book, env, RECEIVER)
                offer = payload_offers.get(env.payload.get("about"))
                if (
                    offer is not None
                    and env.payload.get("payload_hash") == offer.hash
                    and offer.contract in SETTLE_CONTRACTS
                ):
                    book_settle(book, offer, RECEIVER)
            elif env.type == DISPUTE_TYPE:
                book_dispute_receive(book, env, RECEIVER)
                book.log_event(
                    {
                        "event": "payload_dispute_received",
                        "about": env.payload.get("about"),
                        "dispute": env.payload,
                    }
                )
            else:
                book.book(
                    "RECEIVE",
                    transmission_id=env.transmission_id,
                    peer=RECEIVER,
                    payload_type=env.type,
                    payload_hash=env.hash,
                    ending=env.ending.value,
                    cost=env.cost_budget,
                    contract=DEFAULT_CONTRACT_ID,
                )

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

        ``tamper`` simulates corruption in transit: the file carries a
        mutated payload while keeping the sender's declared hash and
        size, so the sender's book records what it *sent*, not what
        arrived.
        """
        nonlocal seq
        book_offer_send(book, env, RECEIVER)
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
                "sends": sent_ids,
                "payload_settled": sorted(settled_payloads(book.ledger)),
                "payload_open": open_offers(book.ledger),
                "payload_disputed": [d["transmission_id"] for d in disputes(book.ledger)],
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
        nonlocal resp_seq
        book.book(
            "SEND",
            transmission_id=env.transmission_id,
            peer=SENDER,
            payload_type=env.type,
            payload_hash=env.hash,
            ending=env.ending.value,
            cost=env.cost_budget,
            contract=DEFAULT_CONTRACT_ID,
            detail={"in_reply_to": in_reply_to},
        )
        resp_seq += 1
        (responses / f"{resp_seq:04d}-{env.transmission_id}.json").write_text(env.to_json())

    def handle_payload(text: str) -> None:
        """v0.2: an actual payload transfer, two phases, dispute path.

        Phase 1 ends here: verify hash + declared size against the
        envelope, book RECEIVE(offer) or RECEIVE-DISPUTED, and answer
        with a receipt or a dispute crossing. Phase 2 (SETTLE) is this
        receiver's own policy choice — a verified offer settles only
        under a contract it holds a standing order for.
        """
        reading = read_offer(text)
        env = reading.envelope
        if reading.ok:
            book_offer_receive(book, reading, SENDER)
            receipt = make_receipt(reading)
            book_receipt_send(book, receipt, reading, SENDER)
            nonlocal resp_seq
            resp_seq += 1
            (responses / f"{resp_seq:04d}-{receipt.transmission_id}.json").write_text(
                receipt.to_json()
            )
            if env.contract in SETTLE_CONTRACTS:
                book_settle(book, env, SENDER)
            seam(
                {
                    "event": "payload_received",
                    "transmission_id": env.transmission_id,
                    "payload_hash": env.hash,
                    "contract": env.contract,
                    "size": env.payload_size,
                    "settled": env.contract in SETTLE_CONTRACTS,
                }
            )
        else:
            book_disputed_receive(book, reading, SENDER)
            dispute = make_dispute(reading)
            book_dispute_send(book, dispute, reading, SENDER)
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
            try:
                arrived.append(TransmissionEnvelope.from_dict(d))
            except ValueError:
                seam({"event": "unreadable_envelope", "file": f.name})
        decision = None
        for env in arrived:
            book.book(
                "RECEIVE",
                transmission_id=env.transmission_id,
                peer=SENDER,
                payload_type=env.type,
                payload_hash=env.hash,
                ending=env.ending.value,
                cost=env.cost_budget,
                contract=DEFAULT_CONTRACT_ID,
            )
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
                        "contract": DEFAULT_CONTRACT_ID,
                    }
                )
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
        }
    )
    return 0


# ------------------------------------------------------------------ orchestrator

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def verify_payload(sender: StateStore, receiver: StateStore) -> list[str]:
    """v0.2 acceptance over the two books: settle / open / dispute."""
    problems: list[str] = []
    s_settled = settled_payloads(sender.ledger)
    r_settled = settled_payloads(receiver.ledger)
    if len(s_settled) != 2 or set(s_settled) != set(r_settled):
        problems.append(
            f"settled views disagree: sender {sorted(s_settled)} vs "
            f"receiver {sorted(r_settled)} (want exactly the route plan "
            "and the corrected manifest)"
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
            problems.append("only few agreed crossings (A-F + 2 responses = 8)")
        sev = [e for e in read_jsonl(work / "sender_book.jsonl") if e.get("kind") == "event"]
        if not any(e.get("event") == "refusal_received" for e in sev):
            problems.append("sender never received the refusal profile")
        if not any(e.get("event") == "clarify_received" for e in sev):
            problems.append("sender never received the clarify question")

    if scenario in ("payload", "all"):
        problems += verify_payload(sender, receiver)

    # Gap 2 across the whole session: the only crossings allowed to be
    # unagreed are payload offers whose dispute is on record in both
    # books (the tampered one). Everything else must match.
    agreed, unagreed = agreed_crossings(sender.ledger, receiver.ledger)
    disputed_ids = {
        d["transmission_id"]
        for d in disputes(sender.ledger) + disputes(receiver.ledger)
    }
    if set(unagreed) != (disputed_ids & set(unagreed)):
        problems.append(f"unagreed crossings beyond the recorded disputes: {unagreed}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["sender", "receiver"], default=None)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--until", type=float, default=None)
    ap.add_argument(
        "--scenario",
        choices=["proposals", "payload", "all"],
        default="all",
        help="proposals (v0.1 deadband demo), payload (v0.2 transfer "
        "agreement), or all (default)",
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
    send.wait(timeout=120)
    recv.wait(timeout=120)
    problems = verify(work, args.scenario)
    seam = read_jsonl(work / "seam_log.jsonl")
    print(f"demo workdir: {work}")
    for e in seam:
        if e.get("event") in (
            "decision", "disagreement", "course_adjusted",
            "payload_received", "payload_disputed",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
