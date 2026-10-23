"""Acceptance: both-sides booking, agreement, C2 seam log, refusal return.

JevPair runs the handshake in-process, but the two patches still
communicate only through envelopes, each holding only its own book.
"""

import unittest

from quilt_engine.core import StateStore, TransmissionEnvelope, agreed_crossings
from quilt_engine.seams import (
    Act,
    Deadband,
    Hold,
    JevPair,
    ReceiverJev,
    Refuse,
    SenderJev,
)


def make_pair(**receiver_kw):
    sender_book = StateStore("vessel-a")
    receiver_book = StateStore("vessel-b")
    seam_book = StateStore("seam")
    receiver_kw.setdefault("deadband", Deadband(band=0.15, yaw_time=0.8))
    receiver_kw.setdefault("expected_course", 0.50)
    receiver_kw.setdefault("pool_ttl", 60.0)
    pair = JevPair(
        sender=SenderJev(field_expectations={"telemetry": ["a", "b"]}),
        receiver=ReceiverJev(**receiver_kw),
        sender_book=sender_book,
        receiver_book=receiver_book,
        sender_name="vessel-a",
        receiver_name="vessel-b",
        seam_book=seam_book,
    )
    return pair, sender_book, receiver_book, seam_book


def env(merit, type_="course-proposal"):
    return TransmissionEnvelope(
        payload={"merit": merit, "a": 1, "b": 2}, type=type_, cost_budget=2.0
    )


class TestBothSidesBooking(unittest.TestCase):
    def test_crossing_booked_send_and_receive_with_contract(self):
        pair, sb, rb, _ = make_pair()
        e = env(0.55)
        result = pair.cross(e, now=100.0)
        self.assertIsInstance(result.decision, Hold)
        self.assertEqual(len(sb.ledger), 1)
        self.assertEqual(len(rb.ledger), 1)
        send, recv = sb.ledger[0], rb.ledger[0]
        self.assertEqual(send.side, "SEND")
        self.assertEqual(recv.side, "RECEIVE")
        self.assertEqual(send.transmission_id, recv.transmission_id)
        self.assertEqual(send.payload_hash, recv.payload_hash)
        # Gap 3: the governing contract's identity is in both entries.
        self.assertEqual(send.contract, "beam-quilt/seam-handshake@1.2")
        self.assertEqual(recv.contract, send.contract)
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertEqual(agreed, [e.transmission_id])
        self.assertEqual(skewed, [])
        self.assertEqual(unagreed, [])

    def test_receiver_active_log_has_dated_reading(self):
        pair, _, rb, _ = make_pair()
        pair.cross(env(0.55), now=100.0)
        reading = rb.latest_reading("vessel-a")
        self.assertIsNotNone(reading)
        self.assertEqual(reading.taken_at, 100.0)
        # Staleness is explicit (P4): the reading ages, measurably.
        self.assertTrue(reading.is_stale(now=1000.0, max_age=60.0))
        self.assertFalse(reading.is_stale(now=110.0, max_age=60.0))

    def test_unilateral_send_is_not_shared(self):
        # A SEND with no matching RECEIVE is *not* an agreed crossing:
        # shared state exists only in the agreement (gap 2).
        sb = StateStore("a")
        rb = StateStore("b")
        e = env(0.5)
        sb.book(
            "SEND",
            transmission_id=e.transmission_id,
            peer="b",
            payload_type=e.type,
            payload_hash=e.hash,
            ending="PROJECT",
            cost=0.0,
            contract="beam-quilt/seam-handshake@1.2",
        )
        agreed, skewed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertEqual(agreed, [])
        self.assertEqual(skewed, [])
        self.assertEqual(unagreed, [e.transmission_id])


class TestSeamLogC2(unittest.TestCase):
    def test_crossing_logged_with_c2_fields(self):
        pair, _, _, seam_book = make_pair()
        e = env(0.55)
        pair.cross(e, now=100.0)
        crossings = [ev for ev in seam_book.events if ev.get("event") == "crossing"]
        self.assertEqual(len(crossings), 1)
        c = crossings[0]
        for fieldname in ("payload_type", "profile", "pick", "ending", "cost"):
            self.assertIn(fieldname, c)
        self.assertEqual(c["payload_type"], "course-proposal")
        self.assertEqual(c["pick"], e.transmission_id)
        self.assertEqual(c["ending"], "PROJECT")
        self.assertEqual(c["cost"], 2.0)

    def test_disagreement_in_seam_log_verbatim(self):
        pair, _, _, seam_book = make_pair(
            required_fields={"telemetry": ["a", "b", "c"]}
        )
        pair.cross(env(0.55, type_="telemetry"), now=100.0)
        recs = [ev for ev in seam_book.events if ev.get("event") == "disagreement"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["sender_profile"]["completeness"], 1.0)
        self.assertAlmostEqual(
            recs[0]["receiver_measurement"]["completeness"], 2 / 3, places=3
        )


class TestRefusalReturn(unittest.TestCase):
    def test_refusal_profile_travels_back_and_is_booked(self):
        pair, sb, rb, seam_book = make_pair()
        e = env(-0.20)
        first = pair.cross(e, now=0.0)
        self.assertEqual(first.decision.kind, "hold_watch")
        # Same candidate re-asserted past the yaw-time -> Act(refuse).
        second = pair.cross(e.refreshed(), now=1.0)
        self.assertIsInstance(second.decision, Act)
        self.assertIsInstance(second.decision.response, Refuse)
        self.assertIsNotNone(second.response_envelope)
        self.assertEqual(second.response_envelope.type, "course-proposal/refusal")
        self.assertTrue(second.response_envelope.payload["refused"])
        # The refusal crossing is booked on both sides, reverse direction.
        resp_id = second.response_envelope.transmission_id
        self.assertTrue(
            any(en.transmission_id == resp_id and en.side == "SEND" for en in rb.ledger)
        )
        self.assertTrue(
            any(en.transmission_id == resp_id and en.side == "RECEIVE" for en in sb.ledger)
        )
        # And logged at the seam (C2).
        self.assertTrue(
            any(
                ev.get("event") == "crossing"
                and ev.get("decision") == "act"
                for ev in seam_book.events
            )
        )


class TestGc(unittest.TestCase):
    def test_readings_gc_at_declared_rate_ledger_kept(self):
        store = StateStore("x", reading_gc_keep=2)
        for i in range(5):
            store.record_reading("cell", i, taken_at=float(i))
        store.book(
            "SEND",
            transmission_id="t1",
            peer="y",
            payload_type="telemetry",
            payload_hash="h",
            ending="PROJECT",
            cost=0.0,
            contract="c@1",
        )
        self.assertEqual(store.gc(), 3)
        self.assertEqual(len(store.readings), 2)
        self.assertEqual(len(store.ledger), 1)


if __name__ == "__main__":
    unittest.main()
