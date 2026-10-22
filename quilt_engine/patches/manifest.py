"""PatchManifest — the fixed envelope a patch presents (SPEC P1).

Name, what it accepts, what it emits (typed), what it costs, and its
log's garbage-collection rate — human-readable *without opening the
patch*. The library provides the schema and one example manifest; it
does not provide a registry or loader (ARCHITECTURE, settled/proposed).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PatchManifest:
    name: str
    accepts: list[str]
    emits: list[str]
    cost: str
    log_gc_rate: str
    contract: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("P1: a manifest must name the patch")
        if not self.accepts or not self.emits:
            raise ValueError("P1: a manifest must declare accepts and emits (typed)")
        if not self.log_gc_rate:
            raise ValueError("P5: a manifest must declare its log GC rate")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PatchManifest":
        return cls(
            name=d["name"],
            accepts=list(d["accepts"]),
            emits=list(d["emits"]),
            cost=d["cost"],
            log_gc_rate=d["log_gc_rate"],
            contract=d.get("contract", ""),
        )

    @classmethod
    def load(cls, path: str | Path) -> "PatchManifest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "accepts": self.accepts,
            "emits": self.emits,
            "cost": self.cost,
            "log_gc_rate": self.log_gc_rate,
            "contract": self.contract,
        }
