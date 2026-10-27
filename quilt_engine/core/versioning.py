"""Contract versioning — gap 3, deepened (v0.3, DESIGN-V03).

Ruling-neutral core only: everything here has a shape that does not
depend on the design's §8 open questions. Where a ruling will land,
there is a named seam and nothing decides:

- §8.1 (owner consent) — ``RevisionEntry.owner_consent``, recorded
  verbatim, enforced nowhere. Since v0.3.1 a "yes" ruling has a single
  place to land: the held-but-not-adopted branch of
  ``resolve_citation`` (plus the adopting record itself in
  ``adopt_revision``) — honouring a version this book never adopted is
  exactly the act consent would gate.
- §8.2 (dispute arbitration) — a fork is *detected* (``contract_state``
  surfaces ``fork``, including forks among *pending* claims;
  ``adopt_revision`` refuses to fork the chain and, since v0.3.1,
  refuses to adopt at all while an outstanding concurrent proposal
  claims the same predecessor, §5.2) and left open. No resolution
  route is chosen.
- §8.3 (label in identity) — labels ride outside the hashed bytes
  (``contract.statement_version``); the hash is the only identity.
- §8.4 (settlement-rule immutability) — settlements under any version
  stand (``version_report`` reports the regimes; nothing re-folds).
- §8.5 (multi-party) — adoption is pairwise throughout, and since
  v0.3.1 the fold is scoped by ``(contract_id, seam peer)`` with acks
  matched to the proposal's SEND peer (CC1 F3): one bilateral cycle
  with one peer binds that seam only.
- §8.6 (revocation cause/descendants) — ``reason`` is optional on
  revocation, and revoking a version does not void its descendants
  (the chain walk steps over revoked links only when they are the tip).
  Since v0.3.1 revocation records preserve ``supersedes`` (the chain
  survives a revoked middle link, CC1 F4), a revoked hash cannot be
  re-adopted and the fold keeps the revocation visible — the scar rule
  (CC1 F5) — and revocation has a bilateral crossing path
  (``send_revocation`` / ``receive_revocation``, CC1 F6).

Three pieces, in the design's order:

1. **Revision as a crossing (§2).** A proposal travels as an ordinary
   envelope (type ``contract/revision``) whose payload is
   self-sufficient: {contract_id, version, label, supersedes,
   statement}. Receipt is not adoption: the peer books RECEIVE for the
   crossing *and* a position record; adoption is a separate booking,
   and the revision governs only when both books hold it. The peer's
   answer crosses back as an adoption ack (``contract/adoption-ack``),
   and the proposer books its own adoption on receiving it. Concurrent
   proposals (§5.2) neither resolve nor vanish: each side books the
   other's proposal as still-pending and answers ``proposed`` — the
   conflict notification is itself a crossing, the fork stays visible
   in both folds, and the seam keeps running on the common predecessor.
2. **Superseded citations (§3).** ``resolve_citation`` is the §3.1
   table as a pure function: honour the version the crossing cites;
   superseded-but-held is applied under the cited version and flagged;
   revoked, refused, or unknown citations are refused, leaving the
   books' version fields mismatched — which ``agreed_crossings`` then
   surfaces as *skewed*, never averaged into an effective version.
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


class PendingSiblingError(ValueError):
    """Adoption blocked by §5.2: a *concurrent* proposal — a pending
    claim on the same predecessor from a different proposer — is
    outstanding. Neither side adopts while the other's proposal is
    outstanding; catching this is how the crossing path books the
    stand-off instead of resolving the fork by order of arrival (CC1
    F2). Subclasses ``ValueError`` so direct callers that treat every
    refusal as ``ValueError`` keep working."""

    def __init__(self, message: str, *, sibling_versions: list[str]) -> None:
        super().__init__(message)
        self.sibling_versions = sibling_versions


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

    ``position`` is the answering book's position: "adopted", "refused"
    — or, since v0.3.1, "proposed", the §5.2 stand-off answer (CC1 F2):
    the answering book holds the proposal as pending behind its own
    concurrent claim, has taken no position, and says so; the conflict
    notification is itself a crossing. The ack's own citation is the
    version the *proposal* cited: the ack is a crossing of the seam
    contract, and until the proposer books this ack the seam is still
    bilaterally governed by that version (§2.4) — the ack is the
    receiver's best evidence of the intersection, and it cites it.
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


def seam_context(
    book: StateStore, base_statement: dict | list | str,
    *, contract_id: str | None = None, peer: str | None = None,
) -> tuple[dict, set]:
    """(state fold, held versions) for a book whose seam began at
    ``base_statement``.

    v0.3.1 (CC1 F3): the fold is scoped to one seam — ``contract_id``
    (the family; strongly recommended, an unrelated family's revisions
    must not fork this seam's fold) and ``peer`` (the counterparty;
    records that predate the field are unstamped and count everywhere).
    """
    state = contract_state(
        book.revisions, statement_version(base_statement),
        contract_id=contract_id, peer=peer,
    )
    held = set(held_statements(book.revisions, base_statement))
    return state, held


def current_version(
    book: StateStore, base_statement: dict | list | str,
    *, contract_id: str | None = None,
) -> str:
    """This book's current version of the seam contract (§4.1 fold)."""
    return contract_state(
        book.revisions, statement_version(base_statement),
        contract_id=contract_id,
    )["current"]


# ----------------------------------------------------- revision records

def propose_revision(
    book: StateStore, *,
    contract_id: str, statement: dict | list | str, supersedes: str | None,
    proposed_by: str, label: str | None = None,
    related_crossing: str | None = None, owner_consent: str | None = None,
    peer: str | None = None,
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
        peer=peer,
        statement=statement,
    ))


def _latest_records(
    book: StateStore, *, contract_id: str, peer: str | None = None,
) -> dict[str, RevisionEntry]:
    """Latest record per version, scoped to one seam (CC1 F3).

    Family scoping is strict: an unrelated family's revisions never
    enter this fold. Peer scoping is lenient — records without a peer
    (direct-write API, pre-v0.3.1 ledgers) count on every seam of the
    family, because their seam is not recorded.
    """
    scoped = [
        r for r in book.revisions
        if r.contract_id == contract_id
        and (peer is None or r.peer in (None, "", peer))
    ]
    latest: dict[str, RevisionEntry] = {}
    for r in scoped:
        latest[r.version] = r
    return latest


def adopt_revision(
    book: StateStore, *,
    contract_id: str, version: str, statement: dict | list | str,
    supersedes: str | None, proposed_by: str, label: str | None = None,
    related_crossing: str | None = None, owner_consent: str | None = None,
    peer: str | None = None,
) -> RevisionEntry:
    """Book this book's adoption of a version (§2.3's second booking).

    Refused loudly — nothing booked — when:

    - the adoption would **fork the chain** (§5.2): this seam already
      holds an adopted version with the same predecessor; or
    - a **concurrent proposal** claims the same predecessor (§5.2,
      ``PendingSiblingError``): a pending record proposed by *another*
      proposer is outstanding. Neither side adopts while the other's
      proposal is outstanding; which side *ought* to win is a genuine
      dispute the mechanism detects and does not arbitrate (§8.2 — the
      seam is these refusals plus the ``fork`` field of
      ``contract_state``, where an arbitrator's ruling would land);
    - the version has been **revoked** (§5.4, the scar rule): a
      re-proposal after a revocation is a new statement — a different
      hash — not a restoration of the revoked one.

    A successful adoption implicitly refuses this book's competing
    *pending* proposals for the same slot **from the same proposer**
    (§5.1: two live claims from one source is the proposer's own mess,
    and adopting one retires the others). Pending claims from another
    proposer never get here — the §5.2 block refused the adoption
    first. (§5.1's blanket "one adoption refuses the competing pending
    proposals" and §5.2's "neither side can adopt while the other's
    proposal is outstanding" conflict on exactly this point; v0.3.1
    implements §5.2 for cross-proposer claims — the specific rule for
    concurrent proposals — and keeps §5.1 for same-proposer claims.
    Flagged for the author in FIX-REPORT.md.)
    """
    latest = _latest_records(book, contract_id=contract_id, peer=peer)
    scarred = {
        r.version for r in book.revisions
        if r.contract_id == contract_id and r.position == "revoked"
    }
    if version in scarred:
        raise ValueError(
            f"revoked version {version} cannot be re-adopted: a "
            "re-proposal after a revocation is a new statement, not a "
            "restoration (§5.4, the scar rule)"
        )
    for r in latest.values():
        if (
            r.position == "adopted"
            and r.version not in scarred
            and r.supersedes == supersedes
            and r.version != version
        ):
            raise ValueError(
                f"fork refused: {supersedes} already superseded by {r.version}; "
                "only one successor of a predecessor can be in force (§5.2)"
            )
    concurrent = sorted(
        r.version for r in latest.values()
        if r.position == "proposed"
        and r.supersedes == supersedes
        and r.version != version
        and r.proposed_by != proposed_by
    )
    if concurrent:
        raise PendingSiblingError(
            "concurrent proposal pending: "
            f"{supersedes} is already claimed by pending proposal(s) "
            f"{concurrent} from another proposer; neither side adopts "
            "while the other's proposal is outstanding (§5.2)",
            sibling_versions=concurrent,
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
        peer=peer,
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
                peer=r.peer,
                statement=r.statement,
            ))
    return record


def refuse_revision(
    book: StateStore, *,
    contract_id: str, version: str, reason: str,
    statement: dict | list | str | None = None,
    supersedes: str | None = None, proposed_by: str = "",
    label: str | None = None, related_crossing: str | None = None,
    peer: str | None = None,
) -> RevisionEntry:
    """The peer must answer every proposal — adopted or refused, with a
    reason (§5.1): without the answer, "not yet" and "no" are
    indistinguishable, and the ambiguity is worse than the refusal."""
    return book.book_revision(RevisionEntry(
        contract_id=contract_id, version=version, label=label,
        supersedes=supersedes, proposed_by=proposed_by,
        proposed_at=time.time(), position="refused", reason=reason,
        related_crossing=related_crossing, peer=peer, statement=statement,
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
        peer=record.peer, statement=record.statement,
    ))


def revoke_version(
    book: StateStore, *,
    contract_id: str, version: str,
    reason: str | None = None, proposed_by: str = "",
) -> RevisionEntry:
    """Book a revocation record (§5.4): the chain falls back to its
    nearest live ancestor; settlements under the revoked version stand.

    **Local write, not the seam-level API.** §5.4: "A revocation is a
    bilateral act… It is not a unilateral local edit." Writing this
    record into one book leaves the two books disagreeing about whether
    the version governs — visible, but half-booked. The bilateral path
    is ``send_revocation`` / ``receive_revocation``, which book the
    crossing on both sides and this record on both books. This function
    is the local half those two compose; calling it directly is a
    unilateral act and says so here.

    v0.3.1 (CC1 F4): the record **preserves ``supersedes``** (and the
    label and seam peer) from the version's prior record, so revoking a
    middle link does not break the chain walk — a revoked H2 between
    H1 and H3 leaves H1 correctly superseded and H1 citations correctly
    flagged.

    Whether a cause is mandatory and whether revoking an ancestor voids
    its descendants are §8.6 and stay open: ``reason`` is optional, and
    the fold treats a revoked *tip* as absent without touching
    descendants that stand on their own."""
    prev = _latest_records(book, contract_id=contract_id).get(version)
    return book.book_revision(RevisionEntry(
        contract_id=contract_id, version=version,
        label=prev.label if prev is not None else None,
        supersedes=prev.supersedes if prev is not None else None,
        proposed_by=proposed_by, proposed_at=time.time(),
        position="revoked", reason=reason,
        peer=prev.peer if prev is not None else None,
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
        peer=peer,
    )
    return entry, record


def receive_proposal(
    book: StateStore, envelope: TransmissionEnvelope, peer: str, *,
    base_statement: dict | list | str, adopt: bool = True,
    refuse_reason: str | None = None,
    parties: Iterable[str] | None = None,
) -> tuple[LedgerEntry, RevisionEntry, TransmissionEnvelope]:
    """The peer's side of a proposal crossing (§2.3 rows 1–3).

    Receipt and adoption are separate bookings. The crossing itself is
    honoured under the version *it* cited (§2.5); the position record
    is this book's own act. Default policy adopts; callers refusing
    must say why (§5.1). A proposal whose version is not the hash of
    its statement, whose adoption would fork the chain, or — since
    v0.3.1 — that the book cannot adopt because a concurrent proposal
    from another proposer claims the same predecessor, is refused (or,
    for the concurrent case, held) with the reason — the fork is
    recorded, not decided (§8.2).

    v0.3.1 additions:

    - **§5.2 stand-off (CC1 F2).** When adoption is blocked by the
      other side's outstanding proposal, the book neither adopts nor
      refuses: it books the received proposal as still *pending* and
      answers ``position="proposed"`` — the conflict notification is
      itself a crossing (§5.2, resolution route 1's trigger). The
      stand-off leaves both proposals pending on both books, the fork
      visible in both folds, and the seam on the common predecessor.
    - **§2.2/§5.5 standing (CC1 F3).** ``parties`` is the seam's party
      set, supplied by the seam host that knows its peers. A proposal
      delivered by a peer outside it is refused — no standing to revise
      an agreement you are not party to. ``None`` (the default) keeps
      the v0.3 behaviour: the core cannot derive party-ness from a
      book alone, so the check is the seam host's to enable.
    """
    p = envelope.payload
    contract_id = p["contract_id"]
    state, held = seam_context(
        book, base_statement, contract_id=contract_id, peer=peer,
    )
    ruling = resolve_citation(envelope.contract_version, state=state, held=held)
    entry = book_seam_receive(book, envelope, peer, ruling=ruling)

    version, statement = p["version"], p["statement"]
    supersedes, label = p.get("supersedes"), p.get("label")
    reason: str | None = refuse_reason
    if parties is not None and peer not in set(parties):
        reason = (
            f"no standing: {peer} is not a party to the {contract_id} "
            "seam (§2.2/§5.5)"
        )
    elif statement_version(statement) != version:
        reason = "version is not the hash of the statement"
    elif adopt and reason is None:
        try:
            record = adopt_revision(
                book, contract_id=contract_id, version=version,
                statement=statement, supersedes=supersedes,
                proposed_by=peer, label=label,
                related_crossing=envelope.transmission_id, peer=peer,
            )
            return entry, record, make_adoption_ack(envelope, position="adopted")
        except PendingSiblingError as standoff:
            # §5.2: the peer's proposal stays pending on this book; the
            # answer says so instead of picking a winner by arrival.
            record = propose_revision(
                book, contract_id=contract_id, statement=statement,
                supersedes=supersedes, proposed_by=peer, label=label,
                related_crossing=envelope.transmission_id, peer=peer,
            )
            return entry, record, make_adoption_ack(
                envelope, position="proposed", reason=str(standoff),
            )
        except ValueError as fork:
            reason = str(fork)
    if reason is None:
        reason = "refused by policy"
    record = refuse_revision(
        book, contract_id=contract_id, version=version, reason=reason,
        statement=statement, supersedes=supersedes, proposed_by=peer,
        label=label, related_crossing=envelope.transmission_id, peer=peer,
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

    v0.3.1 additions:

    - **The ack's peer must be the proposal's SEND peer (CC1 F3).**
      This book chose who its proposal went to; an ack attributed to
      anyone else completes nothing — the RECEIVE is still booked (the
      crossing happened), an event is logged, and the proposal stays
      pending. An ack about a proposal with no SEND record on this
      book (a direct-write record) has no peer evidence and is not
      matched either way.
    - **A stand-off ack (``position="proposed"``, CC1 F2)** — the
      peer's answer to a concurrent proposal — books nothing: this
      book's own proposal stays pending, which is the honest state of
      a fork nobody is allowed to resolve by adopting.
    - **No exception escapes (CC1 F2).** The adoption is attempted
      after the RECEIVE is booked, so a refusal there used to leave
      the book half-booked with the exception in flight. Now a fork or
      scar refusal from ``adopt_revision`` is caught and *booked* as
      this book's refusal, with the reason verbatim; a concurrent
      block leaves the proposal pending and logs the stand-off.
    """
    p = envelope.payload
    state, held = seam_context(
        book, base_statement, contract_id=p.get("contract_id"), peer=peer,
    )
    ruling = resolve_citation(envelope.contract_version, state=state, held=held)
    entry = book_seam_receive(book, envelope, peer, ruling=ruling)
    pending = next(
        (r for r in book.revisions
         if r.version == p["version"] and r.position == "proposed"
         and (not p.get("contract_id") or r.contract_id == p["contract_id"])),
        None,
    )
    if pending is None:
        return entry, None
    send_peer = next(
        (e.peer for e in book.ledger
         if e.transmission_id == pending.related_crossing
         and e.side == "SEND"),
        None,
    ) if pending.related_crossing is not None else None
    if send_peer is not None and send_peer != peer:
        # F3: an ack from anyone other than the book the proposal was
        # sent to is not this proposal's answer. Book the crossing,
        # complete nothing, say so.
        book.log_event(
            {
                "event": "ack_from_non_party",
                "version": p["version"],
                "about": p.get("about"),
                "ack_peer": peer,
                "proposal_send_peer": send_peer,
                "note": "ack ignored: adoption completes only on the "
                "proposal's SEND peer (§2.2)",
            }
        )
        return entry, None
    if p.get("position") == "proposed":
        # F2 / §5.2 stand-off answer: the peer holds our proposal as
        # pending behind its own competing claim. Nothing completes;
        # the fork stays open and visible in both folds.
        book.log_event(
            {
                "event": "concurrent_proposal_standoff",
                "version": p["version"],
                "reason": p.get("reason"),
                "note": "proposal remains pending: the peer cannot adopt "
                "while its own concurrent proposal is outstanding (§5.2)",
            }
        )
        return entry, None
    if p.get("position") == "adopted" and pending.statement is not None:
        try:
            record = adopt_revision(
                book, contract_id=pending.contract_id, version=pending.version,
                statement=pending.statement, supersedes=pending.supersedes,
                proposed_by=pending.proposed_by, label=pending.label,
                related_crossing=pending.related_crossing, peer=peer,
            )
        except PendingSiblingError as standoff:
            book.log_event(
                {
                    "event": "concurrent_proposal_standoff",
                    "version": p["version"],
                    "reason": str(standoff),
                    "note": "proposal remains pending: adoption blocked by "
                    "an outstanding concurrent proposal (§5.2)",
                }
            )
            return entry, None
        except ValueError as refused:
            # F2: the RECEIVE is already booked; the refusal must be a
            # record, not an exception escaping after the fact.
            record = refuse_revision(
                book, contract_id=pending.contract_id,
                version=pending.version, reason=str(refused),
                statement=pending.statement, supersedes=pending.supersedes,
                proposed_by=pending.proposed_by, label=pending.label,
                related_crossing=pending.related_crossing, peer=peer,
            )
    else:
        record = refuse_revision(
            book, contract_id=pending.contract_id, version=pending.version,
            reason=p.get("reason") or "peer refused", statement=pending.statement,
            supersedes=pending.supersedes, proposed_by=pending.proposed_by,
            label=pending.label, related_crossing=pending.related_crossing,
            peer=peer,
        )
    return entry, record


def send_revocation(
    book: StateStore, envelope: TransmissionEnvelope, peer: str, *,
    proposed_by: str,
) -> tuple[LedgerEntry, RevisionEntry]:
    """The revoker's two acts (§5.4): book SEND of the ``contract/
    revocation`` crossing and this book's revocation record.

    Revocation is a bilateral act. This is the seam-level API;
    ``revoke_version`` alone writes one book and leaves the seam's two
    books disagreeing about whether the version governs. The peer's
    half is ``receive_revocation``."""
    entry = book_seam_send(book, envelope, peer)
    p = envelope.payload
    record = revoke_version(
        book, contract_id=p["contract_id"], version=p["version"],
        reason=p.get("reason"), proposed_by=proposed_by,
    )
    return entry, record


def receive_revocation(
    book: StateStore, envelope: TransmissionEnvelope, peer: str, *,
    base_statement: dict | list | str,
) -> tuple[LedgerEntry, RevisionEntry]:
    """The peer's side of a revocation crossing (§5.4): book RECEIVE of
    the crossing — honoured under the version *it* cites, which is the
    version about to be withdrawn — and the peer's own revocation
    record. Only when both books hold the revocation does the version
    stop governing bilaterally; each book's fold falls back to the
    nearest live ancestor on its own clock, and the two land together
    because both hold the same two records."""
    p = envelope.payload
    state, held = seam_context(
        book, base_statement, contract_id=p["contract_id"], peer=peer,
    )
    ruling = resolve_citation(envelope.contract_version, state=state, held=held)
    entry = book_seam_receive(book, envelope, peer, ruling=ruling)
    record = revoke_version(
        book, contract_id=p["contract_id"], version=p["version"],
        reason=p.get("reason") or "revoked by the peer's crossing",
        proposed_by=peer,
    )
    book.log_event(
        {
            "event": "revocation_received",
            "contract_id": p["contract_id"],
            "version": p["version"],
            "reason": p.get("reason"),
            "now_on": state["current"],
        }
    )
    return entry, record


# ------------------------------------------------------ the §3.1 citation table

@dataclass
class CitationRuling:
    """What a receiver decided about a crossing's cited version (§3.1).

    ``applied_version`` is what the receiver stamps in its RECEIVE: the
    cited version when honoured; the receiver's current on a revoked or
    refused citation; ``None`` when no version could be applied. A
    refused or pending-flagged ruling means the books' version fields
    will mismatch or disagree — ``agreed_crossings`` surfaces the
    former as *skewed*, and ``divergence`` says a version_divergence
    event belongs in the seam log (§3.4).
    """

    action: str  # "apply" | "refuse"
    applied_version: str | None
    superseded_citation: bool = False
    # "revoked" | "refused" (v0.3.1, CC1 F1) | "statement not held"
    citation_refusal: str | None = None
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
    - **refused by this book** (v0.3.1, CC1 F1): refuse — §2.4 makes
      the governing version *the intersection* of what both books
      hold, and a version this book refused is not in it. A refusal
      must mean something to the sender's crossings too; books the
      receiver's current version, refusal reason ``"refused"``;
    - **unknown** (statement not held): refuse — books nothing, and the
      remedy is the statement arriving (one round trip on the boundary).

    An unversioned citation (``None``) is the v0.1 path: applied as-is.
    A held version that is neither current, revoked, refused, nor
    superseded — a *pending* proposal's — is honoured under the cited
    version **and flagged** (``divergence=True``): deviation 5's
    generalisation kept, but never silent (CC1 F1).
    """
    current = state["current"]
    if cited_version is None or cited_version == current:
        return CitationRuling("apply", cited_version)
    if cited_version in state["revoked"]:
        return CitationRuling(
            "refuse", current, citation_refusal="revoked", divergence=True,
        )
    if cited_version in state.get("refused", ()):
        return CitationRuling(
            "refuse", current, citation_refusal="refused", divergence=True,
        )
    if cited_version not in held:
        return CitationRuling(
            "refuse", None, citation_refusal="statement not held", divergence=True,
        )
    return CitationRuling(
        "apply", cited_version,
        superseded_citation=cited_version in state["superseded"],
        divergence=cited_version not in state["superseded"],
    )


# ----------------------------------------------------------------- folds

def _chain_of(tip: str, latest: dict[str, RevisionEntry]) -> list[str]:
    """``tip``'s ancestor chain (inclusive), following ``supersedes``
    pointers through the latest record per version. Stops at the
    pinned base (which has no record) and on cycles."""
    chain: list[str] = []
    cur, walked = tip, set()
    while cur is not None and cur not in walked and cur in latest:
        chain.append(cur)
        walked.add(cur)
        cur = latest[cur].supersedes
    return chain


def _ancestors(version: str | None, latest: dict[str, RevisionEntry]) -> list[str]:
    """``version`` and every transitive ``supersedes`` target, *including*
    versions with no record of their own* — the pinned base is
    superseded exactly like any other predecessor (a supersedes pointer
    is evidence enough; the target need not have a revision record)."""
    out: list[str] = []
    cur, walked = version, set()
    while cur is not None and cur not in walked:
        out.append(cur)
        walked.add(cur)
        pred = latest.get(cur)
        cur = pred.supersedes if pred is not None else None
    return out


def _common_predecessor(
    fork: list[str], latest: dict[str, RevisionEntry],
    base_version: str, alive: set,
) -> str:
    """§5.2: when the fold finds a fork, the seam stays on the common
    predecessor of the tips — not on the base. The common predecessor
    is the deepest version on every tip's ancestor chain; from there
    the fold walks back to the nearest adopted, non-revoked ancestor,
    and the pinned base is the floor (§4.1/§5.4)."""
    chains = [_chain_of(t, latest) for t in fork]
    common: str | None = None
    for v in chains[0]:
        if all(v in c for c in chains[1:]):
            common = v  # keep walking — the deepest shared ancestor wins
    cur = common
    while cur is not None and cur != base_version and cur not in alive:
        nxt = latest.get(cur)
        cur = nxt.supersedes if nxt is not None else None
    return cur if cur is not None else base_version


def contract_state(
    revisions: Iterable[RevisionEntry], base_version: str,
    *, contract_id: str | None = None, peer: str | None = None,
) -> dict:
    """One book's fold of its revision records (§4.1), scoped to one
    seam since v0.3.1 (CC1 F3): ``contract_id`` keeps an unrelated
    family's revisions out of this fold (their tips are not this
    seam's fork), and ``peer`` — when the records carry one — keeps
    one seam's adoption from moving another seam's current.

    ``current`` is a set operation, not a timestamp: among the versions
    this book has adopted and not revoked, the current one is the tip
    no other adopted, non-revoked version supersedes (transitively,
    stepping over revoked links — revocation is version-local, §5.4).
    No adopted tips: the base version (the seam's pinned start).

    **Forks are loud, and they include pending claims (§5.2, CC1 F2).**
    The live claims on a seam are its adopted versions *and* its
    pending proposals (a proposal from the other side, received and
    held un-adopted, is a live claim too). More than one claim tip is
    a **fork**: ``fork`` lists the tips, and ``current`` falls back to
    the **common predecessor** of the tips — the seam keeps working
    under the last version nobody disputes, not the pinned base.

    Revocation is not erasable by re-adoption (§5.4, CC1 F5): a
    version with *any* revocation record counts as revoked however its
    later records read — the scar stays visible in the fold.
    """
    records = [
        r for r in revisions
        if (contract_id is None or r.contract_id == contract_id)
        and (peer is None or r.peer in (None, "", peer))
    ]
    latest: dict[str, RevisionEntry] = {}
    for r in records:
        latest[r.version] = r
    adopted = {v for v, r in latest.items() if r.position == "adopted"}
    # the scar rule: any revocation record marks the version revoked
    revoked = {r.version for r in records if r.position == "revoked"}
    refused = {v for v, r in latest.items() if r.position == "refused"}
    alive = adopted - revoked
    pending = {v for v, r in latest.items() if r.position == "proposed"}

    superseded: set[str] = set()
    for v in alive:
        superseded.update(_ancestors(latest[v].supersedes, latest))
    # forks are detected among *live claims* — adopted versions and
    # pending proposals — because a pending proposal is a live claim
    # on a slot (§5.2), even though it never advances `current`.
    live = (alive | pending) - revoked
    live_superseded: set[str] = set()
    for v in live:
        live_superseded.update(_ancestors(latest[v].supersedes, latest))
    claim_tips = live - live_superseded
    fork = sorted(claim_tips) if len(claim_tips) > 1 else []
    if fork:
        current = _common_predecessor(fork, latest, base_version, alive)
    else:
        adopted_tips = alive - superseded
        if len(adopted_tips) == 1:
            current = next(iter(adopted_tips))
        else:
            # no live tips: the pinned base is what governs — H1 was
            # never revoked, so it returns without a re-proposal (§5.4)
            current = base_version
    return {
        "base": base_version,
        "proposals_seen": sorted(latest),
        "adopted": sorted(alive),
        "revoked": sorted(revoked),
        "refused": sorted(refused),
        "pending": sorted(pending - revoked),
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
    base_version: str, *, contract_id: str | None = None,
) -> dict:
    """§4.3's report: crossings and settlements per version, which sit
    under since-superseded versions, and the version in force now.

    A revision does not re-fold history — this report *describes* the
    regimes the ledger already records; correcting a settlement is a
    compensating entry, not a re-fold, and nothing here re-judges one.
    ``contract_id`` scopes the revision fold to one seam's family
    (v0.3.1, CC1 F3); omit it for the whole-book view.
    """
    ledger = list(ledger)
    state = contract_state(revisions, base_version, contract_id=contract_id)
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
