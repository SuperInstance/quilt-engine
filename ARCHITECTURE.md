# Architecture — module map

## Provenance and the synthesis decision

The Gemini rounds proposed this library twice, with incompatible layouts:

- **Layout A** (gemini1): `quilt.core / quilt.jev / quilt.engine / quilt.gm`
- **Layout B** (gemini2, gemini7–9): `beamquilt/` with `core/ seams/ engine/ distill/ patches/`

**Decision (liaison synthesis):** Layout B's module boundaries, under the package name `quilt_engine`. Layout B wins because its boundaries match the SPEC's own structure — patch contract, transmission, seam handshake, scope distillation are separate concerns in the spec, so they are separate modules here. Layout A's `gm` (game-master) module is dropped: the game-engine framing is a reading of the design, not a component of it; the loop it named lives in `engine/`. Nothing from either layout's *physics* vocabulary (fields, waves, lattices) is carried over — it names no mechanism in either proposal.

Confidence key, used per module below:

- **Settled** — stated normatively in beam-quilt SPEC.md; build exactly this.
- **Proposed** — named in the Gemini rounds and consistent with the SPEC, but not in the SPEC; build the simple version, expect revision.
- **Speculative** — mentioned, unmechanized; do not build in v0.

---

## `quilt_engine/core/` — transmissions and state

*Implements SPEC §2 (Transmission) and §1 P4–P5.*

- `TransmissionEnvelope` — the tuple `{payload, type, sender-profile, ending, cost-budget}`. **Settled.** Exactly one ending per transmission — MUTATE / MOVE / COPY / PROJECT — chosen and logged; COPY is never a default (T2).
- `AlgorithmContract` — the callable shape a patch exposes: input type → `OUTPUT + WHY + COST` (P2). **Settled.** A bare score with no why is non-conforming; the contract type must make that unrepresentable or loudly invalid.
- `StateStore` — a patch's own state and its own transmission/decision log, garbage-collected at a rate the patch declares in its manifest (P5). **Settled** in obligation, **proposed** in storage form: a local append-only file is the v0 answer.

**Need-to-knows for the builder:**
- Build: plain dataclasses / typed dicts, serializable to JSON. The envelope is the only thing that crosses a seam — no state references inside it, ever (P4).
- Do NOT build: any shared/global state, any registry of live patches, any serialization format beyond JSON in v0.
- Read: SPEC §1–2; beam-quilt docs/01. Ignore: all latent-vector / tensor-seam material in the proposed docs (research option, see BUILD-BRIEF non-goals).

## `quilt_engine/seams/` — the Jev pair

*Implements SPEC §3 (the seam handshake) and §4 (gates).*

- `SenderJev.project(envelope) -> profile` — projects a typed vector (completeness, substance, confidence-by-aspect) describing the outgoing payload. It describes; it does **not** decide delivery. **Settled.**
- `ReceiverJev.evaluate(payload, profile, competing_pool) -> decision` — re-measures the payload with its own questions and decides **comparatively, against the pool of candidates competing for the receiver's attention in this round**. **Settled — and the single most-misimplemented point in the proposals:** several Gemini files quietly replace the comparative pick with an absolute score against fixed thresholds. That is a different, weaker mechanism. Never an absolute score.
- `Deadband` — the band between go and no-go. Landing in it means: **hold, ask a further typed question, or escalate to a larger model scoped to exactly this boundary** (SPEC §3.3). This is the *clarify* outcome. **Settled.**
- `Abstain` — **reserved, not implemented.** The Gemini rounds also used the deadband to mean "do nothing, coast" (gemini15). That is a different act from clarifying. **This is the one open decision in the design, and it is Casey's:** clarify-only (SPEC as written), or abstain added as a distinct fourth outcome alongside clarify. Until ruled, the code carries the outcome value in the decision type and raises/flags if constructed. Do not silently pick one.
- Disagreement record — if sender profile and receiver measurement diverge beyond a declared tolerance, the divergence is logged as a first-class seam event, never averaged away (SPEC §3.4). **Settled.**
- Gates judge **instances**, not types (G1); a gate whose decisions become formula-shaped graduates to an explicit equation, logged with its evidence, with a cheap judge kept on spot-check duty (G2–G3). **Settled** as rules; the graduation machinery is **proposed** for after v0.

**Need-to-knows:**
- Build: `JevPair` as the pair object holding one sender and one receiver judge plus the deadband; decisions are one of accept / refuse / clarify (+ reserved abstain).
- Do NOT build: threshold-only arbitration, score averaging between sender and receiver, any "resonance" scheduling.
- Read: SPEC §3–4; beam-quilt docs/04. Ignore: the wave-interference and oscillator material.

## `quilt_engine/distill/` — scope distillation

*Implements the mechanism behind beam-quilt docs/05; supports SPEC T1 (untypable crossings are decomposed first).*

- `TreeSplitter` — decomposes an unbounded question at fixed, typed angles until every sub-question is narrow enough for a small judge. **Proposed** (the decomposition discipline is settled in the study; this class shape is from the proposals).
- `ReassemblyEngine` — synthesizes sub-question outputs back into a coherent action (named in gemini2; v0's decomposition had no named reassembly half). **Proposed.**

**Need-to-knows:**
- Build: only what the v0 seam demo needs — in practice, nothing in v0. This module is scaffolding for the second build, not the first.
- Do NOT build: automatic angle discovery, learned decomposition. Fixed typed angles only.
- Read: beam-quilt docs/05.

## `quilt_engine/engine/` — the loop

*Supports SPEC §1 P6 (haltable) and §5 C3 (the stop test).*

- The game loop: receive → profile → pick → act → re-profile (SPEC §3.5: an acting patch becomes the sender for the next seam, and its outgoing profile describes the *transformed* result, not its input). **Settled** as a sequence.
- Tick scheduling: event-driven wake — a patch emits/acts when something crosses, and is dormant otherwise. **Proposed** in the simple form (the rounds' "tick economy / resonance allocation" is **speculative**; do not implement scoring-based compute allocation).
- Halt: any patch, and the whole quilt, must stop without the cooperation of the thing being halted (C3). In v0 this is a property of the two-process demo harness, not a daemon. **Settled** as a requirement.

**Need-to-knows:**
- Build: the thinnest loop that runs the BUILD-BRIEF demo.
- Do NOT build: a daemon, a supervisor tree, hot-swapping. Supervision and runtime lifecycle belong to **continuum-runtime**, not this library.
- Read: SPEC §5; beam-quilt docs/03 (prism and BEAM — influence as proposal, receiver sovereign).

## `quilt_engine/patches/` — the patch contract surface

*Implements SPEC §1 (the patch).*

- `PatchManifest` — the fixed envelope: name, what it accepts, what it emits (with types), what it costs, its log's garbage-collection rate. Human-readable **without opening the patch** (P1). One manifest, in Markdown or JSON, is a v0 deliverable. **Settled.**
- Patch base shape: standalone worth is a conformance property (P3) — a patch must be useful or honestly evaluable with no quilt around it. The library provides the manifest and contract types; it does not provide a patch registry or loader in v0. **Settled / proposed** respectively.

**Need-to-knows:**
- Build: the manifest schema and one example manifest.
- Do NOT build: a plugin system, discovery, or a patch store. Admitting patches to a running system is a runtime concern (continuum-runtime).
- Read: SPEC §1; beam-quilt docs/02; the sibling `patchwork-experts` envelope and `quilt-cells` cell spec (input → output + why + cost) are the prior art this schema must stay compatible with.

---

## Cross-cutting rules (all modules)

- **Consent is load-bearing, not decoration** (SPEC §5): enrolment is the owner's act (C1); every crossing is logged with type, profile, pick, ending, and cost, readable by the owner (C2). The Gemini rounds let these rules fade after gemini19 — in this library they are acceptance criteria, not aspirations.
- **No shared memory** is an architectural invariant, not a style preference: two patches in one process for a test must still communicate only through envelopes.
- Where this document says **settled**, the SPEC is the citation. Where it says **proposed**, build the simple version and flag the choice in code comments. Where **speculative**, build nothing.
