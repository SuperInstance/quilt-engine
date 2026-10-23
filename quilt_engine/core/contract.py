"""AlgorithmContract — the callable shape a patch exposes (SPEC P2).

For every input, a patch produces ``OUTPUT + WHY + COST``. A bare score
with no why is non-conforming, so the contract makes it unrepresentable:
the wrapped callable must return a ``ContractResult``, and
``AlgorithmContract.run`` refuses anything else loudly.

v0.3 (DESIGN-V03 §1, §6.4): the contract's **version is derived, never
typed** — it is the SHA-256 of the contract's canonical *statement*
(``statement_version``), so a hand-written version label is
unrepresentable, the same discipline P2 applies to bare scores. A
``label`` ("v2") may ride along for display only: it is outside the
hashed bytes and never part of the identity (§1.3 — whether labels
should be identity is the author's §8.3, and the design's ruling-neutral
stance is hash-only). Ordering comes from the revision chain's
``supersedes`` pointers, not from the hash and not from a counter.

The hash binds an *agreement* (the statement), not a binary: two builds
with one statement and different code share one version. A statement may
cite its dependencies by content hash (``spec_clause_hash``), never by a
mutable pointer (§1.6).
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

from .envelope import canonical_json


def canonical_statement_bytes(statement: dict | list | str) -> bytes:
    """The exact bytes a version is hashed over (DESIGN-V03 §1.7).

    Statements are data wherever the shape allows: dicts/lists hash via
    canonical JSON (sorted keys, no whitespace drift — the
    ``canonical_json`` discipline). A prose statement is still hashable:
    UTF-8, NFC-normalised, LF line endings, no trailing whitespace. Two
    spellings of one algorithm hash differently; that is §7's honest
    limit, not a bug to paper over here.
    """
    if isinstance(statement, str):
        text = unicodedata.normalize("NFC", statement)
        lines = [
            line.rstrip()
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ]
        return "\n".join(lines).encode("utf-8")
    return canonical_json(statement).encode("utf-8")


def statement_version(statement: dict | list | str) -> str:
    """The version of a contract statement: sha256 of its canonical bytes."""
    return hashlib.sha256(canonical_statement_bytes(statement)).hexdigest()


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
    (gap 3). v0.3: ``version`` is computed from the canonical statement
    (§6.4) and ``label`` is display-only, outside the hashed bytes.
    """

    name: str
    statement: dict | list | str
    fn: Callable[[Any], ContractResult]
    label: str | None = None  # display only ("v2"); never identity (§1.3)

    @property
    def version(self) -> str:
        return statement_version(self.statement)

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
