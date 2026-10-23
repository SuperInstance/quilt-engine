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

v0.3 (DESIGN-V03 §6.7): the seam's contract is an ``AlgorithmContract``
object whose version is *derived* from its pinned canonical statement
(``handshake_v1.json``) — the demo's version is a real content hash,
not the literal label "1.2". When a contract object is supplied, the
sender stamps the envelope's ``(contract_id, contract_version)`` before
crossing (the version it cites — its book's current), and the receiver
stamps **the version it applied** into its RECEIVE (honouring the
citation per §2.5/§3.1, flagging superseded citations, refusing
revoked/unknown ones). Without a contract object the seam keeps the
v0.1 flat-string path, whose "@1.2" half is a label, not a hash.

The two-process demo (examples/) uses the same classes with each
process holding only its own side's book — JevPair is the in-process
convenience, not a shared-memory back door: even in one process, the
two patches communicate only through envelopes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.contract import AlgorithmContract, ContractResult
from ..core.envelope import Ending, TransmissionEnvelope
from ..core.state import StateStore
from ..core.versioning import (
    book_seam_receive,
    book_seam_send,
    contract_state,
    held_statements,
    resolve_citation,
)
from .jev import (
    Act,
    ClarifyNarrow,
    Decision,
    ReceiverJev,
    Refuse,
    SenderJev,
)

# v0.1 flat default, kept for the unversioned path. Its "@1.2" half
# tracks the SPEC version and is a display label, not a content hash —
# a legacy citation, not trustworthy about versions (DESIGN-V03 §0/§6.2).
DEFAULT_CONTRACT_ID = "beam-quilt/seam-handshake@1.2"
DEFAULT_CONTRACT_FAMILY = "beam-quilt/seam-handshake"

# The pinned canonical statement of the seam-handshake contract, v1.
# This document — not a typed label — is what the contract's version
# hashes (DESIGN-V03 §6.7), so the demo's version is a real content hash.
HANDSHAKE_STATEMENT_V1 = json.loads(
    (Path(__file__).parent / "handshake_v1.json").read_text(encoding="utf-8")
)


def _handshake_fn(inp: Any) -> ContractResult:
    return ContractResult(
        output=inp, why="seam-handshake statement applied", cost=0.0
    )


SEAM_CONTRACT_V1 = AlgorithmContract(
    name=DEFAULT_CONTRACT_FAMILY,
    statement=HANDSHAKE_STATEMENT_V1,
    fn=_handshake_fn,
    label="v1",
)


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
        contract: AlgorithmContract | None = None,
        seam_book: StateStore | None = None,
    ) -> None:
        self.sender = sender
        self.receiver = receiver
        self.sender_book = sender_book
        self.receiver_book = receiver_book
        self.sender_name = sender_name
        self.receiver_name = receiver_name
        self.contract_id = contract_id
        # v0.3: when set, the seam's contract object governs — the
        # version each side stamps is derived from it and from the
        # revision records in that side's own book (§6.7).
        self.contract = contract
        self.seam_book = seam_book
        self.seam_events: list[dict[str, Any]] = []

    def _current(self, book: StateStore) -> str:
        """The version of the seam contract that book currently holds."""
        return contract_state(book.revisions, self.contract.version)["current"]

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

        # 2. Book both sides of the crossing (P5), then cross. v0.3:
        #    with a contract object, the sender stamps the citation it
        #    cites and the receiver stamps the version it *applied* —
        #    honouring, flagging, or refusing per the §3.1 table.
        citation: dict[str, Any] = {}
        if self.contract is not None:
            sender_current = self._current(self.sender_book)
            envelope.contract_id = self.contract.name
            envelope.contract_version = sender_current
            book_seam_send(self.sender_book, envelope, self.receiver_name)
            state = contract_state(
                self.receiver_book.revisions, self.contract.version
            )
            ruling = resolve_citation(
                envelope.contract_version,
                state=state,
                held=set(
                    held_statements(self.receiver_book.revisions, self.contract.statement)
                ),
            )
            recv_entry = book_seam_receive(
                self.receiver_book, envelope, self.sender_name, ruling=ruling
            )
            citation = {
                "contract_id": envelope.contract_id,
                "cited_version": envelope.contract_version,
                "applied_version": recv_entry.contract_version,
                "superseded_citation": bool(ruling.superseded_citation),
            }
            if ruling.divergence:
                # A version divergence is a first-class seam event,
                # never averaged away (§3.4).
                self._seam_log(
                    {
                        "event": "version_divergence",
                        "transmission_id": envelope.transmission_id,
                        "cited_version": envelope.contract_version,
                        "applied_version": ruling.applied_version,
                        "citation_refusal": ruling.citation_refusal,
                    }
                )
        else:
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

        # C2: every crossing readable after the fact. v0.1: the
        # expectation the decision steered against, and the projection
        # behind it when one was consulted — adopted or refused — so
        # fog behaviour is auditable, never silent. v0.3: the citation
        # — what the crossing cited and what the receiver applied.
        pick_id = getattr(decision, "pick_id", None)
        projection = getattr(decision, "projection", None)
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
                "expected_course": getattr(decision, "expected_course", None),
                "projection": projection.to_dict() if projection else None,
                "contract": self.contract_id,
                **citation,
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
                if self.contract is not None:
                    # The response cites the version the receiver applied
                    # to the crossing it answers (§2.5); the original
                    # sender honours that citation on arrival.
                    response_envelope.contract_id = self.contract.name
                    response_envelope.contract_version = citation.get(
                        "applied_version"
                    )
                    book_seam_send(
                        self.receiver_book, response_envelope, self.sender_name,
                        detail={"in_reply_to": envelope.transmission_id},
                    )
                    resp_state = contract_state(
                        self.sender_book.revisions, self.contract.version
                    )
                    resp_ruling = resolve_citation(
                        response_envelope.contract_version,
                        state=resp_state,
                        held=set(
                            held_statements(
                                self.sender_book.revisions, self.contract.statement
                            )
                        ),
                    )
                    book_seam_receive(
                        self.sender_book, response_envelope, self.receiver_name,
                        ruling=resp_ruling, detail={"in_reply_to": envelope.transmission_id},
                    )
                else:
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
