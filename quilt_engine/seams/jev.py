"""The Jev pair and the deadband (SPEC §3, v1.1/v1.2).

Handshake, in order:

1. ``SenderJev.project`` — projects a typed profile describing the
   payload (completeness, substance, confidence-by-aspect). It
   describes; it does **not** decide delivery.
2. The envelope crosses. Nothing else crosses — no state (P4).
3. ``ReceiverJev.evaluate`` — re-measures the payload with its *own*
   questions and decides **comparatively, against the pool of
   candidates competing for the receiver's attention this round**.
   There is deliberately no code path that decides from a lone
   payload's absolute score: the decision is always made on the
   pool's *pick* (the comparatively best candidate), and the deadband
   is applied to how far acting on that pick would move the receiver
   from its predicted/expected course.

Deadband (Casey's autopilot ruling, SPEC §3 v1.1): a receiver-held pair
**(band, yaw-time)** around the predicted course.

- deviation inside the band -> ``Hold`` (no adjustment; coasting
  lives here — there is no Abstain outcome);
- outside the band, persisted less than the yaw-time -> ``HoldWatch``
  (observe; waves swing out and back);
- outside the band for at least the yaw-time -> ``Act(response)``,
  where the receiver's *policy* — never the band — chooses the
  response: accept-best, clarify/narrow, escalate, or refuse.

Sender/receiver divergence beyond a declared tolerance produces a
``DisagreementRecord`` with both measurements kept verbatim (SPEC
§3.4). It is never averaged away.

v0 judges are small deterministic stand-ins (BUILD-BRIEF allows this);
the comparative-pick tests exercise the real decision structure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.envelope import Ending, TransmissionEnvelope, canonical_json


# ---------------------------------------------------------------- profiles

@dataclass
class Measurement:
    merit: float
    completeness: float
    substance: float


def _completeness(payload: Any, expected_fields: list[str] | None) -> float:
    if isinstance(payload, dict):
        if expected_fields:
            present = sum(1 for f in expected_fields if payload.get(f) is not None)
            return present / len(expected_fields)
        keys = list(payload.keys())
        if not keys:
            return 0.0
        return sum(1 for k in keys if payload[k] is not None) / len(keys)
    return 1.0 if payload is not None else 0.0


def _substance(payload: Any, reference_bytes: int = 256) -> float:
    return min(1.0, len(canonical_json(payload)) / reference_bytes)


class SenderJev:
    """Projects the typed profile (SPEC §3 step 1). Describes only."""

    def __init__(
        self,
        judge_name: str = "sender-jev",
        field_expectations: dict[str, list[str]] | None = None,
    ) -> None:
        self.judge_name = judge_name
        self.field_expectations = field_expectations or {}

    def project(self, envelope: TransmissionEnvelope) -> dict[str, Any]:
        completeness = _completeness(
            envelope.payload, self.field_expectations.get(envelope.type)
        )
        substance = _substance(envelope.payload)
        return {
            "completeness": round(completeness, 4),
            "substance": round(substance, 4),
            "confidence_by_aspect": {
                "content": round(completeness, 4),
                "form": round(substance, 4),
            },
            "judge": self.judge_name,
        }


# ---------------------------------------------------------------- deadband

@dataclass
class Deadband:
    """(band, yaw-time) around a predicted course (SPEC §3 v1.1)."""

    band: float
    yaw_time: float
    context: str = ""
    safety_corridor: float | None = None

    def __post_init__(self) -> None:
        if self.band < 0 or self.yaw_time < 0:
            raise ValueError("band and yaw-time must be >= 0")
        if self.safety_corridor is not None and self.band > self.safety_corridor:
            raise ValueError(
                "SPEC §3: in a constrained context the band MUST remain "
                "inside the safety corridor"
            )


# ---------------------------------------------------------------- decisions

@dataclass
class Hold:
    pick_id: str | None
    deviation: float
    kind: str = "hold"


@dataclass
class HoldWatch:
    pick_id: str | None
    deviation: float
    excursion_elapsed: float
    kind: str = "hold_watch"


@dataclass
class AcceptBest:
    envelope: TransmissionEnvelope
    kind: str = "accept_best"


@dataclass
class ClarifyNarrow:
    question: TransmissionEnvelope
    kind: str = "clarify_narrow"


@dataclass
class Escalate:
    scope: str
    about_transmission_id: str
    kind: str = "escalate"


@dataclass
class Refuse:
    profile: dict[str, Any]  # the refusal profile returned to the sender (C2)
    reason: str
    kind: str = "refuse"


Response = AcceptBest | ClarifyNarrow | Escalate | Refuse


@dataclass
class Act:
    response: Response
    pick_id: str | None
    deviation: float
    kind: str = "act"


Decision = Hold | HoldWatch | Act


@dataclass
class DisagreementRecord:
    """SPEC §3.4: both measurements, verbatim. Never an average."""

    transmission_id: str
    sender_profile: dict[str, Any]
    receiver_measurement: dict[str, Any]
    divergence: float
    at: float


# ---------------------------------------------------------------- receiver

@dataclass
class _PoolEntry:
    envelope: TransmissionEnvelope
    measurement: Measurement
    arrived_at: float


class ReceiverJev:
    """Comparative pick + deadband (SPEC §3 steps 3–4).

    Parameters beyond the deadband are the receiver's own declared
    policy knobs: ``expected_course`` (its predicted course, in merit
    units), ``admissibility_floor`` and ``viability`` (policy levels
    used only *after* the deadband triggers Act, to choose the
    response), ``pool_ttl`` (candidate readings go stale — P4),
    ``divergence_tolerance`` (§3.4).
    """

    def __init__(
        self,
        deadband: Deadband,
        expected_course: float = 0.5,
        judge_name: str = "receiver-jev",
        required_fields: dict[str, list[str]] | None = None,
        admissibility_floor: float = 0.0,
        viability: float = 0.25,
        pool_ttl: float = 30.0,
        divergence_tolerance: float = 0.25,
        measure_fn: Callable[[TransmissionEnvelope], Measurement] | None = None,
    ) -> None:
        self.deadband = deadband
        self.expected_course = expected_course
        self.judge_name = judge_name
        self.required_fields = required_fields or {}
        self.admissibility_floor = admissibility_floor
        self.viability = viability
        self.pool_ttl = pool_ttl
        self.divergence_tolerance = divergence_tolerance
        self._measure_fn = measure_fn
        self._pool: dict[str, _PoolEntry] = {}
        self._excursion_start: float | None = None
        self._decided: set[str] = set()
        self.disagreements: list[DisagreementRecord] = []

    # -- the receiver's own questions -----------------------------------

    def measure(self, envelope: TransmissionEnvelope) -> Measurement:
        if self._measure_fn is not None:
            return self._measure_fn(envelope)
        completeness = _completeness(
            envelope.payload, self.required_fields.get(envelope.type)
        )
        substance = _substance(envelope.payload)
        merit = 0.5 * completeness + 0.5 * substance
        if isinstance(envelope.payload, dict) and isinstance(
            envelope.payload.get("merit"), (int, float)
        ):
            merit = float(envelope.payload["merit"])
        return Measurement(merit=merit, completeness=completeness, substance=substance)

    # -- disagreement (§3.4) ---------------------------------------------

    def _check_disagreement(
        self, envelope: TransmissionEnvelope, m: Measurement, now: float
    ) -> None:
        profile = envelope.sender_profile
        if not profile:
            return
        diffs = [
            abs(float(profile.get("completeness", m.completeness)) - m.completeness),
            abs(float(profile.get("substance", m.substance)) - m.substance),
        ]
        sender_aspects = profile.get("confidence_by_aspect", {})
        receiver_aspects = {"content": m.completeness, "form": m.substance}
        for aspect, sval in sender_aspects.items():
            if aspect in receiver_aspects:
                diffs.append(abs(float(sval) - receiver_aspects[aspect]))
        divergence = max(diffs)
        if divergence > self.divergence_tolerance:
            self.disagreements.append(
                DisagreementRecord(
                    transmission_id=envelope.transmission_id,
                    sender_profile=dict(profile),
                    receiver_measurement={
                        "completeness": round(m.completeness, 4),
                        "substance": round(m.substance, 4),
                        "merit": round(m.merit, 4),
                        "judge": self.judge_name,
                    },
                    divergence=round(divergence, 4),
                    at=now,
                )
            )

    # -- policy (chooses the response once Act triggers; never the band) --

    def _policy(self, pick: _PoolEntry | None) -> Response:
        if pick is None or pick.measurement.merit < self.admissibility_floor:
            bad = pick.envelope if pick else None
            profile = {
                "refused": True,
                "transmission_id": bad.transmission_id if bad else None,
                "reason": "no admissible candidate in pool",
                "receiver_measurement": (
                    {
                        "merit": round(pick.measurement.merit, 4),
                        "completeness": round(pick.measurement.completeness, 4),
                        "substance": round(pick.measurement.substance, 4),
                    }
                    if pick
                    else None
                ),
                "judge": self.judge_name,
            }
            return Refuse(profile=profile, reason="no admissible candidate in pool")
        if pick.measurement.merit < self.viability:
            # Weak but not inadmissible: ask a further typed question
            # rather than silently accepting or refusing (§3, clarify).
            src = pick.envelope
            question = TransmissionEnvelope(
                payload={
                    "question": (
                        f"Narrower {src.type}: which aspect of "
                        f"'{src.transmission_id}' carries the value?"
                    ),
                    "about": src.transmission_id,
                    "aspect": "content",
                },
                type=f"{src.type}/narrow",
                ending=Ending.PROJECT,
                cost_budget=0.0,
            )
            return ClarifyNarrow(question=question)
        return AcceptBest(envelope=pick.envelope)

    # -- evaluation --------------------------------------------------------

    def evaluate(
        self,
        envelope: TransmissionEnvelope | None = None,
        *,
        pool: list[TransmissionEnvelope] | None = None,
        now: float | None = None,
    ) -> Decision:
        """Decide on the current pool. Comparative — always.

        ``envelope`` adds (or refreshes) one candidate; ``pool`` replaces
        the candidate set outright (in-process use); neither is required
        on a tick call, which exists so the yaw-time can expire.
        """
        now = time.monotonic() if now is None else now
        if pool is not None:
            self._pool = {}
            for env in pool:
                self._add(env, now)
        if envelope is not None:
            self._add(envelope, now)
        self._prune(now)

        if not self._pool:
            self._excursion_start = None
            return Hold(pick_id=None, deviation=0.0)

        # The comparative pick: best of the pool competing this round.
        pick = max(
            self._pool.values(),
            key=lambda e: (e.measurement.merit, -e.arrived_at),
        )
        deviation = abs(pick.measurement.merit - self.expected_course)

        if deviation <= self.deadband.band:
            self._excursion_start = None
            return Hold(pick_id=pick.envelope.transmission_id, deviation=deviation)

        # Outside the band: the excursion timer governs (v1.1).
        if self._excursion_start is None:
            self._excursion_start = now
            return HoldWatch(
                pick_id=pick.envelope.transmission_id,
                deviation=deviation,
                excursion_elapsed=0.0,
            )
        elapsed = now - self._excursion_start
        if elapsed < self.deadband.yaw_time:
            return HoldWatch(
                pick_id=pick.envelope.transmission_id,
                deviation=deviation,
                excursion_elapsed=elapsed,
            )
        response = self._policy(pick)
        self._pool = {}
        self._excursion_start = None
        # A decided crossing is decided: later re-assertions of the same
        # transmission are booked by the seam (they did cross) but do not
        # re-enter the pool. A genuinely new proposal needs a new
        # transmission id.
        self._decided.add(pick.envelope.transmission_id)
        return Act(
            response=response,
            pick_id=pick.envelope.transmission_id,
            deviation=deviation,
        )

    def _add(self, envelope: TransmissionEnvelope, now: float) -> None:
        if envelope.transmission_id in self._decided:
            return
        m = self.measure(envelope)
        if envelope.transmission_id not in self._pool:
            # First sight of this candidate; a refresh of a known one is
            # the same crossing, not a new disagreement (§3.4).
            self._check_disagreement(envelope, m, now)
        self._pool[envelope.transmission_id] = _PoolEntry(envelope, m, now)

    def _prune(self, now: float) -> None:
        stale = [
            tid
            for tid, e in self._pool.items()
            if now - e.arrived_at > self.pool_ttl
        ]
        for tid in stale:
            del self._pool[tid]
