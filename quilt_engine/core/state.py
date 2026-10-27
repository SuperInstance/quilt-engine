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
  transmission id, same payload hash (gap 2), and, since v0.3, the same
  cited contract version (DESIGN-V03 §6.6). ``agreed_crossings`` is
  that check, and it keeps version-skewed bookings in a category of
  their own rather than averaging them into plain unagreed.
- **Revision records** (v0.3, DESIGN-V03 §2/§6.3) — the ex-ante half of
  the ledger: a contract revision is booked as a ``RevisionEntry`` in
  each book that takes a position on it (proposed / adopted / refused /
  withdrawn / revoked). Movement entries say what crossed; revision
  records say under what agreement it crossed. The two halves compose.

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

from .contract import statement_version


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
    """One side's booking of one crossing (SPEC P5).

    v0.3 (DESIGN-V03 §6.2): the governing contract is cited as the pair
    ``contract_id`` (the family) + ``contract_version`` (the content
    hash of the statement THIS side applied — never a display label).
    ``contract`` is the v0.2 payload-transfer field (the flat id of the
    contract governing the carried payload, e.g. ``route-plan@1``); a
    legacy ledger whose seam citation lives there is readable by
    splitting on ``@``, but its version half is a label, not a content
    hash — not trustworthy about versions, per §6.2.
    """

    side: str  # "SEND" / "RECEIVE"; v0.2 payload path adds "RECEIVE-DISPUTED" and "SETTLE"
    transmission_id: str
    peer: str
    payload_type: str
    payload_hash: str
    ending: str
    cost: float
    contract: str  # v0.2 payload governing contract (flat id); not the seam citation
    at: float  # wall-clock seconds, for after-the-fact reading (C2)
    detail: dict[str, Any] = field(default_factory=dict)
    # v0.3 seam-contract citation (DESIGN-V03 §6.2): the family id and
    # the version this side applied (None = no versioned contract
    # governed the crossing, or the receiver could not apply any).
    contract_id: str = ""
    contract_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
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
        if self.contract_id:
            d["contract_id"] = self.contract_id
        if self.contract_version is not None:
            d["contract_version"] = self.contract_version
        return d

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
            contract_id=d.get("contract_id", ""),
            contract_version=d.get("contract_version"),
        )


@dataclass
class RevisionEntry:
    """One book's position on one contract revision (DESIGN-V03 §2, §6.3).

    The ex-ante half of the ledger. A revision is *booked*, append-only:
    a position change is a new record, and the fold reads the latest
    record per version. ``statement`` rides in the record beyond §6.3's
    printed field list because §2.5's "statements are retained" needs
    the receiver to hold the bytes of every version it may be asked to
    honour; ``statement_hash`` (== ``version``) makes the binding
    checkable. A revocation is the same shape with
    ``position="revoked"`` and the target version in ``version`` (§5.4).

    ``owner_consent`` is the §8.1 seam: whether a revision needs the
    owner's consent is an open ruling, so the field is recorded verbatim
    when supplied and enforced nowhere (DESIGN-V03 §8, open question 1).

    v0.3.1 (CC1 F3): revisions belong to a *seam* — the pair
    ``(contract_id, peer)`` — not to a book in the large. ``peer`` is
    the counterparty of the seam the revision was proposed across; the
    crossing path stamps it, the direct-write API leaves it ``None``
    (unstamped records predate the field and count on every seam of
    their family). The fold scopes by family, and by peer when the
    records carry one.
    """

    kind: str = "revision"
    contract_id: str = ""
    version: str = ""  # content hash of the statement (identity, §1.1)
    label: str | None = None  # display only; outside identity (§1.3)
    supersedes: str | None = None  # predecessor version hash; ordering (§1.4)
    statement_hash: str = ""  # == version; explicit for the reader (§6.3)
    proposed_by: str = ""
    proposed_at: float = 0.0
    position: str = "proposed"  # proposed | adopted | refused | withdrawn | revoked
    reason: str | None = None  # why refused / withdrawn / revoked
    related_crossing: str | None = None  # the proposal crossing's transmission id
    owner_consent: str | None = None  # §8.1 seam: recorded, never enforced
    peer: str | None = None  # v0.3.1: the seam counterparty (F3); None = unstamped
    statement: dict | list | str | None = None  # the bytes the version hashes

    def __post_init__(self) -> None:
        if self.position not in (
            "proposed", "adopted", "refused", "withdrawn", "revoked",
        ):
            raise ValueError(f"unknown revision position: {self.position!r}")
        if not self.statement_hash:
            self.statement_hash = self.version

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "contract_id": self.contract_id,
            "version": self.version,
            "label": self.label,
            "supersedes": self.supersedes,
            "statement_hash": self.statement_hash,
            "proposed_by": self.proposed_by,
            "proposed_at": self.proposed_at,
            "position": self.position,
            "reason": self.reason,
            "related_crossing": self.related_crossing,
            "owner_consent": self.owner_consent,
            "peer": self.peer,
            "statement": self.statement,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RevisionEntry":
        return cls(
            kind=d.get("kind", "revision"),
            contract_id=d.get("contract_id", ""),
            version=d["version"],
            label=d.get("label"),
            supersedes=d.get("supersedes"),
            statement_hash=d.get("statement_hash", d["version"]),
            proposed_by=d.get("proposed_by", ""),
            proposed_at=d.get("proposed_at", 0.0),
            position=d.get("position", "proposed"),
            reason=d.get("reason"),
            related_crossing=d.get("related_crossing"),
            owner_consent=d.get("owner_consent"),
            peer=d.get("peer"),
            statement=d.get("statement"),
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
        self.revisions: list[RevisionEntry] = []  # v0.3: the ex-ante half
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
        contract_id: str = "",
        contract_version: str | None = None,
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
            contract_id=contract_id,
            contract_version=contract_version,
        )
        self.ledger.append(e)
        self._append_file(e.to_dict())
        return e

    def book_revision(self, record: RevisionEntry) -> RevisionEntry:
        """Append a revision record — the ex-ante half of the ledger.

        Positions are booked, never edited: moving from "proposed" to
        "adopted" is a second record, and the fold reads the latest per
        version. Integrity is checked here so a record whose version is
        not the hash of the statement it carries cannot enter a book.
        """
        if record.statement is not None:
            h = statement_version(record.statement)
            if h != record.version:
                raise ValueError(
                    "revision record is not self-consistent: version "
                    f"{record.version} != sha256(statement) {h}"
                )
        self.revisions.append(record)
        self._append_file(record.to_dict())
        return record

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
            elif d.get("kind") == "revision":
                store.revisions.append(RevisionEntry.from_dict(d))
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
) -> tuple[list[str], list[str], list[str]]:
    """Gap 2 + v0.3 version awareness: shared state exists only in agreement.

    A crossing is *agreed* when one side's book holds a SEND and the
    other's holds a RECEIVE with the same transmission id, the same
    payload hash, **and the same cited contract version** (DESIGN-V03
    §6.6 — agreement includes the version). A crossing whose two
    bookings match on id and hash but name *different* versions is
    **skewed**: the books disagree about what governed it, which is a
    fact of its own, not a plain failure to agree — it is returned in
    its own category, never averaged into a single effective version
    (§3.4). Everything else is *unagreed*.

    Returns (agreed transmission ids, skewed ids, unagreed ids). Only
    SEND/RECEIVE pairings classify a crossing; SETTLE and
    RECEIVE-DISPUTED entries ride along in the books without pairing.
    """
    def key(e: LedgerEntry) -> tuple[str, str, str | None, str]:
        return (e.transmission_id, e.payload_hash, e.contract_version, e.side)

    keys_a = {key(e) for e in entries_a}
    keys_b = {key(e) for e in entries_b}
    all_ids = {tid for tid, _, _, _ in keys_a | keys_b}
    agreed, skewed, unagreed = [], [], []
    for tid in sorted(all_ids):
        status = "unagreed"
        hashes = {h for t, h, _, _ in keys_a | keys_b if t == tid}
        for h in sorted(hashes, key=str):
            def vers(keys, side):
                return {
                    v for t, hh, v, s in keys if t == tid and hh == h and s == side
                }

            send_a, recv_b = vers(keys_a, "SEND"), vers(keys_b, "RECEIVE")
            send_b, recv_a = vers(keys_b, "SEND"), vers(keys_a, "RECEIVE")
            if (send_a & recv_b) or (send_b & recv_a):
                status = "agreed"  # some version pairing matches exactly
                break
            if (send_a and recv_b) or (send_b and recv_a):
                # A SEND/RECEIVE pairing exists on this hash, but every
                # such pairing names different versions: skew, not noise.
                status = "skewed"
        (agreed if status == "agreed" else skewed if status == "skewed" else unagreed).append(tid)
    return agreed, skewed, unagreed
