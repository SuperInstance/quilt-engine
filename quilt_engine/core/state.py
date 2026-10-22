"""A patch's two sovereign records (SPEC P4/P5, beam-quilt SPEC v1.2).

- **Active log** — the patch's own readings of every cell from its own
  perspective, each reading *dated*. Readings age; staleness is explicit
  (``Reading.age`` / ``Reading.is_stale``) because acting on a reading
  must account for its date. This is gap 1 of beam-quilt FOUNDATIONS in
  its smallest honest form: dated readings + explicit staleness.
  v0.1 adds the smallest honest piece of the fog model: a reading of a
  numeric cell carries a **vector** — a ``trend`` (rate of change per
  second) derived from the two most recent readings of that cell — and
  can *project* itself a short way into the future
  (``Reading.projected_value``). Linear, local, short-horizon: a wake,
  not a prophecy. The projection horizon is capped at a small multiple
  of the spacing between the readings the trend came from, so a wild
  slope from two noisy, close-together readings cannot project
  confidently far (``Projection.refused`` says why, out loud).
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


# A projection may reach at most this many times the spacing between
# the two readings its trend was derived from. Two readings 0.1 s apart
# license a glance 0.3 s ahead, not a forecast a minute out.
PROJECTION_HORIZON_FACTOR = 3.0


@dataclass
class Projection:
    """One reading's linear extrapolation to a later time.

    Explicit and inspectable: it carries the trend it used, the pair of
    reading-times it was derived from (``based_on``), how far ahead it
    reaches (``horizon``) and how far it was *allowed* to reach
    (``max_horizon``). A refused projection has ``value is None`` and a
    ``reason`` — refusal is a result, not a silent fallback.
    """

    value: float | None
    trend: float
    horizon: float  # seconds beyond the reading being projected
    max_horizon: float  # PROJECTION_HORIZON_FACTOR x trend spacing
    based_on: tuple[float, float]  # taken_at of the two readings used
    refused: bool = False
    reason: str = ""
    used: bool = False  # set by a consumer that actually decided with it

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "trend": self.trend,
            "horizon": self.horizon,
            "max_horizon": self.max_horizon,
            "based_on": list(self.based_on),
            "refused": self.refused,
            "reason": self.reason,
            "used": self.used,
        }


@dataclass
class Reading:
    """One dated reading of one cell, from this patch's perspective.

    v0.1: a numeric reading may also carry a *vector* — ``trend``, the
    rate of change (per second) derived from this reading and the
    previous reading of the same cell, with ``trend_from`` naming the
    pair of reading-times it came from and ``trend_spacing`` their
    distance apart. Non-numeric readings never carry a trend.
    """

    cell: str
    value: Any
    taken_at: float  # seconds, on the reader's clock (time.monotonic in v0)
    note: str = ""
    trend: float | None = None
    trend_from: tuple[float, float] | None = None
    trend_spacing: float | None = None

    def age(self, now: float) -> float:
        return now - self.taken_at

    def is_stale(self, now: float, max_age: float) -> bool:
        return self.age(now) > max_age

    def project(
        self, at_time: float, horizon_factor: float = PROJECTION_HORIZON_FACTOR
    ) -> Projection:
        """Extrapolate this reading's value to ``at_time``, linearly.

        Refused (``Projection.refused``) when there is no trend, when
        ``at_time`` precedes the reading, or when the horizon exceeds
        ``horizon_factor`` x the spacing the trend was derived from.
        """
        based_on = self.trend_from or (self.taken_at, self.taken_at)
        spacing = self.trend_spacing or 0.0
        max_horizon = horizon_factor * spacing
        horizon = at_time - self.taken_at
        if self.trend is None:
            return Projection(
                value=None, trend=0.0, horizon=horizon,
                max_horizon=max_horizon, based_on=based_on,
                refused=True, reason="reading carries no trend",
            )
        if horizon < 0:
            return Projection(
                value=None, trend=self.trend, horizon=horizon,
                max_horizon=max_horizon, based_on=based_on,
                refused=True, reason="projection precedes the reading",
            )
        if horizon > max_horizon:
            return Projection(
                value=None, trend=self.trend, horizon=horizon,
                max_horizon=max_horizon, based_on=based_on,
                refused=True,
                reason=(
                    f"horizon {horizon:.3f}s exceeds cap {max_horizon:.3f}s "
                    f"({horizon_factor}x the {spacing:.3f}s trend spacing)"
                ),
            )
        return Projection(
            value=float(self.value) + self.trend * horizon,
            trend=self.trend, horizon=horizon, max_horizon=max_horizon,
            based_on=based_on,
        )

    def projected_value(
        self, at_time: float, horizon_factor: float = PROJECTION_HORIZON_FACTOR
    ) -> float | None:
        """value + trend x (at_time - taken_at), or None if refused."""
        return self.project(at_time, horizon_factor).value

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": "reading",
            "cell": self.cell,
            "value": self.value,
            "taken_at": self.taken_at,
            "note": self.note,
        }
        if self.trend is not None:
            d["trend"] = self.trend
            d["trend_from"] = list(self.trend_from) if self.trend_from else None
            d["trend_spacing"] = self.trend_spacing
        return d


@dataclass
class LedgerEntry:
    """One side's booking of one crossing (SPEC P5)."""

    side: str  # "SEND" / "RECEIVE"; v0.2 payload path adds "RECEIVE-DISPUTED" and "SETTLE"
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


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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
        self._derive_trend(r)
        self.readings.append(r)
        self._append_file(r.to_dict())
        return r

    def _derive_trend(self, r: Reading) -> None:
        """Give a numeric reading its vector, from the previous one.

        The trend is the last-pair slope — (v - v_prev) / (t - t_prev)
        against the most recent earlier numeric reading of the same
        cell. Two readings at the same instant, or a non-numeric value
        on either side, leave the reading trendless.
        """
        if not _is_number(r.value):
            return
        prev = self.latest_reading(r.cell)
        if prev is None or not _is_number(prev.value):
            return
        spacing = r.taken_at - prev.taken_at
        if spacing <= 0:
            return
        r.trend = (float(r.value) - float(prev.value)) / spacing
        r.trend_from = (prev.taken_at, r.taken_at)
        r.trend_spacing = spacing

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
                tf = d.get("trend_from")
                store.readings.append(
                    Reading(
                        d["cell"],
                        d["value"],
                        d["taken_at"],
                        d.get("note", ""),
                        trend=d.get("trend"),
                        trend_from=tuple(tf) if tf else None,
                        trend_spacing=d.get("trend_spacing"),
                    )
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
