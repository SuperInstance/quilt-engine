"""seams — JevPair: SenderJev.project(), ReceiverJev.evaluate(), Deadband.
Implements beam-quilt SPEC §3 (seam handshake) and §4 (gates). Picks are comparative.
v0.3: the seam's contract is a derived-version AlgorithmContract pinned to
handshake_v1.json (DESIGN-V03 §6.7).
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
from .pair import (
    DEFAULT_CONTRACT_FAMILY,
    DEFAULT_CONTRACT_ID,
    HANDSHAKE_STATEMENT_V1,
    SEAM_CONTRACT_V1,
    CrossingResult,
    JevPair,
)

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
    "DEFAULT_CONTRACT_FAMILY",
    "HANDSHAKE_STATEMENT_V1",
    "SEAM_CONTRACT_V1",
    "CrossingResult",
    "JevPair",
]
