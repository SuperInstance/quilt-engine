"""JevPair — one sender Jev + one receiver Jev across one seam.

Orchestrates the full SPEC §3 handshake in-process, with the P4/P5
bookkeeping done properly on both sides:

- the sender's book records a **SEND** and the receiver's book a
  **RECEIVE** for the same transmission id + payload hash, each entry
  stamped with the governing contract's identity (gap 3);
- the receiver's active log gains a dated **reading** of the arriving
  candidate (P4);
- every crossing lands in the seam log with the C2 fields — payload
  type, profile, pick, ending, cost — plus the decision;
- disagreement records (§3.4) are drained into the seam log verbatim;
- an Act response that travels back (refusal profile, clarify
  question) is itself booked as a crossing in the reverse direction.

The two-process demo (examples/) uses the same classes with each
process holding only its own side's book — JevPair is the in-process
convenience, not a shared-memory back door: even in one process, the
two patches communicate only through envelopes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..core.envelope import Ending, TransmissionEnvelope
from ..core.state import StateStore
from .jev import (
    Act,
    ClarifyNarrow,
    Decision,
    ReceiverJev,
    Refuse,
    SenderJev,
)

DEFAULT_CONTRACT_ID = "beam-quilt/seam-handshake@1.2"


@dataclass
class CrossingResult:
    decision: Decision
    profiled_envelope: TransmissionEnvelope
    response_envelope: TransmissionEnvelope | None = None


class JevPair:
    def __init__(
        self,
        sender: SenderJev,
        receiver: ReceiverJev,
        sender_book: StateStore,
        receiver_book: StateStore,
        sender_name: str = "sender",
        receiver_name: str = "receiver",
        contract_id: str = DEFAULT_CONTRACT_ID,
        seam_book: StateStore | None = None,
    ) -> None:
        self.sender = sender
        self.receiver = receiver
        self.sender_book = sender_book
        self.receiver_book = receiver_book
        self.sender_name = sender_name
        self.receiver_name = receiver_name
        self.contract_id = contract_id
        self.seam_book = seam_book
        self.seam_events: list[dict[str, Any]] = []

    def _seam_log(self, event: dict[str, Any]) -> None:
        event = {"at": time.time(), **event}
        self.seam_events.append(event)
        if self.seam_book is not None:
            self.seam_book.log_event(event)

    def cross(
        self,
        envelope: TransmissionEnvelope,
        *,
        extra_pool: list[TransmissionEnvelope] | None = None,
        now: float | None = None,
    ) -> CrossingResult:
        now = time.monotonic() if now is None else now

        # 1. Profile (sender Jev describes; it does not decide).
        profile = self.sender.project(envelope)
        envelope.sender_profile = profile

        # 2. Book both sides of the crossing (P5), then cross.
        self.sender_book.book(
            "SEND",
            transmission_id=envelope.transmission_id,
            peer=self.receiver_name,
            payload_type=envelope.type,
            payload_hash=envelope.hash,
            ending=envelope.ending.value,
            cost=envelope.cost_budget,
            contract=self.contract_id,
        )
        self.receiver_book.book(
            "RECEIVE",
            transmission_id=envelope.transmission_id,
            peer=self.sender_name,
            payload_type=envelope.type,
            payload_hash=envelope.hash,
            ending=envelope.ending.value,
            cost=envelope.cost_budget,
            contract=self.contract_id,
        )
        # The receiver's active log gains a dated reading (P4).
        self.receiver_book.record_reading(
            cell=self.sender_name,
            value={"transmission_id": envelope.transmission_id, "type": envelope.type},
            taken_at=now,
            note="candidate received",
        )

        # 3. Comparative pick + deadband (receiver Jev).
        pool = [envelope] + list(extra_pool or [])
        decision = self.receiver.evaluate(pool=pool, now=now)

        # 4. Disagreements are seam events, verbatim (§3.4).
        for rec in self.receiver.disagreements:
            self._seam_log(
                {
                    "event": "disagreement",
                    "transmission_id": rec.transmission_id,
                    "sender_profile": rec.sender_profile,
                    "receiver_measurement": rec.receiver_measurement,
                    "divergence": rec.divergence,
                }
            )
        self.receiver.disagreements.clear()

        # C2: every crossing readable after the fact.
        pick_id = getattr(decision, "pick_id", None)
        self._seam_log(
            {
                "event": "crossing",
                "transmission_id": envelope.transmission_id,
                "payload_type": envelope.type,
                "profile": profile,
                "pick": pick_id,
                "ending": envelope.ending.value,
                "cost": envelope.cost_budget,
                "decision": decision.kind,
                "contract": self.contract_id,
            }
        )

        # 5. Responses that travel back are crossings too (booked both
        #    sides, reverse direction).
        response_envelope: TransmissionEnvelope | None = None
        if isinstance(decision, Act):
            resp = decision.response
            if isinstance(resp, Refuse):
                response_envelope = TransmissionEnvelope(
                    payload=resp.profile,
                    type=f"{envelope.type}/refusal",
                    ending=Ending.PROJECT,
                    cost_budget=0.0,
                )
            elif isinstance(resp, ClarifyNarrow):
                response_envelope = resp.question
            if response_envelope is not None:
                self.receiver_book.book(
                    "SEND",
                    transmission_id=response_envelope.transmission_id,
                    peer=self.sender_name,
                    payload_type=response_envelope.type,
                    payload_hash=response_envelope.hash,
                    ending=response_envelope.ending.value,
                    cost=response_envelope.cost_budget,
                    contract=self.contract_id,
                    detail={"in_reply_to": envelope.transmission_id},
                )
                self.sender_book.book(
                    "RECEIVE",
                    transmission_id=response_envelope.transmission_id,
                    peer=self.receiver_name,
                    payload_type=response_envelope.type,
                    payload_hash=response_envelope.hash,
                    ending=response_envelope.ending.value,
                    cost=response_envelope.cost_budget,
                    contract=self.contract_id,
                    detail={"in_reply_to": envelope.transmission_id},
                )
                self._seam_log(
                    {
                        "event": "response",
                        "transmission_id": response_envelope.transmission_id,
                        "in_reply_to": envelope.transmission_id,
                        "payload_type": response_envelope.type,
                        "ending": response_envelope.ending.value,
                        "cost": response_envelope.cost_budget,
                    }
                )

        return CrossingResult(
            decision=decision,
            profiled_envelope=envelope,
            response_envelope=response_envelope,
        )
