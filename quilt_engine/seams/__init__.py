"""seams — JevPair: SenderJev.project(), ReceiverJev.evaluate(), Deadband.
Implements beam-quilt SPEC §3 (seam handshake) and §4 (gates). Picks are comparative.
"""

from .jev import (
    AcceptBest,
    Act,
    ClarifyNarrow,
    Deadband,
    Decision,
    DisagreementRecord,
    Escalate,
    Hold,
    HoldWatch,
    Measurement,
    ReceiverJev,
    Refuse,
    SenderJev,
)
from .pair import DEFAULT_CONTRACT_ID, CrossingResult, JevPair

__all__ = [
    "AcceptBest",
    "Act",
    "ClarifyNarrow",
    "Deadband",
    "Decision",
    "DisagreementRecord",
    "Escalate",
    "Hold",
    "HoldWatch",
    "Measurement",
    "ReceiverJev",
    "Refuse",
    "SenderJev",
    "DEFAULT_CONTRACT_ID",
    "CrossingResult",
    "JevPair",
]
