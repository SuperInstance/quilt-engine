"""Payload-transfer agreement — gap 2, deepened (v0.2).

v0's crossings were typed envelopes about *proposals in miniature*: a
SEND booked here, a RECEIVE booked there, and a crossing counts as
shared only where the two bookings match (``agreed_crossings``). This
module makes the same discipline carry an **actual payload transfer**,
in two phases, with a dispute path:

- **Offer (phase 1)** — the sender books ``SEND``(offer); the receiver,
  after checking the payload hash and the declared size *against the
  envelope*, books ``RECEIVE``(offer) — or ``RECEIVE-DISPUTED`` with
  its own computed hash if either check fails. The payload is not yet
  part of either side's derived state.
- **Settle (phase 2)** — only when both phase-1 bookings match may
  either side book ``SETTLE``, and only settled payloads enter derived
  state: ``settled_payloads`` is a fold over the ledger (record
  primary, state as a fold). An offer never settled is visible in both
  books as open, forever — no timeout magic, no silent expiry.

Booking a SETTLE requires each side's *own* evidence of the match: the
receiver needs its verified RECEIVE (a dispute books nothing it could
settle on); the sender needs its SEND plus a returned receipt crossing
naming the same hash — the receipt is the inter-process form of "the
other side booked agreement". ``settleable_crossings`` re-checks the
whole condition over both books at audit time.

Disputes are permanent for the transmission id: a disputed crossing
can never settle, the disagreement is kept verbatim on both sides (the
§3.4 discipline — the receiver's ``RECEIVE-DISPUTED`` detail, and the
``payload/dispute`` envelope that crosses back and is booked whole
into the sender's book), and a corrected payload travels under a NEW
transmission id. No retries with a mutated payload under the same id.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Iterable

from .envelope import Ending, TransmissionEnvelope, canonical_json
from .state import LedgerEntry, StateStore

PAYLOAD_TYPE = "payload"
RECEIPT_TYPE = "payload/receipt"
DISPUTE_TYPE = "payload/dispute"


# ------------------------------------------------------------ wire form

def wire_content(content: Any) -> dict[str, Any]:
    """The JSON-safe wire form of an actual payload: bytes ride base64."""
    if isinstance(content, (bytes, bytearray)):
        return {
            "content": base64.b64encode(bytes(content)).decode("ascii"),
            "content_encoding": "base64",
        }
    return {"content": content, "content_encoding": "json"}


def decode_wire(wire: Any) -> Any:
    if not (isinstance(wire, dict) and "content" in wire):
        raise ValueError("not a payload wire form: {'content', 'content_encoding'}")
    if wire.get("content_encoding") == "base64":
        return base64.b64decode(wire["content"])
    return wire["content"]


def content_size(content: Any) -> int:
    if isinstance(content, (bytes, bytearray)):
        return len(content)
    return len(canonical_json(content).encode("utf-8"))


def make_payload_offer(
    content: Any,
    *,
    contract: str,
    ending: Ending = Ending.PROJECT,
    cost_budget: float = 0.0,
    copy_authorized_by: str | None = None,
) -> TransmissionEnvelope:
    if not contract:
        raise ValueError("a payload offer must name its governing contract")
    return TransmissionEnvelope(
        payload=wire_content(content),
        type=PAYLOAD_TYPE,
        ending=ending,
        cost_budget=cost_budget,
        copy_authorized_by=copy_authorized_by,
        payload_size=content_size(content),
        contract=contract,
    )


def measured_size(envelope: TransmissionEnvelope) -> int:
    """Size in bytes of the carried content, recomputed from the wire."""
    return content_size(decode_wire(envelope.payload))


# ------------------------------------------------- the receiver's check

@dataclass
class OfferReading:
    """What a receiver actually measured off an arriving offer.

    ``declared_hash`` / ``declared_size`` are what the envelope claims;
    ``computed_hash`` / ``measured_size`` are what the receiver itself
    measured. Booking a RECEIVE requires both to agree; a disagreement
    on either is dispute material, kept verbatim.
    """

    envelope: TransmissionEnvelope
    declared_hash: str | None
    declared_size: int | None
    measured_size: int

    @property
    def computed_hash(self) -> str:
        return self.envelope.hash

    @property
    def hash_ok(self) -> bool:
        return self.declared_hash == self.computed_hash

    @property
    def size_ok(self) -> bool:
        return (
            self.declared_size is not None
            and self.declared_size == self.measured_size
        )

    @property
    def ok(self) -> bool:
        return self.hash_ok and self.size_ok


def read_offer(text: str) -> OfferReading:
    """Read an arriving payload offer, tolerating a tampered payload.

    The strict envelope parser refuses hash mismatches on arrival; a
    receiver following the dispute path must nonetheless be able to
    read the id, the declared hash, and its own computed hash well
    enough to book the disagreement. An undecodable content (e.g.
    corrupted base64) measures as -1: a dispute, not a crash.
    """
    d = json.loads(text)
    if d.get("type") != PAYLOAD_TYPE:
        raise ValueError(f"read_offer: not a {PAYLOAD_TYPE} transmission")
    env = TransmissionEnvelope.from_dict(d, verify_payload_hash=False)
    try:
        size = content_size(decode_wire(env.payload))
    except Exception:
        size = -1
    return OfferReading(
        envelope=env,
        declared_hash=d.get("payload_hash"),
        declared_size=d.get("payload_size"),
        measured_size=size,
    )


# -------------------------------------------------- response envelopes

def make_receipt(reading: OfferReading) -> TransmissionEnvelope:
    """The receiver's acknowledgment that it booked RECEIVE(offer)."""
    return TransmissionEnvelope(
        payload={
            "about": reading.envelope.transmission_id,
            "payload_hash": reading.computed_hash,
            "measured_size": reading.measured_size,
        },
        type=RECEIPT_TYPE,
        ending=Ending.PROJECT,
        cost_budget=0.0,
    )


def make_dispute(reading: OfferReading) -> TransmissionEnvelope:
    """Both measurements, verbatim — this is §3.4 material, never averaged."""
    return TransmissionEnvelope(
        payload={
            "about": reading.envelope.transmission_id,
            "declared_hash": reading.declared_hash,
            "computed_hash": reading.computed_hash,
            "declared_size": reading.declared_size,
            "measured_size": reading.measured_size,
        },
        type=DISPUTE_TYPE,
        ending=Ending.PROJECT,
        cost_budget=0.0,
    )


# ------------------------------------------------------------- bookings

def book_offer_send(
    book: StateStore, offer: TransmissionEnvelope, peer: str,
    *, contract_id: str = "", contract_version: str | None = None,
) -> LedgerEntry:
    # contract_id/contract_version carry the SEAM contract's citation
    # (v0.3) — the version of the seam contract governing this crossing
    # at booking time — distinct from ``contract``, the payload's own
    # governing id (v0.2).
    return book.book(
        "SEND",
        transmission_id=offer.transmission_id,
        peer=peer,
        payload_type=offer.type,
        payload_hash=offer.hash,
        ending=offer.ending.value,
        cost=offer.cost_budget,
        contract=offer.contract or "",
        detail={"phase": "offer", "size": offer.payload_size},
        contract_id=contract_id,
        contract_version=contract_version,
    )


def book_offer_receive(
    book: StateStore, reading: OfferReading, peer: str,
    *, contract_id: str = "", contract_version: str | None = None,
) -> LedgerEntry:
    """Book the verified RECEIVE. The caller has checked ``reading.ok``."""
    if not reading.ok:
        raise ValueError(
            "book a dispute, not a RECEIVE: hash/size do not match the envelope"
        )
    offer = reading.envelope
    return book.book(
        "RECEIVE",
        transmission_id=offer.transmission_id,
        peer=peer,
        payload_type=offer.type,
        payload_hash=offer.hash,
        ending=offer.ending.value,
        cost=offer.cost_budget,
        contract=offer.contract or "",
        detail={"phase": "offer", "size": offer.payload_size},
        contract_id=contract_id,
        contract_version=contract_version,
    )


def book_disputed_receive(
    book: StateStore, reading: OfferReading, peer: str,
    *, contract_id: str = "", contract_version: str | None = None,
) -> LedgerEntry:
    """RECEIVE-DISPUTED, carrying the receiver's own computed hash."""
    offer = reading.envelope
    return book.book(
        "RECEIVE-DISPUTED",
        transmission_id=offer.transmission_id,
        peer=peer,
        payload_type=offer.type,
        payload_hash=reading.computed_hash,
        ending=offer.ending.value,
        cost=offer.cost_budget,
        contract=offer.contract or "",
        detail={
            "phase": "offer",
            "disputed": True,
            "declared_hash": reading.declared_hash,
            "declared_size": reading.declared_size,
            "measured_size": reading.measured_size,
        },
        contract_id=contract_id,
        contract_version=contract_version,
    )


def book_receipt_send(
    book: StateStore, receipt: TransmissionEnvelope, reading: OfferReading, peer: str,
    *, contract_id: str = "", contract_version: str | None = None,
) -> LedgerEntry:
    return book.book(
        "SEND",
        transmission_id=receipt.transmission_id,
        peer=peer,
        payload_type=RECEIPT_TYPE,
        payload_hash=receipt.hash,
        ending=receipt.ending.value,
        cost=receipt.cost_budget,
        contract=reading.envelope.contract or "",
        detail={
            "in_reply_to": reading.envelope.transmission_id,
            "offer_hash": receipt.payload.get("payload_hash"),
        },
        contract_id=contract_id,
        contract_version=contract_version,
    )


def book_receipt_receive(
    book: StateStore, receipt: TransmissionEnvelope, peer: str,
    *, contract_id: str = "", contract_version: str | None = None,
) -> LedgerEntry:
    return book.book(
        "RECEIVE",
        transmission_id=receipt.transmission_id,
        peer=peer,
        payload_type=RECEIPT_TYPE,
        payload_hash=receipt.hash,
        ending=receipt.ending.value,
        cost=receipt.cost_budget,
        contract="",
        detail={
            "in_reply_to": receipt.payload.get("about"),
            "offer_hash": receipt.payload.get("payload_hash"),
        },
        contract_id=contract_id,
        contract_version=contract_version,
    )


def book_dispute_send(
    book: StateStore, dispute: TransmissionEnvelope, reading: OfferReading, peer: str,
    *, contract_id: str = "", contract_version: str | None = None,
) -> LedgerEntry:
    """The dispute crossing back — its payload embedded verbatim."""
    return book.book(
        "SEND",
        transmission_id=dispute.transmission_id,
        peer=peer,
        payload_type=DISPUTE_TYPE,
        payload_hash=dispute.hash,
        ending=dispute.ending.value,
        cost=dispute.cost_budget,
        contract=reading.envelope.contract or "",
        detail={
            "in_reply_to": reading.envelope.transmission_id,
            "dispute": dict(dispute.payload),
        },
        contract_id=contract_id,
        contract_version=contract_version,
    )


def book_dispute_receive(
    book: StateStore, dispute: TransmissionEnvelope, peer: str,
    *, contract_id: str = "", contract_version: str | None = None,
) -> LedgerEntry:
    """The sender's booking of the returned dispute — verbatim (§3.4)."""
    return book.book(
        "RECEIVE",
        transmission_id=dispute.transmission_id,
        peer=peer,
        payload_type=DISPUTE_TYPE,
        payload_hash=dispute.hash,
        ending=dispute.ending.value,
        cost=dispute.cost_budget,
        contract="",
        detail={
            "in_reply_to": dispute.payload.get("about"),
            "dispute": dict(dispute.payload),
        },
        contract_id=contract_id,
        contract_version=contract_version,
    )


def book_settle(
    book: StateStore, offer: TransmissionEnvelope, peer: str,
    *, contract_id: str = "", contract_version: str | None = None,
) -> LedgerEntry:
    """Phase 2. Refused unless this book itself holds match evidence.

    The receiver's evidence is its verified RECEIVE; the sender's is
    its SEND plus a receipt crossing that named the same hash. A
    disputed id never settles — a corrected payload needs a new
    transmission id. The SETTLE entry carries the wire content so the
    settled view is a true fold of the ledger.
    """
    tid, h = offer.transmission_id, offer.hash
    if any(e.side == "SETTLE" and e.transmission_id == tid for e in book.ledger):
        raise ValueError(f"payload {tid} is already settled in this book")
    if tid in _disputed_tids(book.ledger):
        raise ValueError(
            f"payload {tid} was disputed and can never settle; "
            "a corrected payload is a NEW transmission"
        )
    recv = any(
        e.side == "RECEIVE" and e.transmission_id == tid and e.payload_hash == h
        for e in book.ledger
    )
    send = any(
        e.side == "SEND" and e.transmission_id == tid and e.payload_hash == h
        for e in book.ledger
    )
    receipt = any(
        e.side == "RECEIVE"
        and e.payload_type == RECEIPT_TYPE
        and e.detail.get("in_reply_to") == tid
        and e.detail.get("offer_hash") == h
        for e in book.ledger
    )
    if not (recv or (send and receipt)):
        raise ValueError(
            f"settlement refused for {tid}: this book does not hold both "
            "sides' matching offer bookings"
        )
    return book.book(
        "SETTLE",
        transmission_id=tid,
        peer=peer,
        payload_type=PAYLOAD_TYPE,
        payload_hash=h,
        ending=offer.ending.value,
        cost=offer.cost_budget,
        contract=offer.contract or "",
        detail={
            "phase": "settle",
            "content": dict(offer.payload),
            "size": offer.payload_size,
        },
        contract_id=contract_id,
        contract_version=contract_version,
    )


# ------------------------------------------------ derived state (folds)

def settled_payloads(ledger: Iterable[LedgerEntry]) -> dict[str, dict[str, Any]]:
    """The settled-payloads view: a fold of the ledger, nothing else.

    Only SETTLE entries contribute; open offers and disputes do not
    appear here. Content comes back decoded — bytes as bytes.
    """
    out: dict[str, dict[str, Any]] = {}
    for e in ledger:
        if e.side == "SETTLE":
            out[e.transmission_id] = {
                "payload_hash": e.payload_hash,
                "contract": e.contract,
                "contract_id": e.contract_id,
                "contract_version": e.contract_version,
                "size": e.detail.get("size"),
                "content": decode_wire(e.detail.get("content", {})),
            }
    return out


def open_offers(ledger: Iterable[LedgerEntry]) -> list[str]:
    """Ids with a phase-1 payload booking and no SETTLE — open, forever.

    No timeout magic: a disputed offer is also an open one (it is
    visible in both books and will never settle).
    """
    settled = {e.transmission_id for e in ledger if e.side == "SETTLE"}
    return sorted(
        {
            e.transmission_id
            for e in ledger
            if e.payload_type == PAYLOAD_TYPE
            and e.side in ("SEND", "RECEIVE", "RECEIVE-DISPUTED")
            and e.transmission_id not in settled
        }
    )


def disputes(ledger: Iterable[LedgerEntry]) -> list[dict[str, Any]]:
    """The disputes this book carries, both measurements verbatim.

    The receiver side contributes its RECEIVE-DISPUTED booking; the
    sender side contributes the ``payload/dispute`` crossing that came
    back, payload embedded whole.
    """
    out: list[dict[str, Any]] = []
    for e in ledger:
        if e.side == "RECEIVE-DISPUTED":
            out.append(
                {
                    "transmission_id": e.transmission_id,
                    "declared_hash": e.detail.get("declared_hash"),
                    "computed_hash": e.payload_hash,
                    "declared_size": e.detail.get("declared_size"),
                    "measured_size": e.detail.get("measured_size"),
                    "seen_as": "receive_disputed",
                }
            )
        elif e.payload_type == DISPUTE_TYPE and e.detail.get("dispute"):
            d = e.detail["dispute"]
            out.append(
                {
                    "transmission_id": d.get("about"),
                    "declared_hash": d.get("declared_hash"),
                    "computed_hash": d.get("computed_hash"),
                    "declared_size": d.get("declared_size"),
                    "measured_size": d.get("measured_size"),
                    "seen_as": "dispute_response",
                }
            )
    return out


def _disputed_tids(ledger: Iterable[LedgerEntry]) -> set[str]:
    return {
        d["transmission_id"] for d in disputes(ledger) if d["transmission_id"]
    }


# ------------------------------------------------------- pairwise gate

def settleable_crossings(
    entries_a: Iterable[LedgerEntry], entries_b: Iterable[LedgerEntry]
) -> tuple[list[str], list[str]]:
    """Gap 2, payload form: which payload offers may settle, over both books.

    An offer is *settleable* when one book holds a SEND and the other a
    RECEIVE for the same transmission id and payload hash — and no book
    holds a dispute touching that id. Permission, not fact: an offer
    may be settleable and simply never settled. Returns (settleable
    ids, blocked ids); a disputed id is blocked forever.
    """
    a = [e for e in entries_a if e.payload_type == PAYLOAD_TYPE]
    b = [e for e in entries_b if e.payload_type == PAYLOAD_TYPE]
    keys_a = {(e.transmission_id, e.payload_hash, e.side) for e in a}
    keys_b = {(e.transmission_id, e.payload_hash, e.side) for e in b}
    disputed = _disputed_tids(a) | _disputed_tids(b)
    ids = {e.transmission_id for e in a} | {e.transmission_id for e in b}
    settleable, blocked = [], []
    for tid in sorted(ids):
        hashes = {h for t, h, _ in keys_a | keys_b if t == tid}
        matched = any(
            ((tid, h, "SEND") in keys_a and (tid, h, "RECEIVE") in keys_b)
            or ((tid, h, "SEND") in keys_b and (tid, h, "RECEIVE") in keys_a)
            for h in hashes
        )
        (settleable if matched and tid not in disputed else blocked).append(tid)
    return settleable, blocked
