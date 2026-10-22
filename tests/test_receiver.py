"""Acceptance: the comparative pick and the (band, yaw-time) deadband.

These tests drive ReceiverJev with an injected clock, so the excursion
timing is exact, not slept through.
"""

import unittest

from quilt_engine.core import TransmissionEnvelope
from quilt_engine.seams import (
    Act,
    ClarifyNarrow,
    Deadband,
    Hold,
    HoldWatch,
    ReceiverJev,
    Refuse,
    SenderJev,
)

SENDER = SenderJev(field_expectations={"telemetry": ["a", "b"]})


def env(merit, type_="course-proposal", payload_extra=None):
    payload = {"merit": merit, "a": 1, "b": 2}
    if payload_extra:
        payload.update(payload_extra)
    e = TransmissionEnvelope(payload=payload, type=type_, cost_budget=1.0)
    e.sender_profile = SENDER.project(e)
    return e


def receiver(**kw):
    kw.setdefault("deadband", Deadband(band=0.15, yaw_time=0.8))
    kw.setdefault("expected_course", 0.50)
    kw.setdefault("pool_ttl", 60.0)
    return ReceiverJev(**kw)


class TestComparativePick(unittest.TestCase):
    """The same payload, two pools, different outcomes (SPEC §3).

    An absolute-threshold implementation CANNOT pass this test: the
    payload's own score is identical in both runs; only the competing
    pool changes, and with it the pick and the decision.
    """

    def test_same_payload_weak_pool_vs_strong_pool(self):
        r = receiver(deadband=Deadband(band=0.10, yaw_time=0.5), expected_course=0.55)
        target = env(0.62)
        weak = r.evaluate(pool=[target, env(0.20)], now=0.0)
        self.assertIsInstance(weak, Hold)
        self.assertEqual(weak.pick_id, target.transmission_id)

        r2 = receiver(deadband=Deadband(band=0.10, yaw_time=0.5), expected_course=0.55)
        target2 = env(0.62)
        strong_rival = env(0.95)
        strong = r2.evaluate(pool=[target2, strong_rival], now=0.0)
        # The pick is now the rival, and the decision flips to an excursion.
        self.assertEqual(strong.pick_id, strong_rival.transmission_id)
        self.assertIsInstance(strong, HoldWatch)
        self.assertNotEqual(type(weak), type(strong))


class TestDeadbandTiming(unittest.TestCase):
    def test_transient_excursion_holdwatch_then_hold_never_act(self):
        r = receiver()
        d1 = r.evaluate(pool=[env(0.95)], now=10.0)
        self.assertIsInstance(d1, HoldWatch)
        # Still out, still inside the yaw-time.
        d2 = r.evaluate(now=10.4)
        self.assertIsInstance(d2, HoldWatch)
        # Returns inside the band before the yaw-time expires.
        d3 = r.evaluate(pool=[env(0.52)], now=10.7)
        self.assertIsInstance(d3, Hold)
        # And no Act was ever produced along the way.
        d4 = r.evaluate(now=11.5)
        self.assertIsInstance(d4, Hold)

    def test_persistent_excursion_acts(self):
        r = receiver()
        self.assertIsInstance(r.evaluate(pool=[env(0.97)], now=0.0), HoldWatch)
        self.assertIsInstance(r.evaluate(now=0.79), HoldWatch)
        d = r.evaluate(now=0.80)
        self.assertIsInstance(d, Act)
        self.assertEqual(d.response.kind, "accept_best")
        self.assertEqual(d.response.envelope.payload["merit"], 0.97)

    def test_persistent_weak_pool_clarifies_never_silently_decides(self):
        r = receiver()
        r.evaluate(pool=[env(0.10)], now=0.0)
        d = r.evaluate(now=1.0)
        self.assertIsInstance(d, Act)
        self.assertIsInstance(d.response, ClarifyNarrow)
        q = d.response.question
        self.assertEqual(q.type, "course-proposal/narrow")
        self.assertEqual(q.payload["about"], d.pick_id)

    def test_persistent_inadmissible_pool_refuses(self):
        r = receiver()
        r.evaluate(pool=[env(-0.20)], now=0.0)
        d = r.evaluate(now=1.0)
        self.assertIsInstance(d, Act)
        self.assertIsInstance(d.response, Refuse)
        self.assertTrue(d.response.profile["refused"])
        self.assertEqual(d.response.profile["transmission_id"], d.pick_id)


class TestDisagreement(unittest.TestCase):
    def test_divergence_beyond_tolerance_is_recorded_not_averaged(self):
        r = receiver(required_fields={"telemetry": ["a", "b", "c"]})
        e = env(0.55, type_="telemetry")
        # Sender (expects a,b) says completeness 1.0; receiver (requires
        # a,b,c) measures 2/3. Divergence 1/3 > tolerance 0.25.
        r.evaluate(pool=[e], now=0.0)
        self.assertEqual(len(r.disagreements), 1)
        rec = r.disagreements[0]
        self.assertEqual(rec.sender_profile["completeness"], 1.0)
        self.assertAlmostEqual(rec.receiver_measurement["completeness"], 2 / 3, places=3)
        self.assertAlmostEqual(rec.divergence, 1 / 3, places=3)
        # Not an average of the two measurements.
        self.assertNotAlmostEqual(rec.divergence, (1.0 + 2 / 3) / 2, places=3)
        self.assertNotAlmostEqual(
            rec.receiver_measurement["completeness"], (1.0 + 2 / 3) / 2, places=3
        )

    def test_within_tolerance_no_record(self):
        r = receiver(required_fields={"telemetry": ["a", "b"]})
        r.evaluate(pool=[env(0.55, type_="telemetry")], now=0.0)
        self.assertEqual(r.disagreements, [])


class TestDeadbandShape(unittest.TestCase):
    def test_band_must_stay_inside_safety_corridor(self):
        with self.assertRaises(ValueError):
            Deadband(band=0.5, yaw_time=1.0, safety_corridor=0.3)
        Deadband(band=0.2, yaw_time=1.0, safety_corridor=0.3)  # no raise


if __name__ == "__main__":
    unittest.main()
