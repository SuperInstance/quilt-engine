"""Acceptance, v0.1: readings carry a vector (gap 1, the fog layer).

A reading of a numeric cell derives a trend from the previous reading
of the same cell and can project itself — linearly, locally, and only
a short way: the horizon is capped at a multiple of the spacing the
trend came from. The receiver may form its expected course from that
projection when its own latest reading is stale, and every decision
(and every seam-log crossing) records whether a projection was used
or refused. These tests drive everything on injected clocks.
"""

import tempfile
import unittest
from pathlib import Path

from quilt_engine.core import StateStore, TransmissionEnvelope
from quilt_engine.seams import (
    Deadband,
    Hold,
    HoldWatch,
    JevPair,
    ReceiverJev,
    SenderJev,
)

SENDER = SenderJev(field_expectations={"telemetry": ["a", "b"]})


def env(merit, type_="course-proposal"):
    e = TransmissionEnvelope(
        payload={"merit": merit, "a": 1, "b": 2}, type=type_, cost_budget=1.0
    )
    e.sender_profile = SENDER.project(e)
    return e


def course_log():
    """A receiver's own readings of the channel: 0.30 @0s, 0.40 @2s.

    Trend +0.05/s from a 2.0 s spacing; horizon cap 3x spacing = 6 s.
    The world keeps moving at +0.05/s after the last reading.
    """
    store = StateStore("receiver")
    store.record_reading("course", 0.30, taken_at=0.0)
    store.record_reading("course", 0.40, taken_at=2.0)
    return store


def fog_receiver(log=None, **kw):
    kw.setdefault("deadband", Deadband(band=0.15, yaw_time=0.8))
    kw.setdefault("expected_course", 0.40)  # the stale raw value, as fallback
    kw.setdefault("pool_ttl", 60.0)
    if log is not None:
        kw.setdefault("log", log)
        kw.setdefault("course_cell", "course")
    return ReceiverJev(**kw)


class TestTrendDerivation(unittest.TestCase):
    def test_second_reading_carries_the_vector(self):
        store = course_log()
        latest = store.latest_reading("course")
        self.assertAlmostEqual(latest.trend, 0.05)
        self.assertEqual(latest.trend_from, (0.0, 2.0))
        self.assertEqual(latest.trend_spacing, 2.0)
        # The first reading of a cell has nothing to derive from.
        self.assertIsNone(store.readings[0].trend)

    def test_trend_is_per_cell_and_numeric_only(self):
        store = StateStore("s")
        store.record_reading("a", 1.0, taken_at=0.0)
        store.record_reading("b", 5.0, taken_at=1.0)  # different cell
        self.assertIsNone(store.latest_reading("b").trend)
        store.record_reading("a", {"merit": 0.5}, taken_at=2.0)  # non-numeric
        self.assertIsNone(store.latest_reading("a").trend)
        # ...and a numeric reading after a non-numeric one stays trendless.
        store.record_reading("a", 2.0, taken_at=3.0)
        self.assertIsNone(store.latest_reading("a").trend)

    def test_same_instant_readings_derive_nothing(self):
        store = StateStore("s")
        store.record_reading("a", 1.0, taken_at=1.0)
        store.record_reading("a", 9.0, taken_at=1.0)
        self.assertIsNone(store.latest_reading("a").trend)

    def test_projected_value_is_value_plus_trend_times_age(self):
        latest = course_log().latest_reading("course")
        self.assertAlmostEqual(latest.projected_value(2.0), 0.40)
        self.assertAlmostEqual(latest.projected_value(6.0), 0.60)
        p = latest.project(6.0)
        self.assertAlmostEqual(p.value, 0.60)
        self.assertEqual(p.horizon, 4.0)
        self.assertEqual(p.max_horizon, 6.0)
        self.assertEqual(p.based_on, (0.0, 2.0))
        self.assertFalse(p.refused)

    def test_trend_survives_a_persisted_book(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "book.jsonl"
            store = StateStore("s", path=path)
            store.record_reading("course", 0.30, taken_at=0.0)
            store.record_reading("course", 0.40, taken_at=2.0)
            loaded = StateStore.load("s", path)
            latest = loaded.latest_reading("course")
            self.assertAlmostEqual(latest.trend, 0.05)
            self.assertAlmostEqual(latest.projected_value(6.0), 0.60)


class TestHorizonCap(unittest.TestCase):
    """The counter-case: extrapolation must refuse, not prophesy."""

    def test_beyond_the_cap_is_refused_out_loud(self):
        latest = course_log().latest_reading("course")
        # Cap is 6 s past the reading (t=2): t=8 is the edge, t=9 is out.
        self.assertIsNotNone(latest.projected_value(8.0))
        self.assertIsNone(latest.projected_value(9.0))
        p = latest.project(9.0)
        self.assertTrue(p.refused)
        self.assertIn("exceeds cap", p.reason)

    def test_noisy_close_readings_cannot_project_far(self):
        store = StateStore("s")
        store.record_reading("course", 0.0, taken_at=0.0)
        wild = store.record_reading("course", 0.5, taken_at=0.1)
        self.assertAlmostEqual(wild.trend, 5.0)  # a wild slope...
        self.assertAlmostEqual(wild.project(0.1).max_horizon, 0.3)  # tiny cap
        self.assertIsNotNone(wild.projected_value(0.35))  # a glance ahead
        self.assertIsNone(wild.projected_value(5.0))  # ...not a forecast
        self.assertTrue(wild.project(5.0).refused)

    def test_trendless_reading_projects_nothing(self):
        store = StateStore("s")
        only = store.record_reading("course", 0.5, taken_at=0.0)
        self.assertIsNone(only.projected_value(1.0))
        self.assertEqual(only.project(1.0).reason, "reading carries no trend")


class TestFogScenario(unittest.TestCase):
    """The direct reading is stale; the wake in the state is not.

    World: the course moves at +0.05/s. The receiver's last reading
    (0.40, at t=2) is 4 s stale at decision time (t=6); the true
    course is 0.60.
    """

    def test_projection_keeps_the_expectation_correct(self):
        r = fog_receiver(log=course_log())
        d = r.evaluate(pool=[env(0.60)], now=6.0)
        self.assertIsInstance(d, Hold)
        self.assertAlmostEqual(d.expected_course, 0.60)
        self.assertAlmostEqual(d.deviation, 0.0)
        self.assertIsNotNone(d.projection)
        self.assertTrue(d.projection.used)
        self.assertEqual(d.projection.horizon, 4.0)

    def test_the_stale_value_would_have_misjudged(self):
        # Control: the same receiver with no log steers against the
        # stale 0.40 and calls the true course an excursion.
        blind = fog_receiver()
        d = blind.evaluate(pool=[env(0.60)], now=6.0)
        self.assertIsInstance(d, HoldWatch)
        self.assertAlmostEqual(d.deviation, 0.20)

    def test_projection_also_convicts_a_stale_match(self):
        # A candidate matching the *stale* reading is not a match any
        # more: the projection correctly puts it outside the band.
        r = fog_receiver(log=course_log())
        d = r.evaluate(pool=[env(0.40)], now=6.0)
        self.assertIsInstance(d, HoldWatch)
        self.assertAlmostEqual(d.deviation, 0.20)
        # ...where the blind receiver sits content with the stale value.
        blind = fog_receiver()
        self.assertIsInstance(blind.evaluate(pool=[env(0.40)], now=6.0), Hold)

    def test_refused_projection_falls_back_and_says_so(self):
        # t=20: horizon 18 s >> cap 6 s. The projection is refused; the
        # receiver falls back to the raw stale value, and the decision
        # carries the refusal for the seam log.
        r = fog_receiver(log=course_log())
        d = r.evaluate(pool=[env(0.45)], now=20.0)
        self.assertIsInstance(d, Hold)
        self.assertAlmostEqual(d.expected_course, 0.40)
        self.assertIsNotNone(d.projection)
        self.assertTrue(d.projection.refused)
        self.assertFalse(d.projection.used)
        self.assertIn("exceeds cap", d.projection.reason)


class TestSeamLogAudit(unittest.TestCase):
    """Through JevPair: the fog behaviour lands in the seam log (C2)."""

    def make_pair(self):
        sender_book = StateStore("sender")
        receiver_book = StateStore("receiver")
        receiver_book.record_reading("channel", 0.30, taken_at=0.0)
        receiver_book.record_reading("channel", 0.40, taken_at=2.0)
        seam_book = StateStore("seam")
        receiver = ReceiverJev(
            deadband=Deadband(band=0.15, yaw_time=0.8),
            expected_course=0.40,
            pool_ttl=60.0,
            log=receiver_book,
            course_cell="channel",
        )
        pair = JevPair(
            SenderJev(), receiver, sender_book, receiver_book,
            seam_book=seam_book,
        )
        return pair, seam_book

    def test_crossing_records_the_projection_it_used(self):
        pair, seam_book = self.make_pair()
        result = pair.cross(env(0.60), now=6.0)
        self.assertIsInstance(result.decision, Hold)
        crossings = [ev for ev in seam_book.events if ev.get("event") == "crossing"]
        self.assertEqual(len(crossings), 1)
        c = crossings[0]
        self.assertAlmostEqual(c["expected_course"], 0.60)
        self.assertIsNotNone(c["projection"])
        self.assertTrue(c["projection"]["used"])
        self.assertFalse(c["projection"]["refused"])
        self.assertEqual(c["projection"]["horizon"], 4.0)
        self.assertEqual(c["projection"]["based_on"], [0.0, 2.0])
        self.assertAlmostEqual(c["projection"]["value"], 0.60)

    def test_crossing_records_a_refusal_too(self):
        pair, seam_book = self.make_pair()
        pair.cross(env(0.45), now=20.0)
        c = [ev for ev in seam_book.events if ev.get("event") == "crossing"][0]
        self.assertTrue(c["projection"]["refused"])
        self.assertFalse(c["projection"]["used"])
        self.assertIn("exceeds cap", c["projection"]["reason"])
        self.assertAlmostEqual(c["expected_course"], 0.40)


if __name__ == "__main__":
    unittest.main()
