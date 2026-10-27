"""Regression tests for the CC1 audit findings F1–F6 (v0.3.1).

One test per finding, adapted from the referee's probe run (P1–P6 in
CC1-AUDIT.md; the probe scratch file itself never entered the repo, so
each probe is reconstructed here from the audit's description and made
asserting). The governing text for every fix is
DESIGN-V03-contract-versioning.md; section numbers cite it.

- F1 (§2.4/§3.1): a version this book has REFUSED must not govern a
  crossing — resolve_citation refuses it; a held-but-pending version is
  still honoured (deviation 5) but never silently: divergence flagged.
- F2 (§5.2): concurrent proposals must not self-resolve. Neither side
  adopts while the other's proposal is outstanding; the fork is visible
  in BOTH books' folds; the seam runs on the common predecessor; no
  exception escapes receive_ack — a refusal is booked there instead.
- F3 (§2.2/§5.5): revision state is per (contract_id, seam/peer). An
  unrelated family's adoption must not fork this seam's fold; an ack
  from anyone but the proposal's SEND peer completes nothing; a
  proposal from a non-party is refused — no standing.
- F4 (§3.1/§5.4): a revocation record preserves the chain, so revoking
  a middle link leaves the root superseded and its citations flagged.
- F5 (§5.4): revocation is not erasable by re-adoption — the scar rule.
  Adopting the revoked hash is refused; the fold keeps the revocation
  visible even under a rogue re-adoption record.
- F6 (§5.4): revocation is bilateral — send_revocation /
  receive_revocation book the crossing and the record on both books.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The demo module holds the canonical REVISION_STATEMENT_V2; load it by
# path so this suite's H2 can never drift from the scenario's H2.
_spec = importlib.util.spec_from_file_location(
    "two_process_demo", ROOT / "examples" / "two_process_demo.py"
)
_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo)

from quilt_engine.core import (
    RevisionEntry,
    StateStore,
    TransmissionEnvelope,
    agreed_crossings,
    adopt_revision,
    book_seam_receive,
    book_seam_send,
    contract_state,
    current_version,
    held_statements,
    make_adoption_ack,
    make_revision_proposal,
    make_revocation,
    propose_revision,
    receive_ack,
    receive_proposal,
    receive_revocation,
    resolve_citation,
    revoke_version,
    seam_context,
    send_proposal,
    send_revocation,
    statement_version,
)
from quilt_engine.seams import HANDSHAKE_STATEMENT_V1, SEAM_CONTRACT_V1

SENDER, RECEIVER = "vessel-a", "vessel-b"
NOT_A_PARTY = "vessel-c-not-a-party"
FAMILY = "beam-quilt/seam-handshake"
BASE = HANDSHAKE_STATEMENT_V1
H1 = SEAM_CONTRACT_V1.version
S2 = _demo.REVISION_STATEMENT_V2
H2 = _demo.H2
S2B = dict(HANDSHAKE_STATEMENT_V1, pool_rule="a competing successor")
H2B = statement_version(S2B)
S3 = dict(HANDSHAKE_STATEMENT_V1, pool_rule="a third link in the chain")
H3 = statement_version(S3)


def books():
    return StateStore(SENDER), StateStore(RECEIVER)


def probe(type_="version-probe", cite=H1):
    """One ordinary crossing stamped with a seam citation."""
    env = TransmissionEnvelope(payload={"merit": 0.5}, type=type_, cost_budget=1.0)
    env.contract_id = FAMILY
    env.contract_version = cite
    return env


def crossing_pair(sb, rb, env):
    """Book both sides of a crossing, the receiver resolving per §3.1."""
    book_seam_send(sb, env, RECEIVER)
    state, held = seam_context(rb, BASE, contract_id=FAMILY)
    ruling = resolve_citation(env.contract_version, state=state, held=held)
    entry = book_seam_receive(rb, env, SENDER, ruling=ruling)
    return ruling, entry


def fold(book):
    """The seam-scoped fold (CC1 F3): family-scoped, peer-lenient."""
    return contract_state(book.revisions, H1, contract_id=FAMILY)


def full_adoption_cycle(sb, rb):
    """The §2.3 sequence: proposal crossing, records, ack crossing."""
    proposal = make_revision_proposal(
        S2, contract_id=FAMILY, supersedes=H1, cited_version=H1, label="v2"
    )
    send_proposal(sb, proposal, RECEIVER, proposed_by=SENDER)
    _, record, ack = receive_proposal(rb, proposal, SENDER, base_statement=BASE)
    assert record.position == "adopted"
    book_seam_send(rb, ack, SENDER, detail={"in_reply_to": proposal.transmission_id})
    _, srec = receive_ack(sb, ack, RECEIVER, base_statement=BASE)
    assert srec is not None and srec.position == "adopted"
    return proposal, ack


# --------------------------------------------------------------- F1 (§2.4)

class TestAuditF1RefusedVersionsNeverGovern(unittest.TestCase):
    def test_audit_f1_refused_version_never_governs(self):
        # P2: B refuses H2a with reason "disputed" — and B retains the
        # statement. Before the fix, resolve_citation still APPLIED a
        # later citation of H2a (deviation 5's reach): the refusal had
        # no effect on the proposer's crossings, and the crossing was
        # agreed and unflagged. §2.4: the governing version is the
        # intersection of what both books hold; a refused version is
        # not in it.
        sb, rb = books()
        proposal = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1
        )
        send_proposal(sb, proposal, RECEIVER, proposed_by=SENDER)
        _, record, _ack = receive_proposal(
            rb, proposal, SENDER, base_statement=BASE,
            adopt=False, refuse_reason="disputed",
        )
        self.assertEqual(record.position, "refused")
        # The refused ack never crossed back: A is behind and keeps
        # citing H2a — the normal case (§5.5), not an edge case.
        env = probe("version-probe/refused-cite", cite=H2)
        ruling, entry = crossing_pair(sb, rb, env)
        self.assertEqual(ruling.action, "refuse")
        self.assertEqual(ruling.citation_refusal, "refused")
        self.assertTrue(ruling.divergence)          # surfaced, never silent
        self.assertEqual(entry.contract_version, H1)  # B's current
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertNotIn(env.transmission_id, agreed)
        self.assertIn(env.transmission_id, skewed)
        self.assertNotIn(env.transmission_id, unagreed)
        # The refusal did not move the seam: still v1 on both books.
        self.assertEqual(current_version(sb, BASE, contract_id=FAMILY), H1)
        self.assertEqual(current_version(rb, BASE, contract_id=FAMILY), H1)

    def test_audit_f1_pending_citation_honoured_but_flagged(self):
        # Deviation 5 kept, never silent: a held-but-pending version
        # (a proposal this book has taken no position on) is still
        # honoured under the cited version — but the ruling carries
        # divergence=True, so the honouring is flagged, not silent.
        sb, rb = books()
        # A's proposal sits pending on B's book — received and held,
        # no position taken (the §5.2 stand-off shape; before v0.3.1
        # receive_proposal would silently resolve it by adopting).
        propose_revision(
            rb, contract_id=FAMILY, statement=S2, supersedes=H1,
            proposed_by=SENDER,
        )
        state, held = seam_context(rb, BASE, contract_id=FAMILY)
        self.assertIn(H2, held)
        self.assertIn(H2, state["pending"])
        ruling = resolve_citation(H2, state=state, held=held)
        self.assertEqual(ruling.action, "apply")
        self.assertEqual(ruling.applied_version, H2)   # honoured under the cite
        self.assertFalse(ruling.superseded_citation)   # not superseded — pending
        self.assertTrue(ruling.divergence)             # but flagged, never silent


# --------------------------------------------------------------- F2 (§5.2)

class TestAuditF2ConcurrentProposals(unittest.TestCase):
    def test_audit_f2_concurrent_proposals_do_not_self_resolve(self):
        # P1: A proposes H2a and B proposes H2b, both superseding H1.
        # Before the fix, each side default-adopted the peer's
        # proposal, implicitly refused its own, the books ended on
        # different v2s with fork=[] in both folds, and both receive_ack
        # calls RAISED after their RECEIVEs were booked. §5.2: neither
        # wins — both proposals pending, seam on H1, fork loud.
        sb, rb = books()
        pa = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1, label="v2a"
        )
        pb = make_revision_proposal(
            S2B, contract_id=FAMILY, supersedes=H1, cited_version=H1, label="v2b"
        )
        send_proposal(sb, pa, RECEIVER, proposed_by=SENDER)
        send_proposal(rb, pb, SENDER, proposed_by=RECEIVER)
        # Crossed receipt: each side stands off against the other's
        # outstanding proposal — neither adopts, neither refuses.
        _, ra, acka = receive_proposal(rb, pa, SENDER, base_statement=BASE)
        self.assertEqual(ra.position, "proposed")
        self.assertEqual(acka.payload["position"], "proposed")
        _, rb_rec, ackb = receive_proposal(sb, pb, RECEIVER, base_statement=BASE)
        self.assertEqual(rb_rec.position, "proposed")
        self.assertEqual(ackb.payload["position"], "proposed")
        # The stand-off answers cross back and complete nothing.
        book_seam_send(rb, acka, SENDER)
        entry_a, rec_a = receive_ack(sb, acka, RECEIVER, base_statement=BASE)
        self.assertIsNone(rec_a)                      # nothing completed
        self.assertIsNotNone(entry_a)                 # but the crossing is booked
        book_seam_send(sb, ackb, RECEIVER)
        entry_b, rec_b = receive_ack(rb, ackb, SENDER, base_statement=BASE)
        self.assertIsNone(rec_b)
        # Outcome 1: neither side adopted anything.
        self.assertEqual(
            [r.position for r in sb.revisions + rb.revisions],
            ["proposed"] * 4,
        )
        # Outcome 2: the fork is visible in BOTH books' folds, and the
        # seam stays on H1 — the common predecessor of the tips.
        for book in (sb, rb):
            state = fold(book)
            self.assertEqual(state["fork"], sorted([H2, H2B]))
            self.assertEqual(state["current"], H1)
            self.assertEqual(state["pending"], sorted([H2, H2B]))
        # §5.2: the crossing set keeps working under the common
        # predecessor — agreed, unflagged, no dispute damage.
        env = probe("version-probe/during-standoff")
        ruling, entry = crossing_pair(sb, rb, env)
        self.assertEqual((ruling.action, ruling.applied_version), ("apply", H1))
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertIn(env.transmission_id, agreed)
        self.assertEqual(skewed, [])
        # The stand-off was said out loud on both sides.
        events = [e.get("event") for e in sb.events + rb.events]
        self.assertEqual(events.count("concurrent_proposal_standoff"), 2)

    def test_audit_f2_receive_ack_books_refusal_instead_of_raising(self):
        # P1's exception path: adoption refused inside receive_ack used
        # to raise AFTER the ack's RECEIVE was booked, leaving the book
        # half-booked with the error in flight. Now the refusal is a
        # record; nothing escapes.
        sb, _ = books()
        adopt_revision(                       # the book already moved to H2b
            sb, contract_id=FAMILY, version=H2B, statement=S2B,
            supersedes=H1, proposed_by=SENDER,
        )
        pending = propose_revision(           # its own H2a is still pending
            sb, contract_id=FAMILY, statement=S2, supersedes=H1,
            proposed_by=SENDER,
        )
        self.assertEqual(pending.position, "proposed")
        proposal = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1
        )
        ack = make_adoption_ack(proposal, position="adopted")
        entry, record = receive_ack(sb, ack, RECEIVER, base_statement=BASE)
        self.assertIsNotNone(entry)           # the RECEIVE is booked…
        self.assertIsNotNone(record)          # …and the refusal is booked too
        self.assertEqual(record.position, "refused")
        self.assertIn("fork refused", record.reason)
        self.assertEqual(                    # no position record was adopted
            [r.position for r in sb.revisions if r.version == H2],
            ["proposed", "refused"],
        )
        self.assertEqual(current_version(sb, BASE, contract_id=FAMILY), H2B)

    def test_audit_f2_fork_falls_back_to_common_predecessor(self):
        # The fold's fork fallback (§5.2): with H1 <- H2 <- {H3a, H3b},
        # the seam stays on H2 — the COMMON PREDECESSOR of the tips —
        # not on the base H1. The two adopted tips are booked directly,
        # simulating a ledger written before the fork refusals existed.
        sb, _ = books()
        adopt_revision(
            sb, contract_id=FAMILY, version=H2, statement=S2,
            supersedes=H1, proposed_by=SENDER,
        )
        s3a = dict(HANDSHAKE_STATEMENT_V1, pool_rule="fork branch a")
        s3b = dict(HANDSHAKE_STATEMENT_V1, pool_rule="fork branch b")
        h3a, h3b = statement_version(s3a), statement_version(s3b)
        for statement, version in ((s3a, h3a), (s3b, h3b)):
            sb.book_revision(RevisionEntry(
                contract_id=FAMILY, version=version, supersedes=H2,
                proposed_by=SENDER, position="adopted", statement=statement,
            ))
        state = fold(sb)
        self.assertEqual(state["fork"], sorted([h3a, h3b]))
        self.assertEqual(state["current"], H2)      # the common predecessor
        self.assertNotEqual(state["current"], H1)  # not the base
        self.assertIn(H1, state["superseded"])


# ---------------------------------------------------- F3 (§2.2/§5.5, scoping)

class TestAuditF3PerSeamScoping(unittest.TestCase):
    def test_audit_f3_unrelated_family_does_not_fork_the_seam(self):
        # P3: adopting a revision of an UNRELATED contract family used
        # to create a false fork in this seam's fold (tips from both
        # families) and drop the seam back to H1. A revision belongs to
        # (contract_id, seam); another family's tips are not this
        # seam's fork.
        sb, rb = books()
        full_adoption_cycle(sb, rb)           # this seam: H1 -> H2, bilateral
        other = "fleet/unrelated-contract"
        g1 = {"algorithm": "something else entirely"}
        g2 = dict(g1, tweak="a revision of the other family")
        adopt_revision(
            sb, contract_id=other, version=statement_version(g2),
            statement=g2, supersedes=statement_version(g1),
            proposed_by="some-other-party",
        )
        state = fold(sb)
        self.assertEqual(state["current"], H2)   # untouched by the other family
        self.assertEqual(state["fork"], [])      # no false fork
        self.assertEqual(state["adopted"], [H2])
        # The crossing path folds the same scoped way: a later proposal
        # on THIS seam is judged against this seam's chain alone.
        s3 = dict(HANDSHAKE_STATEMENT_V1, pool_rule="a third link")
        proposal = make_revision_proposal(
            s3, contract_id=FAMILY, supersedes=H2, cited_version=H2, label="v3"
        )
        send_proposal(sb, proposal, RECEIVER, proposed_by=SENDER)
        _, record, _ = receive_proposal(rb, proposal, SENDER, base_statement=BASE)
        self.assertEqual(record.position, "adopted")
        self.assertEqual(current_version(rb, BASE, contract_id=FAMILY),
                         statement_version(s3))

    def test_audit_f3_ack_from_non_party_completes_nothing(self):
        # P6: receive_ack used to accept an ack from ANY peer — "an
        # ack from anyone completes adoption: a quorum of one". The
        # ack's peer must match the proposal's SEND peer, which is this
        # book's own sovereign record of who it proposed to.
        sb, rb = books()
        proposal = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1
        )
        send_proposal(sb, proposal, RECEIVER, proposed_by=SENDER)
        _, _, ack = receive_proposal(rb, proposal, SENDER, base_statement=BASE)
        self.assertEqual(ack.payload["position"], "adopted")
        # The ack arrives attributed to a vessel that is not the peer
        # the proposal was SENT to.
        entry, record = receive_ack(
            sb, ack, NOT_A_PARTY, base_statement=BASE
        )
        self.assertIsNone(record)                  # completes nothing
        self.assertIsNotNone(entry)                # the crossing is still booked
        self.assertEqual(
            [r.position for r in sb.revisions if r.version == H2], ["proposed"]
        )
        self.assertTrue(
            any(e.get("event") == "ack_from_non_party" for e in sb.events)
        )
        self.assertEqual(current_version(sb, BASE, contract_id=FAMILY), H1)
        # The genuine ack from the SEND peer still completes adoption.
        _, record = receive_ack(sb, ack, RECEIVER, base_statement=BASE)
        self.assertEqual(record.position, "adopted")
        self.assertEqual(current_version(sb, BASE, contract_id=FAMILY), H2)

    def test_audit_f3_proposal_from_non_party_refused(self):
        # §2.2/§5.5 standing rule: a party to the seam may propose a
        # revision of that seam's contract; a third party proposing
        # over the seam is refused — no standing. The seam host passes
        # the seam's party set; the core refuses outside it.
        sb, rb = books()
        proposal = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1
        )
        entry, record, ack = receive_proposal(
            rb, proposal, NOT_A_PARTY, base_statement=BASE,
            parties={SENDER, RECEIVER},
        )
        self.assertEqual(record.position, "refused")
        self.assertIn("no standing", record.reason)
        self.assertIn(NOT_A_PARTY, record.reason)
        self.assertEqual(ack.payload["position"], "refused")
        self.assertIsNotNone(entry)            # the crossing itself is booked (P5)
        state = fold(rb)                       # and it moved nothing
        self.assertEqual(state["current"], H1)
        self.assertEqual(state["adopted"], [])
        self.assertEqual(state["pending"], [])
        # The same proposal from a party adopts normally.
        proposal2 = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1
        )
        _, record2, _ = receive_proposal(
            rb, proposal2, SENDER, base_statement=BASE,
            parties={SENDER, RECEIVER},
        )
        self.assertEqual(record2.position, "adopted")


# --------------------------------------------------------------- F4 (§3.1)

class TestAuditF4RevocationPreservesTheChain(unittest.TestCase):
    def test_audit_f4_revocation_preserves_the_chain(self):
        # P4: in H1 <- H2 <- H3, revoking H2 used to book a record with
        # supersedes=None, which became the latest H2 record and broke
        # the chain walk — H1 stopped being superseded and H1 citations
        # lost their §3.1 flag. The revocation record now preserves the
        # chain pointer.
        sb, _ = books()
        adopt_revision(
            sb, contract_id=FAMILY, version=H2, statement=S2,
            supersedes=H1, proposed_by=SENDER,
        )
        adopt_revision(
            sb, contract_id=FAMILY, version=H3, statement=S3,
            supersedes=H2, proposed_by=SENDER,
        )
        revocation = revoke_version(
            sb, contract_id=FAMILY, version=H2, reason="bad middle link"
        )
        self.assertEqual(revocation.supersedes, H1)   # the chain pointer kept
        state = fold(sb)
        self.assertEqual(state["current"], H3)       # version-local (§5.4)
        self.assertIn(H2, state["revoked"])
        self.assertIn(H1, state["superseded"])        # the chain is intact
        # An H1 citation while H3 is current: honoured under the cited
        # version AND flagged — §3.1's superseded-citation rule.
        ruling = resolve_citation(
            H1, state=state,
            held=set(held_statements(sb.revisions, BASE)),
        )
        self.assertEqual(ruling.action, "apply")
        self.assertEqual(ruling.applied_version, H1)
        self.assertTrue(ruling.superseded_citation)   # the flag F4 lost
        # H2 itself is refused as revoked.
        ruling2 = resolve_citation(
            H2, state=state, held=set(held_statements(sb.revisions, BASE))
        )
        self.assertEqual(
            (ruling2.action, ruling2.citation_refusal), ("refuse", "revoked")
        )


# --------------------------------------------------------------- F5 (§5.4)

class TestAuditF5RevocationScar(unittest.TestCase):
    def test_audit_f5_revocation_not_erasable_by_readoption(self):
        # P5: adopting the same hash after a revocation used to become
        # the latest record per version, and the fold (and
        # version_report) silently lost the revocation: current=H2a,
        # revoked=[]. §5.4: a re-proposal after a revocation is a NEW
        # statement (a new hash), not a restoration. Adopting the
        # revoked hash is refused, and the fold keeps the revocation
        # visible — the scar rule.
        sb, rb = books()
        full_adoption_cycle(sb, rb)           # H2 in force bilaterally
        revoke_version(sb, contract_id=FAMILY, version=H2, reason="bad revision")
        self.assertEqual(current_version(sb, BASE, contract_id=FAMILY), H1)
        # Adopting the revoked hash itself: refused, loudly.
        with self.assertRaises(ValueError) as ctx:
            adopt_revision(
                sb, contract_id=FAMILY, version=H2, statement=S2,
                supersedes=H1, proposed_by=SENDER,
            )
        self.assertIn("revoked", str(ctx.exception))
        self.assertIn("§5.4", str(ctx.exception))
        # Even a rogue re-adoption record booked directly (a ledger
        # written by older code or another writer) cannot erase the
        # scar from the fold.
        sb.book_revision(RevisionEntry(
            contract_id=FAMILY, version=H2, supersedes=H1,
            proposed_by=SENDER, position="adopted", statement=S2,
        ))
        state = fold(sb)
        self.assertIn(H2, state["revoked"])       # the scar stays visible
        self.assertNotIn(H2, state["adopted"])    # and it does not govern
        self.assertEqual(state["current"], H1)
        ruling = resolve_citation(
            H2, state=state, held=set(held_statements(sb.revisions, BASE))
        )
        self.assertEqual(
            (ruling.action, ruling.citation_refusal), ("refuse", "revoked")
        )
        # The design's remedy: a NEW statement with a new hash adopts
        # cleanly over the same predecessor.
        s2p = dict(S2, pool_rule="the fixed successor, a new statement")
        h2p = statement_version(s2p)
        record = adopt_revision(
            sb, contract_id=FAMILY, version=h2p, statement=s2p,
            supersedes=H1, proposed_by=SENDER,
        )
        self.assertEqual(record.position, "adopted")
        self.assertEqual(current_version(sb, BASE, contract_id=FAMILY), h2p)


# --------------------------------------------------------------- F6 (§5.4)

class TestAuditF6BilateralRevocation(unittest.TestCase):
    def test_audit_f6_revocation_is_bilateral(self):
        # §5.4: "A revocation is a bilateral act… It is not a
        # unilateral local edit." make_revocation existed but nothing
        # ever received it. The crossing path now books the revocation
        # crossing and the revocation record on BOTH books.
        sb, rb = books()
        full_adoption_cycle(sb, rb)           # H2 in force bilaterally
        revocation = make_revocation(
            contract_id=FAMILY, version=H2, cited_version=H2, reason="bad revision",
        )
        send_revocation(sb, revocation, RECEIVER, proposed_by=SENDER)
        self.assertEqual(
            [r.position for r in sb.revisions if r.version == H2],
            ["proposed", "adopted", "revoked"],
        )
        entry, record = receive_revocation(
            rb, revocation, SENDER, base_statement=BASE
        )
        self.assertEqual(record.position, "revoked")     # B's own record
        self.assertEqual(record.supersedes, H1)          # chain preserved (F4)
        self.assertIsNotNone(entry)                      # the crossing booked
        # Both books fall back to H1 — each on its own fold, together.
        self.assertEqual(current_version(sb, BASE, contract_id=FAMILY), H1)
        self.assertEqual(current_version(rb, BASE, contract_id=FAMILY), H1)
        # The revocation crossing itself agreed, under the version it
        # cited — the one it was withdrawing.
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertIn(revocation.transmission_id, agreed)
        self.assertEqual(skewed, [])
        # And citations of H2 are refused on both sides now.
        for book in (sb, rb):
            state = fold(book)
            ruling = resolve_citation(
                H2, state=state, held=set(held_statements(book.revisions, BASE))
            )
            self.assertEqual(
                (ruling.action, ruling.citation_refusal), ("refuse", "revoked")
            )
        # The seam keeps working under H1 after the bilateral revocation.
        env = probe("version-probe/after-revocation")
        ruling, entry = crossing_pair(sb, rb, env)
        self.assertEqual((ruling.action, ruling.applied_version), ("apply", H1))
        agreed, _, _ = agreed_crossings(sb.ledger, rb.ledger)
        self.assertIn(env.transmission_id, agreed)


if __name__ == "__main__":
    unittest.main()
