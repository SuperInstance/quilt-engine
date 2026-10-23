"""Contract versioning — gap 3, deepened (v0.3, DESIGN-V03).

Ruling-neutral core only: everything here has a shape that does not
depend on the design's §8 open questions. Where a ruling will land,
there is a named seam and nothing decides:

- §8.1 (owner consent) — ``RevisionEntry.owner_consent``, recorded
  verbatim, enforced nowhere.
- §8.2 (dispute arbitration) — a fork is *detected* (``contract_state``
  surfaces ``fork``; ``adopt_revision`` refuses to fork the chain) and
  left open. No resolution route is chosen.
- §8.3 (label in identity) — labels ride outside the hashed bytes
  (``contract.statement_version``); the hash is the only identity.
- §8.4 (settlement-rule immutability) — settlements under any version
  stand (``version_report`` reports the regimes; nothing re-folds).
- §8.5 (multi-party) — adoption is pairwise throughout.
- §8.6 (revocation cause/descendants) — ``reason`` is optional on
  revocation, and revoking a version does not void its descendants
  (the chain walk steps over revoked links only when they are the tip).

Three pieces, in the design's order:

1. **Revision as a crossing (§2).** A proposal travels as an ordinary
   envelope (type ``contract/revision``) whose payload is
   self-sufficient: {contract_id, version, label, supersedes,
   statement}. Receipt is not adoption: the peer books RECEIVE for the
   crossing *and* a position record; adoption is a separate booking,
   and the revision governs only when both books hold it. The peer's
   answer crosses back as an adoption ack (``contract/adoption-ack``),
   and the proposer books its own adoption on receiving it.
2. **Superseded citations (§3).** ``resolve_citation`` is the §3.1
   table as a pure function: honour the version the crossing cites;
   superseded-but-held is applied under the cited version and flagged;
   revoked or unknown citations are refused, leaving the books'
   version fields mismatched — which ``agreed_crossings`` then surfaces
   as *skewed*, never averaged into an effective version.
3. **Per-book folds (§4).** ``contract_state`` folds one book's
   revision records into {proposals_seen, adopted, revoked, current,
   fork}; ``current`` is a set operation over the ``supersedes`` chain,
   never a clock. ``version_report`` is §4.3's report: crossings and
   settlements per version, which sit under since-superseded versions,
   and the version in force now. History is never re-folded.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

from .contract import statement_version
from .envelope import Ending, TransmissionEnvelope
from .state import LedgerEntry, RevisionEntry, StateStore

REVISION_TYPE = "contract/revision"
ADOPTION_ACK_TYPE = "contract/adoption-ack"
REVOCATION_TYPE = "contract/revocation"

UNVERSIONED = "(unversioned)"


# ---------------------------------------------------------- wire builders

def make_revision_proposal(
    statement: dict | list | str,
    *,
    contract_id: str,
    supersedes: str | None,
    cited_version: str | None,
    label: str | None = None,
    cost_budget: float = 0.0,
) -> TransmissionEnvelope:
    """A revision proposal crossing (DESIGN-V03 §2.1).

    The payload carries the whole revision — the statement travels, so
    the proposal is self-sufficient and one round trip delivers it. The
    proposal's *own* citation is ``(contract_id, cited_version)``: the
    version **in force when the proposal was made** (§2.1) — the
    proposal is made under the current contract; the new version it
    proposes rides in the payload. Conflating the two is the first
    mistake the design names.
    """
    version = statement_version(statement)
    return TransmissionEnvelope(
        payload={
            "contract_id": contract_id,
            "version": version,
            "label": label,
            "supersedes": supersedes,
            "statement": statement,
        },
        type=REVISION_TYPE,
        ending=Ending.PROJECT,
        cost_budget=cost_budget,
        contract_id=contract_id,
        contract_version=cited_version,
    )


def make_adoption_ack(
    proposal: TransmissionEnvelope,
    *,
    position: str,
    reason: str | None = None,
) -> TransmissionEnvelope:
    """The peer's answer to a revision proposal (§2.3's third booking).

    ``position`` is the answering book's position ("adopted" or
    "refused"). The ack's own citation is the version the *proposal*
    cited: the ack is a crossing of the seam contract, and until the
    proposer books this ack the seam is still bilaterally governed by
    that version (§2.4) — the ack is the receiver's best evidence of
    the intersection, and it cites it.
    """
    p = proposal.payload
    return TransmissionEnvelope(
        payload={
            "about": proposal.transmission_id,
            "contract_id": p["contract_id"],
            "version": p["version"],
            "position": position,
            "reason": reason,
        },
        type=ADOPTION_ACK_TYPE,
        ending=Ending.PROJECT,
        cost_budget=0.0,
        contract_id=p["contract_id"],
        contract_version=proposal.contract_version,
    )


def make_revocation(
    *, contract_id: str, version: str, cited_version: str | None,
    reason: str | None = None,
) -> TransmissionEnvelope:
    """A revocation crossing (§5.4): bilateral, version-local, its own act.

    ``reason`` is optional — whether a revocation needs a mandatory
    cause is §8.6 and stays open; the field is the seam.
    """
    return TransmissionEnvelope(
        payload={"contract_id": contract_id, "version": version, "reason": reason},
        type=REVOCATION_TYPE,
        ending=Ending.PROJECT,
        cost_budget=0.0,
        contract_id=contract_id,
        contract_version=cited_version,
    )


def make_statement_request(
    envelope: TransmissionEnvelope, *, contract_id: str, missing_version: str
) -> TransmissionEnvelope:
    """The clarify/narrow reuse for an unknown citation (§2.5).

    The receiver cannot apply a version whose statement it does not
    hold; it fails closed and asks. The question names exactly what is
    missing so the remedy — the statement arriving — is one crossing
    away. (Fetching it is the transport's business, not the seam's; §7.)
    """
    return TransmissionEnvelope(
        payload={
            "question": (
                f"statement not held: {contract_id}@{missing_version[:12]}… "
                "— send the canonical statement for the cited version"
            ),
            "about": envelope.transmission_id,
            "missing_statement_for": {
                "contract_id": contract_id,
                "version": missing_version,
            },
        },
        type=f"{envelope.type}/narrow",
        ending=Ending.PROJECT,
        cost_budget=0.0,
    )


# ------------------------------------------------- versioned seam bookings

def book_seam_send(
    book: StateStore, envelope: TransmissionEnvelope, peer: str,
    *, detail: dict[str, Any] | None = None,
) -> LedgerEntry:
    """SEND stamped with the citation the envelope carries (§6.1, §6.7).

    The version travels in the envelope; the sender's book records what
    it cited — a fact about the sender, not a constant it shares with
    the receiver.
    """
    return book.book(
        "SEND",
        transmission_id=envelope.transmission_id,
        peer=peer,
        payload_type=envelope.type,
        payload_hash=envelope.hash,
        ending=envelope.ending.value,
        cost=envelope.cost_budget,
        contract="",
        contract_id=envelope.contract_id or "",
        contract_version=envelope.contract_version,
        detail=dict(detail or {}),
    )


def book_seam_receive(
    book: StateStore, envelope: TransmissionEnvelope, peer: str,
    *, ruling: "CitationRuling", detail: dict[str, Any] | None = None,
) -> LedgerEntry:
    """RECEIVE stamped with the version THIS side applied (§2.5, §6.7).

    That is the whole fix for "the two books agree on the contract by
    construction": the receiver stamps what it applied — same as the
    citation when it honoured it, its own current on a revoked refusal,
    nothing at all when it could not apply any version.
    """
    d: dict[str, Any] = {}
    if ruling.superseded_citation:
        d["superseded_citation"] = True
    if ruling.citation_refusal:
        d["citation_refusal"] = ruling.citation_refusal
    d.update(detail or {})
    return book.book(
        "RECEIVE",
        transmission_id=envelope.transmission_id,
        peer=peer,
        payload_type=envelope.type,
        payload_hash=envelope.hash,
        ending=envelope.ending.value,
        cost=envelope.cost_budget,
        contract="",
        contract_id=envelope.contract_id or "",
        contract_version=ruling.applied_version,
        detail=d,
    )


def seam_context(book: StateStore, base_statement: dict | list | str) -> tuple[dict, set]:
    """(state fold, held versions) for a book whose seam began at ``base_statement``."""
    state = contract_state(book.revisions, statement_version(base_statement))
    held = set(held_statements(book.revisions, base_statement))
    return state, held


def current_version(book: StateStore, base_statement: dict | list | str) -> str:
    """This book's current version of the seam contract (§4.1 fold)."""
    return contract_state(book.revisions, statement_version(base_statement))["current"]


# ----------------------------------------------------- revision records

def propose_revision(
    book: StateStore, *,
    contract_id: str, statement: dict | list | str, supersedes: str | None,
    proposed_by: str, label: str | None = None,
    related_crossing: str | None = None, owner_consent: str | None = None,
) -> RevisionEntry:
    """The proposer's own ex-ante record: this version is on the table."""
    return book.book_revision(RevisionEntry(
        contract_id=contract_id,
        version=statement_version(statement),
        label=label,
        supersedes=supersedes,
        proposed_by=proposed_by,
        proposed_at=time.time(),
        position="proposed",
        related_crossing=related_crossing,
        owner_consent=owner_consent,
        statement=statement,
    ))


def adopt_revision(
    book: StateStore, *,
    contract_id: str, version: str, statement: dict | list | str,
    supersedes: str | None, proposed_by: str, label: str | None = None,
    related_crossing: str | None = None, owner_consent: str | None = None,
) -> RevisionEntry:
    """Book this book's adoption of a version (§2.3's second booking).

    Refused loudly — nothing booked — when the adoption would **fork
    the chain** (§5.2): this book already holds an adopted version with
    the same predecessor. Only one successor of a predecessor can be in
    force; which one *ought* to win when both sides hold different ones
    is a genuine dispute the mechanism detects and does not arbitrate
    (§8.2 — the seam is this refusal plus the ``fork`` field of
    ``contract_state``, where an arbitrator's ruling would land).

    A successful adoption implicitly refuses this book's competing
    *pending* proposals for the same slot (§5.1): two live claims about
    one predecessor is the ambiguity worse than a refusal.
    """
    latest: dict[str, RevisionEntry] = {}
    for r in book.revisions:
        latest[r.version] = r
    for r in latest.values():
        if (
            r.position == "adopted"
            and r.supersedes == supersedes
            and r.version != version
        ):
            raise ValueError(
                f"fork refused: {supersedes} already superseded by {r.version}; "
                "only one successor of a predecessor can be in force (§5.2)"
            )
    record = book.book_revision(RevisionEntry(
        contract_id=contract_id,
        version=version,
        label=label,
        supersedes=supersedes,
        proposed_by=proposed_by,
        proposed_at=time.time(),
        position="adopted",
        related_crossing=related_crossing,
        owner_consent=owner_consent,
        statement=statement,
    ))
    for r in latest.values():
        if (
            r.position == "proposed"
            and r.supersedes == supersedes
            and r.version != version
        ):
            book.book_revision(RevisionEntry(
                contract_id=contract_id,
                version=r.version,
                label=r.label,
                supersedes=r.supersedes,
                proposed_by=r.proposed_by,
                proposed_at=r.proposed_at,
                position="refused",
                reason=f"superseded by the adoption of {version}",
                related_crossing=r.related_crossing,
                statement=r.statement,
            ))
    return record


def refuse_revision(
    book: StateStore, *,
    contract_id: str, version: str, reason: str,
    statement: dict | list | str | None = None,
    supersedes: str | None = None, proposed_by: str = "",
    label: str | None = None, related_crossing: str | None = None,
) -> RevisionEntry:
    """The peer must answer every proposal — adopted or refused, with a
    reason (§5.1): without the answer, "not yet" and "no" are
    indistinguishable, and the ambiguity is worse than the refusal."""
    return book.book_revision(RevisionEntry(
        contract_id=contract_id, version=version, label=label,
        supersedes=supersedes, proposed_by=proposed_by,
        proposed_at=time.time(), position="refused", reason=reason,
        related_crossing=related_crossing, statement=statement,
    ))


def withdraw_revision(
    book: StateStore, record: RevisionEntry,
) -> RevisionEntry:
    """The proposer may withdraw its own pending proposal (§5.1).

    Withdrawn proposals have no effect and are not refusals."""
    return book.book_revision(RevisionEntry(
        contract_id=record.contract_id, version=record.version,
        label=record.label, supersedes=record.supersedes,
        proposed_by=record.proposed_by, proposed_at=record.proposed_at,
        position="withdrawn", related_crossing=record.related_crossing,
        statement=record.statement,
    ))


def revoke_version(
    book: StateStore, *,
    contract_id: str, version: str,
    reason: str | None = None, proposed_by: str = "",
) -> RevisionEntry:
    """Book a revocation record (§5.4): the chain falls back to its
    nearest live ancestor; settlements under the revoked version stand.

    Whether a cause is mandatory and whether revoking an ancestor voids
    its descendants are §8.6 and stay open: ``reason`` is optional, and
    the fold treats a revoked *tip* as absent without touching
    descendants that stand on their own."""
    return book.book_revision(RevisionEntry(
        contract_id=contract_id, version=version,
        proposed_by=proposed_by, proposed_at=time.time(),
        position="revoked", reason=reason,
    ))


# ----------------------------------------------- proposal crossing handlers

def send_proposal(
    book: StateStore, envelope: TransmissionEnvelope, peer: str, *,
    proposed_by: str,
) -> tuple[LedgerEntry, RevisionEntry]:
    """The proposer's two acts (§2.3 row 1–2): book SEND of the crossing
    and the ``proposed`` record. The version is not in force — the
    proposal is shared history, nothing more (§5.1)."""
    entry = book_seam_send(book, envelope, peer)
    p = envelope.payload
    record = propose_revision(
        book,
        contract_id=p["contract_id"],
        statement=p["statement"],
        supersedes=p.get("supersedes"),
        proposed_by=proposed_by,
        label=p.get("label"),
        related_crossing=envelope.transmission_id,
    )
    return entry, record


def receive_proposal(
    book: StateStore, envelope: TransmissionEnvelope, peer: str, *,
    base_statement: dict | list | str, adopt: bool = True,
    refuse_reason: str | None = None,
) -> tuple[LedgerEntry, RevisionEntry, TransmissionEnvelope]:
    """The peer's side of a proposal crossing (§2.3 rows 1–3).

    Receipt and adoption are separate bookings. The crossing itself is
    honoured under the version *it* cited (§2.5); the position record
    is this book's own act. Default policy adopts; callers refusing
    must say why (§5.1). A proposal whose version is not the hash of
    its statement, or whose adoption would fork the chain, is refused
    with the reason — the fork is recorded, not decided (§8.2).
    """
    state, held = seam_context(book, base_statement)
    ruling = resolve_citation(envelope.contract_version, state=state, held=held)
    entry = book_seam_receive(book, envelope, peer, ruling=ruling)

    p = envelope.payload
    contract_id, version, statement = p["contract_id"], p["version"], p["statement"]
    supersedes, label = p.get("supersedes"), p.get("label")
    reason: str | None = refuse_reason
    if statement_version(statement) != version:
        reason = "version is not the hash of the statement"
    elif adopt and reason is None:
        try:
            record = adopt_revision(
                book, contract_id=contract_id, version=version,
                statement=statement, supersedes=supersedes,
                proposed_by=peer, label=label,
                related_crossing=envelope.transmission_id,
            )
            return entry, record, make_adoption_ack(envelope, position="adopted")
        except ValueError as fork:
            reason = str(fork)
    if reason is None:
        reason = "refused by policy"
    record = refuse_revision(
        book, contract_id=contract_id, version=version, reason=reason,
        statement=statement, supersedes=supersedes, proposed_by=peer,
        label=label, related_crossing=envelope.transmission_id,
    )
    return entry, record, make_adoption_ack(envelope, position="refused", reason=reason)


def receive_ack(
    book: StateStore, envelope: TransmissionEnvelope, peer: str, *,
    base_statement: dict | list | str,
) -> tuple[LedgerEntry, RevisionEntry | None]:
    """The proposer's side of the adoption ack (§2.3 rows 3–4).

    The ack crossing is honoured under the version it cites; if the ack
    says the peer adopted and this book holds the proposed statement,
    this book books its own adoption — the revision governs only now,
    when both books hold it. A refused ack books the refusal with the
    peer's reason, verbatim.
    """
    state, held = seam_context(book, base_statement)
    ruling = resolve_citation(envelope.contract_version, state=state, held=held)
    entry = book_seam_receive(book, envelope, peer, ruling=ruling)
    p = envelope.payload
    pending = next(
        (r for r in book.revisions
         if r.version == p["version"] and r.position == "proposed"),
        None,
    )
    if pending is None:
        return entry, None
    if p.get("position") == "adopted" and pending.statement is not None:
        record = adopt_revision(
            book, contract_id=pending.contract_id, version=pending.version,
            statement=pending.statement, supersedes=pending.supersedes,
            proposed_by=pending.proposed_by, label=pending.label,
            related_crossing=pending.related_crossing,
        )
    else:
        record = refuse_revision(
            book, contract_id=pending.contract_id, version=pending.version,
            reason=p.get("reason") or "peer refused", statement=pending.statement,
            supersedes=pending.supersedes, proposed_by=pending.proposed_by,
            label=pending.label, related_crossing=pending.related_crossing,
        )
    return entry, record


# ------------------------------------------------------ the §3.1 citation table

@dataclass
class CitationRuling:
    """What a receiver decided about a crossing's cited version (§3.1).

    ``applied_version`` is what the receiver stamps in its RECEIVE: the
    cited version when honoured; the receiver's current on a revoked
    refusal; ``None`` when no version could be applied. A refused or
    superseded ruling means the books' version fields will mismatch —
    ``agreed_crossings`` surfaces that as *skewed*, and ``divergence``
    says a version_divergence event belongs in the seam log (§3.4).
    """

    action: str  # "apply" | "refuse"
    applied_version: str | None
    superseded_citation: bool = False
    citation_refusal: str | None = None  # "revoked" | "statement not held"
    divergence: bool = False


def resolve_citation(
    cited_version: str | None, *, state: dict, held: set,
) -> CitationRuling:
    """Honour the version the crossing cites (§3.1's table, verbatim).

    - the receiver's current: apply it;
    - **superseded** but held, not revoked: **apply it anyway**, under
      the *cited* version — superseded is not revoked, and re-judging
      under the current version would falsify the record (§3.3);
    - **revoked**: refuse — books the receiver's current version;
    - **unknown** (statement not held): refuse — books nothing, and the
      remedy is the statement arriving (one round trip on the boundary).

    An unversioned citation (``None``) is the v0.1 path: applied as-is.
    A held version that is neither current, revoked, nor superseded
    (e.g. a pending proposal's) is honoured under the cited version,
    unflagged — it is not in the §3.1 table, and honouring the citation
    is the rule that table generalises.
    """
    current = state["current"]
    if cited_version is None or cited_version == current:
        return CitationRuling("apply", cited_version)
    if cited_version in state["revoked"]:
        return CitationRuling(
            "refuse", current, citation_refusal="revoked", divergence=True,
        )
    if cited_version not in held:
        return CitationRuling(
            "refuse", None, citation_refusal="statement not held", divergence=True,
        )
    return CitationRuling(
        "apply", cited_version,
        superseded_citation=cited_version in state["superseded"],
    )


# ----------------------------------------------------------------- folds

def contract_state(
    revisions: Iterable[RevisionEntry], base_version: str,
) -> dict:
    """One book's fold of its revision records (§4.1).

    ``current`` is a set operation, not a timestamp: among the versions
    this book has adopted and not revoked, the current one is the tip
    no other adopted, non-revoked version supersedes (transitively,
    stepping over revoked links — revocation is version-local, §5.4).
    No adopted tips: the base version (the seam's pinned start). More
    than one tip: a **fork** — the seam stays on the base, and the fork
    is surfaced, not resolved (§5.2 / §8.2).
    """
    latest: dict[str, RevisionEntry] = {}
    for r in revisions:
        latest[r.version] = r
    adopted = {v for v, r in latest.items() if r.position == "adopted"}
    revoked = {v for v, r in latest.items() if r.position == "revoked"}
    alive = adopted - revoked

    superseded: set[str] = set()
    for v in alive:
        cur = latest[v].supersedes
        walked: set[str] = set()
        while cur is not None and cur not in walked:
            superseded.add(cur)
            walked.add(cur)
            pred = latest.get(cur)
            cur = pred.supersedes if pred is not None else None

    tips = alive - superseded
    fork = sorted(tips) if len(tips) > 1 else []
    if len(tips) == 1:
        current = next(iter(tips))
    else:
        # no live tips (or a fork): the pinned base is what governs —
        # H1 was never revoked, so it returns without a re-proposal (§5.4)
        current = base_version
    return {
        "base": base_version,
        "proposals_seen": sorted(latest),
        "adopted": sorted(alive),
        "revoked": sorted(revoked),
        "superseded": sorted(superseded),
        "current": current,
        "fork": fork,
    }


def held_statements(
    revisions: Iterable[RevisionEntry], base_statement: dict | list | str,
) -> dict[str, dict | list | str]:
    """Every statement this book holds: the pinned base plus one per
    revision record that carried one (§2.5: statements are retained —
    honouring a superseded citation requires holding its bytes)."""
    out = {statement_version(base_statement): base_statement}
    for r in revisions:
        if r.statement is not None:
            out[r.version] = r.statement
    return out


def version_report(
    ledger: Iterable[LedgerEntry], revisions: Iterable[RevisionEntry],
    base_version: str,
) -> dict:
    """§4.3's report: crossings and settlements per version, which sit
    under since-superseded versions, and the version in force now.

    A revision does not re-fold history — this report *describes* the
    regimes the ledger already records; correcting a settlement is a
    compensating entry, not a re-fold, and nothing here re-judges one.
    """
    ledger = list(ledger)
    state = contract_state(revisions, base_version)
    by_version: dict[str, dict[str, Any]] = {}
    for e in ledger:
        v = e.contract_version if e.contract_version is not None else UNVERSIONED
        slot = by_version.setdefault(v, {"bookings": 0, "settlements": []})
        slot["bookings"] += 1
        if e.side == "SETTLE":
            slot["settlements"].append(e.transmission_id)
    for v, slot in by_version.items():
        if v != UNVERSIONED:
            slot["superseded"] = v in state["superseded"] and v != state["current"]
            slot["revoked"] = v in state["revoked"]
    return {
        "current": state["current"],
        "bookings_by_version": by_version,
        "settlements_under_superseded": sorted(
            e.transmission_id
            for e in ledger
            if e.side == "SETTLE"
            and e.contract_version is not None
            and e.contract_version != state["current"]
            and e.contract_version in state["superseded"]
        ),
    }
