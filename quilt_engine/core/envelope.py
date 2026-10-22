"""TransmissionEnvelope — the only thing that crosses a seam (SPEC §2).

The envelope is the tuple ``{payload, type, sender-profile, ending,
cost-budget}`` plus two additions the later programme documents require:

- a **provenance slot** shaped as OpenTelemetry / W3C span context
  (trace/span ids + links), assigned to this envelope by beam-quilt's
  FOUNDATIONS pull list (see this repo's FOUNDATIONS-APPLICATION.md);
- a **payload hash**, so the sender's SEND booking and the receiver's
  RECEIVE booking of the same crossing can be checked against each
  other (SPEC P5: only entries both sides agree on count as shared).

Rules enforced here, all from SPEC §2:

- T1: the payload's type is declared (a non-empty string).
- T2: exactly one ending — MUTATE / MOVE / COPY / PROJECT. COPY is
  never a default: it must be chosen explicitly *and* attributed
  (``copy_authorized_by``), per transmission or per standing owner
  instruction. The default ending is PROJECT (influence as proposal,
  receiver sovereign — beam-quilt docs/03).
- No state references cross (P4): the envelope is plain JSON data.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Ending(str, Enum):
    MUTATE = "MUTATE"
    MOVE = "MOVE"
    COPY = "COPY"
    PROJECT = "PROJECT"


def _hex(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


def canonical_json(obj: Any) -> str:
    """Canonical JSON for hashing: sorted keys, no whitespace drift."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass
class Provenance:
    """OTel/W3C-shaped span context (ids + links only; no backend).

    ``attributes`` is the free slot the foundations assign to intent and
    cost annotations. Ids are random hex in W3C trace-context widths:
    128-bit trace id, 64-bit span id.
    """

    trace_id: str = field(default_factory=lambda: _hex(16))
    span_id: str = field(default_factory=lambda: _hex(8))
    parent_span_id: str | None = None
    links: list[dict[str, str]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def child(self) -> "Provenance":
        """Span context for a transmission derived from this one."""
        return Provenance(
            trace_id=self.trace_id,
            span_id=_hex(8),
            parent_span_id=self.span_id,
            links=list(self.links),
            attributes=dict(self.attributes),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "links": list(self.links),
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Provenance":
        return cls(
            trace_id=d["trace_id"],
            span_id=d["span_id"],
            parent_span_id=d.get("parent_span_id"),
            links=list(d.get("links", [])),
            attributes=dict(d.get("attributes", {})),
        )


@dataclass
class TransmissionEnvelope:
    payload: Any
    type: str
    ending: Ending = Ending.PROJECT
    cost_budget: float = 0.0
    sender_profile: dict[str, Any] | None = None
    transmission_id: str = field(default_factory=lambda: _hex(8))
    provenance: Provenance = field(default_factory=Provenance)
    copy_authorized_by: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or not self.type:
            raise ValueError("T1: a transmission must declare a non-empty type")
        # T2: exactly one ending. A list/tuple (zero or two endings) is
        # rejected loudly rather than coerced.
        if isinstance(self.ending, (list, tuple, set)):
            raise ValueError(
                "T2: exactly one ending per transmission; got a collection"
            )
        self.ending = Ending(self.ending)  # raises ValueError if unknown
        if self.ending is Ending.COPY and not self.copy_authorized_by:
            raise ValueError(
                "T2: COPY is never a default; it must be chosen explicitly "
                "and attributed (copy_authorized_by)"
            )
        if self.cost_budget < 0:
            raise ValueError("cost-budget must be >= 0")

    @property
    def hash(self) -> str:
        return payload_hash(self.payload)

    def refreshed(self) -> "TransmissionEnvelope":
        """A re-send of the same transmission (same id, new span).

        Used when a sender re-asserts a candidate so the receiver's
        dated reading of it does not go stale (P4: readings age).
        """
        return TransmissionEnvelope(
            payload=self.payload,
            type=self.type,
            ending=self.ending,
            cost_budget=self.cost_budget,
            sender_profile=self.sender_profile,
            transmission_id=self.transmission_id,
            provenance=self.provenance.child(),
            copy_authorized_by=self.copy_authorized_by,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transmission_id": self.transmission_id,
            "payload": self.payload,
            "payload_hash": self.hash,
            "type": self.type,
            "sender_profile": self.sender_profile,
            "ending": self.ending.value,
            "cost_budget": self.cost_budget,
            "copy_authorized_by": self.copy_authorized_by,
            "provenance": self.provenance.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TransmissionEnvelope":
        if "ending" not in d or d["ending"] is None:
            raise ValueError("T2: envelope has no ending (zero endings)")
        if isinstance(d["ending"], (list, tuple)):
            raise ValueError("T2: envelope has more than one ending")
        env = cls(
            payload=d["payload"],
            type=d["type"],
            ending=Ending(d["ending"]),
            cost_budget=d.get("cost_budget", 0.0),
            sender_profile=d.get("sender_profile"),
            transmission_id=d.get("transmission_id") or _hex(8),
            provenance=Provenance.from_dict(d["provenance"])
            if d.get("provenance")
            else Provenance(),
            copy_authorized_by=d.get("copy_authorized_by"),
        )
        if "payload_hash" in d and d["payload_hash"] != env.hash:
            raise ValueError("payload hash mismatch: envelope was altered in transit")
        return env

    @classmethod
    def from_json(cls, s: str) -> "TransmissionEnvelope":
        return cls.from_dict(json.loads(s))
