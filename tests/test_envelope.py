"""Acceptance: envelope shape and the one-ending rules (SPEC T1–T3)."""

import unittest

from quilt_engine.core import Ending, TransmissionEnvelope
from quilt_engine.core.contract import (
    AlgorithmContract,
    ContractResult,
    statement_version,
)
from quilt_engine.patches import PatchManifest


def make(**kw):
    kw.setdefault("payload", {"merit": 0.5, "a": 1})
    kw.setdefault("type", "telemetry")
    kw.setdefault("cost_budget", 2.0)
    return TransmissionEnvelope(**kw)


class TestEnvelopeRoundTrip(unittest.TestCase):
    def test_json_round_trip_intact(self):
        env = make(ending=Ending.MOVE)
        env.sender_profile = {
            "completeness": 1.0,
            "substance": 0.4,
            "confidence_by_aspect": {"content": 1.0, "form": 0.4},
            "judge": "sender-jev",
        }
        back = TransmissionEnvelope.from_json(env.to_json())
        self.assertEqual(back.payload, env.payload)
        self.assertEqual(back.type, "telemetry")
        self.assertEqual(back.sender_profile, env.sender_profile)
        self.assertEqual(back.ending, Ending.MOVE)
        self.assertEqual(back.cost_budget, 2.0)
        self.assertEqual(back.hash, env.hash)
        # Provenance slot (OTel span context) survives the round trip.
        self.assertEqual(back.provenance.trace_id, env.provenance.trace_id)
        self.assertEqual(back.provenance.span_id, env.provenance.span_id)

    def test_zero_endings_rejected(self):
        with self.assertRaises(ValueError):
            TransmissionEnvelope.from_dict(
                {"payload": {}, "type": "x", "cost_budget": 0}
            )
        with self.assertRaises(ValueError):
            make(ending=None)

    def test_two_endings_rejected(self):
        with self.assertRaises(ValueError):
            make(ending=["MOVE", "COPY"])
        with self.assertRaises(ValueError):
            TransmissionEnvelope.from_dict(
                {"payload": {}, "type": "x", "ending": ["MOVE", "PROJECT"]}
            )

    def test_unknown_ending_rejected(self):
        with self.assertRaises(ValueError):
            make(ending="TELEPORT")


class TestCopyIsNeverADefault(unittest.TestCase):
    def test_default_construction_is_not_copy(self):
        self.assertIsNot(make().ending, Ending.COPY)
        self.assertIs(make().ending, Ending.PROJECT)

    def test_copy_requires_explicit_attributed_choice(self):
        with self.assertRaises(ValueError):
            make(ending=Ending.COPY)
        env = make(ending=Ending.COPY, copy_authorized_by="owner-standing-order-7")
        self.assertIs(env.ending, Ending.COPY)
        back = TransmissionEnvelope.from_json(env.to_json())
        self.assertIs(back.ending, Ending.COPY)
        self.assertEqual(back.copy_authorized_by, "owner-standing-order-7")


class TestContractShape(unittest.TestCase):
    def test_output_why_cost(self):
        contract = AlgorithmContract(
            name="echo",
            statement={"algorithm": "echo", "output": "input"},
            fn=lambda x: ContractResult(output=x, why="echo is honest", cost=0.1),
        )
        # v0.3 (§6.4): the version is derived from the statement, so the
        # identity is name@sha256(statement) — a typed label is unrepresentable.
        self.assertEqual(contract.version, statement_version(contract.statement))
        self.assertEqual(contract.identity, f"echo@{contract.version}")
        self.assertEqual(contract.run(3).why, "echo is honest")

    def test_bare_score_is_non_conforming(self):
        contract = AlgorithmContract(name="bad", statement={"algorithm": "bare"}, fn=lambda x: 0.9)
        with self.assertRaises(TypeError):
            contract.run(3)
        with self.assertRaises(ValueError):
            ContractResult(output=1, why="", cost=0.0)


class TestManifest(unittest.TestCase):
    def test_example_manifest_loads(self):
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "quilt_engine"
            / "patches"
            / "edge_fleet_node.json"
        )
        m = PatchManifest.load(path)
        self.assertEqual(m.name, "edge-fleet-node")
        self.assertIn("telemetry", m.accepts)
        self.assertTrue(m.log_gc_rate)


if __name__ == "__main__":
    unittest.main()
