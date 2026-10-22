"""AlgorithmContract — the callable shape a patch exposes (SPEC P2).

For every input, a patch produces ``OUTPUT + WHY + COST``. A bare score
with no why is non-conforming, so the contract makes it unrepresentable:
the wrapped callable must return a ``ContractResult``, and
``AlgorithmContract.run`` refuses anything else loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ContractResult:
    output: Any
    why: str
    cost: float

    def __post_init__(self) -> None:
        if not isinstance(self.why, str) or not self.why:
            raise ValueError("P2: a result without a WHY is non-conforming")
        if self.cost < 0:
            raise ValueError("P2: cost must be >= 0")


@dataclass
class AlgorithmContract:
    """A named, versioned algorithm — its identity goes in ledger entries.

    "A contract is an algorithm" (beam-quilt FOUNDATIONS). The identity
    string (``name@version``) is what P5 bookings record, so a crossing
    can later be audited against the exact algorithm that governed it
    (gap 3).
    """

    name: str
    version: str
    fn: Callable[[Any], ContractResult]

    @property
    def identity(self) -> str:
        return f"{self.name}@{self.version}"

    def run(self, inp: Any) -> ContractResult:
        result = self.fn(inp)
        if not isinstance(result, ContractResult):
            raise TypeError(
                "P2: contract callable must return ContractResult"
                "(OUTPUT + WHY + COST); a bare value is non-conforming"
            )
        return result
