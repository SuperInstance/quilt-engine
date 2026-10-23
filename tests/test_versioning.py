"""Acceptance: contract versioning (v0.3, DESIGN-V03 — ruling-neutral core).

- Version derivation (§1): the version is the hash of the canonical
  statement — same bytes → same version, any change → a new one, and a
  label is outside the hashed bytes.
- Revision as a crossing (§2): adoption is three bookings on each side
  and governs only when *both* books hold it; adoption is skewed across
  sovereign timing and that is safe.
- Superseded citations (§3): honoured under the version the crossing
  cites, logged verbatim, never silently upgraded; revoked and unknown
  citations are refused.
- Version-aware agreement (§6.6): mismatched-version bookings surface
  as *skewed*, a category of their own — not agreed, not plain unagreed.
- Per-book folds (§4): a revision does not re-fold history; a
  settlement made under v1 stands when v2 lands.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The demo module holds the canonical REVISION_STATEMENT_V2; load it by
# path so the test's H2 can never drift from the scenario's H2.
_spec = importlib.util.spec_from_file_location(
    "two_process_demo", ROOT / "examples" / "two_process_demo.py"
)
_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo)

from quilt_engine.core import (
    REVISION_TYPE,
    AlgorithmContract,
    ContractResult,
    LedgerEntry,
    RevisionEntry,
    StateStore,
    TransmissionEnvelope,
    agreed_crossings,
    book_seam_receive,
    book_seam_send,
    book_settle,
    contract_state,
    current_version,
    held_statements,
    make_payload_offer,
    make_revision_proposal,
    make_statement_request,
    receive_ack,
    receive_proposal,
    resolve_citation,
    revoke_version,
    send_proposal,
    settled_payloads,
    statement_version,
    version_report,
)
from quilt_engine.core.envelope import Ending
from quilt_engine.seams import (
    HANDSHAKE_STATEMENT_V1,
    SEAM_CONTRACT_V1,
)

SENDER, RECEIVER = "vessel-a", "vessel-b"
FAMILY = "beam-quilt/seam-handshake"
BASE = HANDSHAKE_STATEMENT_V1
H1 = SEAM_CONTRACT_V1.version
S2 = _demo.REVISION_STATEMENT_V2
H2 = _demo.H2
S2B = dict(HANDSHAKE_STATEMENT_V1, pool_rule="a competing successor")
H2B = statement_version(S2B)
S3 = dict(HANDSHAKE_STATEMENT_V1, pool_rule="a third link in the chain")
H3 = statement_version(S3)
UNHELD = statement_version(dict(HANDSHAKE_STATEMENT_V1, pool_rule="never crossed"))


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
    state, held = seam_context(rb)
    ruling = resolve_citation(env.contract_version, state=state, held=held)
    entry = book_seam_receive(rb, env, SENDER, ruling=ruling)
    return ruling, entry


def seam_context(book):
    from quilt_engine.core import seam_context as _sc

    return _sc(book, BASE)


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


# ------------------------------------------------------------- §1 identity

class TestVersionDerivation(unittest.TestCase):
    def test_same_statement_same_version_any_change_new(self):
        a = statement_version(S2)
        self.assertEqual(a, statement_version(dict(S2)))  # fresh dict, same data
        changed = dict(S2, pool_rule=S2["pool_rule"] + ".")
        self.assertNotEqual(a, statement_version(changed))
        # Key insertion order is not part of the canonical form.
        reordered = {k: S2[k] for k in reversed(list(S2))}
        self.assertEqual(statement_version(S2), statement_version(reordered))

    def test_pinned_statement_hash_is_the_seam_version(self):
        # The demo's v1 is a real content hash of the pinned document,
        # not the "1.2" label of the v0.1 path.
        self.assertEqual(SEAM_CONTRACT_V1.version, statement_version(BASE))
        self.assertNotEqual(SEAM_CONTRACT_V1.version, H2)

    def test_label_is_outside_the_hashed_bytes(self):
        # §1.3 / §8.3: the label is display only — changing it cannot
        # change the version, because identity is hash-only.
        fn = lambda x: ContractResult(output=x, why="applied", cost=0.0)
        c1 = AlgorithmContract(name="c", statement=S2, fn=fn, label="v2")
        c2 = AlgorithmContract(name="c", statement=S2, fn=fn, label="v3")
        self.assertEqual(c1.version, c2.version)
        self.assertEqual(c1.identity, c2.identity)

    def test_prose_statements_normalised_before_hashing(self):
        # §1.7: NFC, LF endings — spelling may differ, bytes may not.
        self.assertEqual(
            statement_version("café\r\nrule one"),
            statement_version("café\nrule one"),
        )

    def test_contract_constructor_has_no_typed_version(self):
        fn = lambda x: ContractResult(output=x, why="applied", cost=0.0)
        c = AlgorithmContract(name="echo", statement={"algorithm": "echo"}, fn=fn)
        self.assertEqual(c.version, statement_version({"algorithm": "echo"}))


# -------------------------------------------------------- §2 revision cycle

class TestRevisionAsCrossing(unittest.TestCase):
    def test_adoption_requires_both_books(self):
        # §2.3's table: three bookings on each side. Before the ack
        # crosses back, only one book holds the adoption — the folds
        # differ (§2.4), and the seam is still bilaterally on v1.
        sb, rb = books()
        proposal = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1, label="v2"
        )
        send_proposal(sb, proposal, RECEIVER, proposed_by=SENDER)
        _, record, ack = receive_proposal(rb, proposal, SENDER, base_statement=BASE)
        self.assertEqual(record.position, "adopted")
        self.assertEqual(current_version(rb, BASE), H2)   # B's local current
        self.assertEqual(current_version(sb, BASE), H1)   # the seam, still v1
        book_seam_send(rb, ack, SENDER)
        receive_ack(sb, ack, RECEIVER, base_statement=BASE)
        self.assertEqual(current_version(sb, BASE), H2)   # now bilateral
        # The proposal crossing itself agreed under the version it cited.
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertIn(proposal.transmission_id, agreed)
        # Records: proposer booked proposed -> adopted; peer booked adopted.
        self.assertEqual(
            [r.position for r in sb.revisions if r.version == H2],
            ["proposed", "adopted"],
        )
        self.assertEqual(
            [r.position for r in rb.revisions if r.version == H2], ["adopted"]
        )

    def test_proposal_payload_is_self_sufficient(self):
        # §2.1: the statement travels; one round trip delivers the revision.
        env = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1, label="v2"
        )
        self.assertEqual(env.type, REVISION_TYPE)
        back = TransmissionEnvelope.from_json(env.to_json())
        self.assertEqual(back.payload["statement"], S2)
        self.assertEqual(back.payload["version"], H2)
        self.assertEqual(back.payload["supersedes"], H1)
        # The proposal's own citation is the version in force (H1), not
        # the version it proposes (H2) — the two must not be conflated.
        self.assertEqual(back.contract_version, H1)

    def test_refused_peer_answer_carries_reason(self):
        # §5.1: the peer must answer; a refusal with a reason is a fact.
        sb, rb = books()
        proposal = make_revision_proposal(
            S2, contract_id=FAMILY, supersedes=H1, cited_version=H1
        )
        send_proposal(sb, proposal, RECEIVER, proposed_by=SENDER)
        _, record, ack = receive_proposal(
            rb, proposal, SENDER, base_statement=BASE,
            adopt=False, refuse_reason="receiver policy: not now",
        )
        self.assertEqual(record.position, "refused")
        self.assertEqual(record.reason, "receiver policy: not now")
        self.assertEqual(ack.payload["position"], "refused")
        book_seam_send(rb, ack, SENDER)
        _, srec = receive_ack(sb, ack, RECEIVER, base_statement=BASE)
        # The proposer's pending record becomes a refusal, verbatim.
        self.assertEqual(srec.position, "refused")
        self.assertEqual(srec.reason, "receiver policy: not now")
        # The seam never moved off v1.
        self.assertEqual(current_version(sb, BASE), H1)
        self.assertEqual(current_version(rb, BASE), H1)

    def test_fork_refused_loudly(self):
        # §5.2: only one successor of a predecessor can be in force.
        from quilt_engine.core import adopt_revision

        sb, rb = books()
        full_adoption_cycle(sb, rb)
        with self.assertRaises(ValueError) as ctx:
            adopt_revision(
                sb, contract_id=FAMILY, version=H2B, statement=S2B,
                supersedes=H1, proposed_by=SENDER,
            )
        self.assertIn("fork refused", str(ctx.exception))
        self.assertEqual(current_version(sb, BASE), H2)

    def test_adoption_refuses_competing_pending_proposals(self):
        # §5.1: adopting one successor implicitly refuses the other
        # pending claim on the same slot, with the recorded reason.
        from quilt_engine.core import adopt_revision, propose_revision

        sb, _ = books()
        propose_revision(
            sb, contract_id=FAMILY, statement=S2B, supersedes=H1,
            proposed_by=SENDER,
        )
        adopt_revision(
            sb, contract_id=FAMILY, version=H2, statement=S2,
            supersedes=H1, proposed_by=SENDER,
        )
        positions = [(r.version, r.position, r.reason) for r in sb.revisions]
        self.assertIn(
            (H2B, "refused", f"superseded by the adoption of {H2}"), positions
        )

    def test_revision_record_round_trips_through_the_book(self):
        # §6.3: a third JSONL kind, readable back.
        with tempfile.TemporaryDirectory() as td:
            book = StateStore("a", Path(td) / "book.jsonl")
            full_adoption_cycle(book, StateStore("b"))
            loaded = StateStore.load("a", Path(td) / "book.jsonl")
            self.assertEqual(
                [r.position for r in loaded.revisions], ["proposed", "adopted"]
            )
            self.assertEqual(loaded.revisions[-1].statement, S2)
            self.assertEqual(current_version(loaded, BASE), H2)


# ---------------------------------------------------- §3 superseded citations

class TestCrossingInFlight(unittest.TestCase):
    def test_in_flight_honoured_under_cited_version(self):
        # §2.5: sent under v1, received after the receiver adopted v2 —
        # honoured under v1 on both sides, agreed, shared, flagged.
        sb, rb = books()
        full_adoption_cycle(sb, rb)   # both books on v2 now
        # But an envelope composed *before* the sender could know still
        # cites v1 (in the demo it crosses the boundary mid-adoption):
        env = probe("version-probe/in-flight", cite=H1)
        ruling, entry = crossing_pair(sb, rb, env)
        self.assertEqual(ruling.action, "apply")
        self.assertEqual(entry.contract_version, H1)   # honoured, cited
        self.assertTrue(entry.detail.get("superseded_citation"))
        send = next(e for e in sb.ledger if e.transmission_id == env.transmission_id)
        self.assertEqual(send.contract_version, H1)
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertIn(env.transmission_id, agreed)
        self.assertEqual(skewed, [])
        self.assertEqual(unagreed, [])

    def test_superseded_citation_logged_not_upgraded(self):
        # §3.1: the receiver applies the *cited* version and records
        # what it knows; it never re-judges under its current.
        sb, rb = books()
        full_adoption_cycle(sb, rb)
        env = probe("version-probe/stale-v1", cite=H1)
        ruling, entry = crossing_pair(sb, rb, env)
        self.assertEqual(entry.contract_version, H1)
        self.assertNotEqual(entry.contract_version, current_version(rb, BASE))
        self.assertTrue(ruling.superseded_citation)
        # The skew is in the detail, verbatim — never averaged.
        self.assertEqual(entry.detail.get("superseded_citation"), True)

    def test_unknown_citation_refused_and_statement_requested(self):
        # §2.5's asymmetry: the receiver cannot apply what it does not
        # hold. It fails closed, books no version, asks for the bytes.
        sb, rb = books()
        full_adoption_cycle(sb, rb)
        env = probe("version-probe/unknown", cite=UNHELD)
        ruling, entry = crossing_pair(sb, rb, env)
        self.assertEqual(ruling.action, "refuse")
        self.assertIsNone(entry.contract_version)
        self.assertEqual(entry.detail.get("citation_refusal"), "statement not held")
        self.assertTrue(ruling.divergence)
        request = make_statement_request(
            env, contract_id=FAMILY, missing_version=UNHELD
        )
        self.assertTrue(request.type.endswith("/narrow"))  # reuse, not invention
        self.assertEqual(request.payload["missing_statement_for"]["version"], UNHELD)
        # The crossing is skewed — not agreed, not plain unagreed. (The
        # adoption cycle's own crossings remain agreed, as they should.)
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertNotIn(env.transmission_id, agreed)
        self.assertEqual(skewed, [env.transmission_id])
        self.assertEqual(unagreed, [])

    def test_revoked_citation_refused_under_receiver_current(self):
        # §3.1: superseded is not revoked. A revoked citation is refused
        # and the receiver books *its* current — the versions diverge.
        sb, rb = books()
        full_adoption_cycle(sb, rb)
        revoke_version(rb, contract_id=FAMILY, version=H2, reason="bad revision")
        self.assertEqual(current_version(rb, BASE), H1)  # nearest live ancestor
        env = probe("version-probe/revoked", cite=H2)
        ruling, entry = crossing_pair(sb, rb, env)
        self.assertEqual(ruling.action, "refuse")
        self.assertEqual(ruling.citation_refusal, "revoked")
        self.assertEqual(entry.contract_version, H1)  # receiver's current
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertEqual(skewed, [env.transmission_id])

    def test_revocation_is_version_local(self):
        # §5.4: revoking H2 does not void H3, which stands on its own.
        from quilt_engine.core import adopt_revision

        sb, rb = books()
        full_adoption_cycle(sb, rb)
        adopt_revision(
            sb, contract_id=FAMILY, version=H3, statement=S3,
            supersedes=H2, proposed_by=SENDER,
        )
        revoke_version(sb, contract_id=FAMILY, version=H2)
        self.assertEqual(current_version(sb, BASE), H3)


# ------------------------------------------------- §6.6 version-aware agreement

class TestSkewedCategory(unittest.TestCase):
    def test_mismatched_versions_are_skewed_not_unagreed(self):
        # §6.6: agreement includes the version. Same id, same payload
        # hash, different versions — a category of its own.
        sb, rb = books()
        env = probe()
        book_seam_send(sb, env, RECEIVER)
        rb.book(
            "RECEIVE",
            transmission_id=env.transmission_id, peer=SENDER,
            payload_type=env.type, payload_hash=env.hash,
            ending=env.ending.value, cost=env.cost_budget, contract="",
            contract_id=FAMILY, contract_version=H2,   # what it applied
        )
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertEqual(agreed, [])
        self.assertEqual(skewed, [env.transmission_id])
        self.assertEqual(unagreed, [])

    def test_matching_versions_still_agree(self):
        sb, rb = books()
        env = probe()
        crossing_pair(sb, rb, env)
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertEqual(agreed, [env.transmission_id])
        self.assertEqual(skewed, [])
        self.assertEqual(unagreed, [])

    def test_v01_unversioned_crossings_still_agree(self):
        # Back-compat: bookings with no version on either side agree.
        sb, rb = books()
        env = TransmissionEnvelope(payload={"merit": 0.5}, type="telemetry")
        for book, side, peer in ((sb, "SEND", RECEIVER), (rb, "RECEIVE", SENDER)):
            book.book(
                side, transmission_id=env.transmission_id, peer=peer,
                payload_type=env.type, payload_hash=env.hash,
                ending="PROJECT", cost=0.0, contract="legacy@1",
            )
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertEqual(agreed, [env.transmission_id])


# ------------------------------------------------------------ §4 folds stand

class TestSettlementsStand(unittest.TestCase):
    def test_v1_settlement_stands_after_v2(self):
        # §4.2/§4.3: a revision does not re-fold history. The payload
        # settled under v1 keeps its record byte-intact when v2 lands.
        sb, rb = books()
        offer = make_payload_offer(
            {"route": "settled-under-v1"}, contract="route-plan@1",
            ending=Ending.COPY, copy_authorized_by="owner-order-3",
        )
        offer.contract_id, offer.contract_version = FAMILY, H1
        from quilt_engine.core import book_offer_receive, book_offer_send
        from quilt_engine.core.payload import read_offer

        book_offer_send(sb, offer, RECEIVER, contract_id=FAMILY, contract_version=H1)
        reading = read_offer(offer.to_json())
        book_offer_receive(
            rb, reading, SENDER, contract_id=FAMILY, contract_version=H1
        )
        # The receipt crossing back is the sender's evidence (v0.2).
        from quilt_engine.core import (
            book_receipt_receive as _brr,
            book_receipt_send as _brs,
            make_receipt as _mr,
        )

        receipt = _mr(reading)
        receipt.contract_id, receipt.contract_version = FAMILY, H1
        _brs(rb, receipt, reading, SENDER, contract_id=FAMILY, contract_version=H1)
        _brr(sb, receipt, RECEIVER, contract_id=FAMILY, contract_version=H1)
        book_settle(sb, offer, RECEIVER, contract_id=FAMILY, contract_version=H1)
        book_settle(rb, offer, SENDER, contract_id=FAMILY, contract_version=H1)
        before_s, before_r = settled_payloads(sb.ledger), settled_payloads(rb.ledger)

        full_adoption_cycle(sb, rb)   # v2 lands

        after_s, after_r = settled_payloads(sb.ledger), settled_payloads(rb.ledger)
        self.assertEqual(after_s, before_s)   # no re-fold, either book
        self.assertEqual(after_r, before_r)
        tid = offer.transmission_id
        self.assertEqual(after_s[tid]["contract_version"], H1)
        self.assertEqual(after_r[tid]["contract_version"], H1)
        # §4.3's report: the version each was settled under, the version
        # in force now, and the settlement counted under a superseded one.
        rep = version_report(rb.ledger, rb.revisions, H1)
        self.assertEqual(rep["current"], H2)
        self.assertIn(tid, rep["settlements_under_superseded"])
        self.assertTrue(rep["bookings_by_version"][H1]["superseded"])
        # And the settlement's crossing is still agreed — nothing about
        # the past changed when the future's contract did.
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertIn(tid, agreed)

    def test_current_is_a_set_operation_not_a_clock(self):
        # §4.1: the fold walks the supersedes chain; H1 <- H2 <- H3,
        # everything transitively superseded, H3 current.
        from quilt_engine.core import adopt_revision

        sb, _ = books()
        full_adoption_cycle(sb, _)
        adopt_revision(
            sb, contract_id=FAMILY, version=H3, statement=S3,
            supersedes=H2, proposed_by=SENDER,
        )
        state = contract_state(sb.revisions, H1)
        self.assertEqual(state["current"], H3)
        self.assertEqual(set(state["adopted"]), {H2, H3})
        self.assertEqual(set(state["superseded"]), {H1, H2})


# --------------------------------------------------------- §6.1/§6.7 shapes

class TestEnvelopeAndPairCarryTheCitation(unittest.TestCase):
    def test_envelope_round_trip_and_refresh_carry_the_pair(self):
        env = probe()
        back = TransmissionEnvelope.from_json(env.to_json())
        self.assertEqual(back.contract_id, FAMILY)
        self.assertEqual(back.contract_version, H1)
        refreshed = back.refreshed()
        self.assertEqual(refreshed.contract_id, FAMILY)   # same crossing…
        self.assertEqual(refreshed.contract_version, H1)  # …same version
        # A v0.1 envelope (no keys) still parses, citation empty.
        d = json.loads(env.to_json())
        d.pop("contract_id"), d.pop("contract_version")
        legacy = TransmissionEnvelope.from_dict(d)
        self.assertIsNone(legacy.contract_version)

    def test_jev_pair_with_contract_object_stamps_both_sides(self):
        # §6.7: the sender stamps the envelope; the receiver stamps the
        # version it applied — agreement by record, not by constant.
        from quilt_engine.seams import Deadband, JevPair, ReceiverJev, SenderJev

        sb, rb, seam_book = StateStore(SENDER), StateStore(RECEIVER), StateStore("seam")
        pair = JevPair(
            sender=SenderJev(), receiver=ReceiverJev(Deadband(0.15, 0.8)),
            sender_book=sb, receiver_book=rb,
            sender_name=SENDER, receiver_name=RECEIVER,
            contract=SEAM_CONTRACT_V1, seam_book=seam_book,
        )
        env = TransmissionEnvelope(payload={"merit": 0.5}, type="telemetry")
        pair.cross(env, now=100.0)
        send, recv = sb.ledger[0], rb.ledger[0]
        self.assertEqual(send.contract_id, FAMILY)
        self.assertEqual(send.contract_version, H1)
        self.assertEqual(recv.contract_version, H1)   # what it applied
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertEqual(agreed, [env.transmission_id])
        crossing = next(
            ev for ev in seam_book.events if ev.get("event") == "crossing"
        )
        self.assertEqual(crossing["cited_version"], H1)
        self.assertEqual(crossing["applied_version"], H1)
        self.assertFalse(crossing["superseded_citation"])

    def test_manifest_declares_the_version_it_implements(self):
        # §6.5: the example manifest's citation is the derived hash.
        from quilt_engine.patches import PatchManifest

        path = ROOT / "quilt_engine" / "patches" / "edge_fleet_node.json"
        m = PatchManifest.load(path)
        self.assertEqual(m.contract_id, FAMILY)
        self.assertEqual(m.contract_version, H1)


# ------------------------------------------------------------ the demo run

class TestVersioningScenario(unittest.TestCase):
    def test_scenario_exhibits_the_full_versioning_story(self):
        demo = ROOT / "examples" / "two_process_demo.py"
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, DEMO_TIME_SCALE="0.6")
            proc = subprocess.run(
                [sys.executable, str(demo), "--dir", td, "--scenario", "versioning"],
                capture_output=True, text=True, timeout=180, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("VERSIONING OK", proc.stdout)

            work = Path(td)
            sbook = StateStore.load(SENDER, work / "sender_book.jsonl")
            rbook = StateStore.load(RECEIVER, work / "receiver_book.jsonl")
            # Both books hold the revision, adopted; v1 superseded.
            self.assertEqual(current_version(sbook, BASE), H2)
            self.assertEqual(current_version(rbook, BASE), H2)
            # The in-flight crossing agreed under v1, flagged on arrival.
            inflight = [
                e for e in rbook.ledger
                if e.payload_type == "version-probe/in-flight"
            ]
            self.assertEqual(len(inflight), 1)
            self.assertEqual(inflight[0].contract_version, H1)
            self.assertTrue(inflight[0].detail.get("superseded_citation"))
            # The refused citation is the one skewed crossing.
            agreed, skewed, unagreed = agreed_crossings(sbook.ledger, rbook.ledger)
            refused = {
                e.transmission_id
                for e in rbook.ledger
                if e.detail.get("citation_refusal")
            }
            self.assertEqual(set(skewed), refused)
            self.assertEqual(len(refused), 1)
            # The settlement made under v1 still stands, under v1.
            settled = settled_payloads(rbook.ledger)
            v1_settled = [
                tid for tid, rec in settled.items()
                if rec.get("contract_version") == H1
            ]
            self.assertTrue(v1_settled)
            self.assertIn(v1_settled[0], settled_payloads(sbook.ledger))


if __name__ == "__main__":
    unittest.main()
