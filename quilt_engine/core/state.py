"""A patch's two sovereign records (SPEC P4/P5, beam-quilt SPEC v1.2).

- **Active log** — the patch's own readings of every cell from its own
  perspective, each reading *dated*. Readings age; staleness is explicit
  (``Reading.age`` / ``Reading.is_stale``) because acting on a reading
  must account for its date. This is gap 1 of beam-quilt FOUNDATIONS in
  its smallest honest form: dated readings + explicit staleness. (The
  predictive model that fills the fog is not built in v0.)
- **Active ledger** — transmissions booked as entries, not private
  notes. Every crossing is booked twice across the quilt: a SEND by the
  sender, a RECEIVE by the receiver, each entry carrying the identity of
  the contract (algorithm) that governed the crossing (gap 3). A crossing
  counts as *shared* only where the two bookings agree — same
  transmission id, same payload hash (gap 2). ``agreed_crossings`` is
  that check.

Storage form (ARCHITECTURE, proposed): a local append-only JSONL file,
plus the in-memory list. Readings can be garbage-collected at the rate
the patch declares in its manifest (P5); ledger entries are kept.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Reading:
    """One dated reading of one cell, from this patch's perspective."""

    cell: str
    value: Any
    taken_at: float  # seconds, on the reader's clock (time.monotonic in v0)
    note: str = ""

    def age(self, now: float) -> float:
        return now - self.taken_at

    def is_stale(self, now: float, max_age: float) -> bool:
        return self.age(now) > max_age

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "reading",
            "cell": self.cell,
            "value": self.value,
            "taken_at": self.taken_at,
            "note": self.note,
        }


@dataclass
class LedgerEntry:
    """One side's booking of one crossing (SPEC P5)."""

    side: str  # "SEND" or "RECEIVE"
    transmission_id: str
    peer: str
    payload_type: str
    payload_hash: str
    ending: str
    cost: float
    contract: str  # identity of the governing contract (algorithm)
    at: float  # wall-clock seconds, for after-the-fact reading (C2)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "ledger",
            "side": self.side,
            "transmission_id": self.transmission_id,
            "peer": self.peer,
            "payload_type": self.payload_type,
            "payload_hash": self.payload_hash,
            "ending": self.ending,
            "cost": self.cost,
            "contract": self.contract,
            "at": self.at,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LedgerEntry":
        return cls(
            side=d["side"],
            transmission_id=d["transmission_id"],
            peer=d["peer"],
            payload_type=d["payload_type"],
            payload_hash=d["payload_hash"],
            ending=d["ending"],
            cost=d["cost"],
            contract=d["contract"],
            at=d["at"],
            detail=d.get("detail", {}),
        )


class StateStore:
    """One patch's active log + active ledger. Nobody else's (P4)."""

    def __init__(
        self,
        owner: str,
        path: str | Path | None = None,
        reading_gc_keep: int | None = None,
    ) -> None:
        self.owner = owner
        self.path = Path(path) if path is not None else None
        self.reading_gc_keep = reading_gc_keep
        self.readings: list[Reading] = []
        self.ledger: list[LedgerEntry] = []
        self.events: list[dict[str, Any]] = []  # seam/session events (C2)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- writes ---------------------------------------------------------

    def _append_file(self, record: dict[str, Any]) -> None:
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, sort_keys=True) + "\n")

    def record_reading(
        self, cell: str, value: Any, taken_at: float | None = None, note: str = ""
    ) -> Reading:
        r = Reading(
            cell=cell,
            value=value,
            taken_at=time.monotonic() if taken_at is None else taken_at,
            note=note,
        )
        self.readings.append(r)
        self._append_file(r.to_dict())
        return r

    def latest_reading(self, cell: str) -> Reading | None:
        for r in reversed(self.readings):
            if r.cell == cell:
                return r
        return None

    def book(
        self,
        side: str,
        *,
        transmission_id: str,
        peer: str,
        payload_type: str,
        payload_hash: str,
        ending: str,
        cost: float,
        contract: str,
        detail: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        e = LedgerEntry(
            side=side,
            transmission_id=transmission_id,
            peer=peer,
            payload_type=payload_type,
            payload_hash=payload_hash,
            ending=ending,
            cost=cost,
            contract=contract,
            at=time.time(),
            detail=detail or {},
        )
        self.ledger.append(e)
        self._append_file(e.to_dict())
        return e

    def log_event(self, event: dict[str, Any]) -> None:
        event = {"kind": "event", "at": time.time(), **event}
        self.events.append(event)
        self._append_file(event)

    # -- garbage collection (P5: at the rate the patch declares) --------

    def gc(self) -> int:
        """Drop oldest readings beyond the declared retention count.

        Ledger entries are the shared record and are never GC'd here.
        Returns the number of readings dropped.
        """
        if self.reading_gc_keep is None:
            return 0
        excess = len(self.readings) - self.reading_gc_keep
        if excess <= 0:
            return 0
        del self.readings[:excess]
        return excess

    # -- reading a persisted book back (C2, agreement checks) -----------

    @classmethod
    def load(cls, owner: str, path: str | Path) -> "StateStore":
        store = cls(owner)
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("kind") == "ledger":
                store.ledger.append(LedgerEntry.from_dict(d))
            elif d.get("kind") == "reading":
                store.readings.append(
                    Reading(d["cell"], d["value"], d["taken_at"], d.get("note", ""))
                )
            elif d.get("kind") == "event":
                store.events.append(d)
        return store


def agreed_crossings(
    entries_a: Iterable[LedgerEntry], entries_b: Iterable[LedgerEntry]
) -> tuple[list[str], list[str]]:
    """Gap 2, minimal form: shared state exists only in agreement.

    A crossing is *agreed* when one side's book holds a SEND and the
    other's holds a RECEIVE with the same transmission id *and* the
    same payload hash. Returns (agreed transmission ids, unagreed ids).
    """
    def key(e: LedgerEntry) -> tuple[str, str, str]:
        return (e.transmission_id, e.payload_hash, e.side)

    keys_a = {key(e) for e in entries_a}
    keys_b = {key(e) for e in entries_b}
    all_ids = {tid for tid, _, _ in keys_a | keys_b}
    agreed, unagreed = [], []
    for tid in sorted(all_ids):
        hashes = {h for t, h, _ in keys_a | keys_b if t == tid}
        ok = False
        for h in hashes:
            if (tid, h, "SEND") in keys_a | keys_b and (tid, h, "RECEIVE") in keys_a | keys_b:
                # SEND and RECEIVE must live in *different* books.
                send_in_a = (tid, h, "SEND") in keys_a
                recv_in_b = (tid, h, "RECEIVE") in keys_b
                send_in_b = (tid, h, "SEND") in keys_b
                recv_in_a = (tid, h, "RECEIVE") in keys_a
                if (send_in_a and recv_in_b) or (send_in_b and recv_in_a):
                    ok = True
        (agreed if ok else unagreed).append(tid)
    return agreed, unagreed
