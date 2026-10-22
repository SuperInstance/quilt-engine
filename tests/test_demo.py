"""Acceptance: the two-process demo (BUILD-BRIEF deliverable 3).

- The full run exhibits Hold, HoldWatch -> Hold, Act(accept-best),
  Act(clarify/narrow), Act(refuse), a disagreement record, and
  both-sides agreement on every crossing (verified by the demo's own
  orchestrator).
- No shared memory (P4/P6): killing either process mid-run leaves the
  other intact, and the survivor's book accounts for the interrupted
  transmission.
- Either process can be halted externally and stays halted (C3).
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "two_process_demo.py"
sys.path.insert(0, str(ROOT))

from quilt_engine.core import StateStore, agreed_crossings  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def start(role: str, work: Path, env_extra=None, until=None):
    env = dict(os.environ, DEMO_TIME_SCALE="0.6")
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, str(DEMO), "--role", role, "--dir", str(work)]
    if until is not None:
        cmd += ["--until", str(until)]
    return subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class TestFullDemo(unittest.TestCase):
    def test_full_run_exhibits_everything(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, DEMO_TIME_SCALE="0.6")
            proc = subprocess.run(
                [sys.executable, str(DEMO), "--dir", td],
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("DEMO OK", proc.stdout)


def wait_for_receives(work: Path, n: int, timeout: float = 15.0) -> set:
    """Poll the receiver's book until n crossings have actually landed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (work / "receiver_book.jsonl").exists():
            book = StateStore.load("vessel-b", work / "receiver_book.jsonl")
            got = {e.transmission_id for e in book.ledger if e.side == "RECEIVE"}
            if len(got) >= n:
                return got
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {n} received crossings")


class TestKillMidRun(unittest.TestCase):
    def test_kill_sender_receiver_survives_and_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            recv = start("receiver", work, until=1.2)
            send = start("sender", work, env_extra={"DEMO_SENDER_HANG": "1"})
            wait_for_receives(work, 2)  # A and D have both crossed
            send.kill()  # mid-run: D's excursion is open, never resolves
            send.wait(timeout=10)
            # The survivor is intact and finishes its own run.
            self.assertIsNone(recv.poll())
            recv.wait(timeout=30)
            self.assertEqual(recv.returncode, 0)
            rbook = StateStore.load("vessel-b", work / "receiver_book.jsonl")
            received = {e.transmission_id for e in rbook.ledger if e.side == "RECEIVE"}
            self.assertEqual(len(received), 2)  # A and D both crossed
            ends = [e for e in rbook.events if e.get("event") == "session_end"]
            self.assertEqual(len(ends), 1)
            unresolved = ends[0]["unresolved_excursions"]
            self.assertEqual(len(unresolved), 1)
            self.assertIn(unresolved[0], received)

    def test_kill_receiver_sender_survives_and_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            recv = start("receiver", work)
            send = start("sender", work)
            wait_for_receives(work, 1)  # at least A has crossed and is agreed
            time.sleep(0.3)
            recv.kill()
            recv.wait(timeout=10)
            # Sender keeps running to the end of its own script.
            send.wait(timeout=120)
            self.assertEqual(send.returncode, 0)
            sbook = StateStore.load("vessel-a", work / "sender_book.jsonl")
            rbook = StateStore.load("vessel-b", work / "receiver_book.jsonl")
            sent = {e.transmission_id for e in sbook.ledger if e.side == "SEND"}
            got = {e.transmission_id for e in rbook.ledger if e.side == "RECEIVE"}
            # Everything the sender sent is in *its* book (the account);
            # crossings after the kill never reached the other side and
            # therefore are not agreed — shared state is agreement only.
            self.assertTrue(got < sent)
            agreed, unagreed = agreed_crossings(sbook.ledger, rbook.ledger)
            self.assertEqual(set(unagreed), sent - got)
            self.assertTrue(agreed)


class TestExternalHalt(unittest.TestCase):
    def test_sigterm_halts_and_stays_halted(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            recv = start("receiver", work)
            time.sleep(0.5)
            self.assertIsNone(recv.poll())
            recv.send_signal(signal.SIGTERM)
            recv.wait(timeout=10)
            self.assertIsNotNone(recv.returncode)
            time.sleep(0.4)
            # Stays halted: no supervisor, no resurrection (C3,
            # demo-harness level; supervision is continuum-runtime's).
            self.assertIsNotNone(recv.poll())


if __name__ == "__main__":
    unittest.main()
