# quilt-engine

**A Python library implementing beam-quilt's transmission envelope and Jev-pair seam arbitration: two processes, no shared memory, proposals evaluated comparatively with a deadband.**

For builders of sovereign edge and fleet nodes — devices that must coordinate with peers without a central command, on hardware the owner already has, where every influence one node exerts on another is a logged, refusable proposal.

## Status

**Scaffolding only.** This repo is the directory skeleton, the module map, and the version-zero build brief. No implementation yet. The scaffolding synthesizes two incompatible layouts proposed in the Gemini conversation rounds (see [ARCHITECTURE.md](ARCHITECTURE.md)) into one — that synthesis is a liaison decision, recorded here so a builder agent does not have to re-derive it.

## Spec of record

The architecture is specified in **[beam-quilt](https://github.com/SuperInstance/beam-quilt)** — this library implements its [SPEC.md](https://github.com/SuperInstance/beam-quilt/blob/main/SPEC.md), not this README and not the Gemini proposals. Where anything in this repo and the SPEC disagree, the SPEC wins.

Companion repos in the same programme:

- **beam-quilt** — the design study and the spec (source of truth).
- **continuum-runtime** — process supervision / runtime concerns (out of scope here).
- **chrono-tensor** — the time, logging, and flight-recorder layer (out of scope here).

## Build order

1. `TransmissionEnvelope` + `JevPair` (core + seams) — see [BUILD-BRIEF.md](BUILD-BRIEF.md).
2. One Markdown/JSON `PatchManifest`.
3. Two processes passing a transmission through the arbitrator, demonstrating accept / refuse / deadband with no shared memory.

## Reading order for a builder

1. This README.
2. [ARCHITECTURE.md](ARCHITECTURE.md) — what each module is, what not to build, confidence per module.
3. [BUILD-BRIEF.md](BUILD-BRIEF.md) — the v0 build and its acceptance tests.
4. beam-quilt SPEC.md §§1–3 — the rules the code is tested against.
