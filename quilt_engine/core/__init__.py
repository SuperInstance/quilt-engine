"""core — TransmissionEnvelope, AlgorithmContract, StateStore.
Implements beam-quilt SPEC §2 (Transmission) and §1 P4-P5 (sovereign state, own log).
"""

from .contract import AlgorithmContract, ContractResult
from .envelope import Ending, Provenance, TransmissionEnvelope
from .state import (
    PROJECTION_HORIZON_FACTOR,
    LedgerEntry,
    Projection,
    Reading,
    StateStore,
    agreed_crossings,
)

__all__ = [
    "AlgorithmContract",
    "ContractResult",
    "Ending",
    "Provenance",
    "TransmissionEnvelope",
    "LedgerEntry",
    "Projection",
    "Reading",
    "StateStore",
    "agreed_crossings",
    "PROJECTION_HORIZON_FACTOR",
]
