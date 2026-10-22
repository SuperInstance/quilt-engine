# Foundations application — quilt-engine

What beam-quilt's verified foundations (beam-quilt `FOUNDATIONS.md`, Lane A
pull list) mean for this repo specifically. Programme-level reasoning lives
there; this file is only the repo-local translation. Confidence tags:
**SETTLED** (in beam-quilt SPEC or verified by the foundations pass),
**PROPOSED** (adopt the mechanism, shape may revise), **SPECULATIVE** (do not
build).

## PULL / ADAPT here

### ADAPT — OpenTelemetry span context / W3C propagation (Apache-2.0) → `core/TransmissionEnvelope`
- **Replaces:** any bespoke provenance encoding inside the envelope.
  ARCHITECTURE.md currently defines the envelope as `{payload, type,
  sender-profile, ending, cost-budget}` — it has **no provenance slot at
  all**. The foundations pass names this exact gap: adopt trace/span IDs +
  links for provenance, attributes/events for intent and cost, W3C
  propagation formats for interop with edge tooling.
- **Scope limit:** adapt the envelope encoding only. OTel defines no
  storage, replay, or tracks — nothing else from it enters this repo.
- Tag: **PROPOSED** (mechanism settled as the choice; field mapping to the
  envelope tuple is this repo's design work).

### ADAPT — Conformal quantiles + SGR binomial-bound calibration (arXiv:1705.08500, arXiv:2107.07511) → `seams/` deadband band edges
- **Replaces:** hand-set band edges for the receiver's **(band,
  yaw-time)** deadband (SPEC rule G2). Band edges become formula-shaped,
  set from a small calibration set, with an auditable statistical story.
  Yaw-time remains a separate context-set, learnable duration parameter;
  conformal calibration does not collapse the pair into one threshold.
- **Two mandatory inversions:** (1) band the *receiver's* scores, not a
  model's self-assessment; (2) calibration informs the Act trigger, not
  the Act response — clarify/narrow is one policy response after a
  persistent excursion, not the meaning of the band. Sender/receiver
  divergence is non-exchangeability — log it (SPEC §3.4), never average
  it away.
- **Does not touch the comparative rule:** calibration sets where the band
  edges sit; it never converts the pick into an absolute score.
  BUILD-BRIEF's comparative-pick acceptance test still governs.
- Tag: **PROPOSED** for after v0 (the ARCHITECTURE already marks gate
  graduation machinery as proposed; this is that machinery's formula).

### ADAPT — Mozannar & Sontag comparative-defer loss + Switch Transformer divergence logging (arXiv:2006.01862, arXiv:2101.03961) → `seams/ReceiverJev` + disagreement record
- **Replaces:** an ad-hoc receiver score and the unspecified
  divergence-logging step of the seam handshake. Cross-entropy over
  classes ∪ {defer} is the credible starting formula for a comparative
  pick and, once the deadband triggers Act, for the policy's response
  choice; it does not define the deadband trigger itself. Switch's
  dispatched-fraction-vs-mean-probability term templates the
  divergence-from-uniform log (SPEC G3).
- Tag: **PROPOSED** (formula source settled; our extension — N competing
  senders, receiver-side decision rights — is unclaimed in the literature
  and is ours to validate).

### ADAPT — DecomP typed decomposition + base-case stop rule (arXiv:2210.02406, code Apache-2.0) → `distill/TreeSplitter`
- **Replaces:** designing decomposition semantics from scratch. Keep the
  named-handler type system and decompose-until-small-enough recursion.
- **Inversion already present in ARCHITECTURE.md and kept:** our typed
  angles are static (fixed schema), not LLM-generated programs, so the
  decomposer need not be the smartest model in the fleet. This matches the
  existing "fixed typed angles only; no automatic angle discovery" rule —
  no change needed, foundations confirm it.
- Tag: **PROPOSED** (unchanged from ARCHITECTURE.md).

### ADAPT — Chronicle Queue named tailers (Apache-2.0) → `core/StateStore` log reading
- **Replaces:** the plain "local append-only file" as the *reading*
  discipline for the P5 patch log. Named, persistent, read-without-consume
  tailers are how each reader (owner audit under C2, the patch itself)
  keeps its own position in the log. Rebuild the semantics in Python; do
  not vendor the Java original.
- Tag: **PROPOSED** (storage form was already "proposed" in ARCHITECTURE;
  this names the mechanism).

### STUDY pattern — Letta memory-as-git-repo (→ `core/StateStore`)
- Owner-readable, versioned patch state is a fit for C2's readability
  requirement. A pattern to borrow if StateStore outgrows a flat file —
  not a dependency, not v0 work. Tag: **SPECULATIVE** for this repo.

## BUILD OUR OWN (no off-the-shelf equivalent — foundations-verified)

- **The seam itself.** Receiver-side comparative admission with a deadband
  is unclaimed across every surveyed agent framework and the whole gating
  literature. `seams/` is the genuinely unbuilt piece; the ADAPTs above
  supply formulas at its edges, never its decision rights.
- **Latent seams stay parked.** Stitching needs a trained translator fit on
  paired activations (5K–25K+ samples in the kindest results; no LLM
  demonstration; identity transfer fails). Un-park only with a named
  calibration corpus, stitch-point granularity, and acceptance test.
  ARCHITECTURE's "ignore all latent-vector material" rule stands.

## Decisions — deadband ruled; state model still open elsewhere

### Deadband semantics — RULED by Casey, 2026-10-21 (autopilot doctrine)
Casey rejected both earlier framings — clarify-only, and clarify plus
abstain as a fourth outcome. The mechanism is a **(band, yaw-time)**
pair around the receiver's predicted/expected course of action:

- **Band:** maximum tolerated deviation for the current context. It is
  narrow where the safety margin is narrow and wider where deviation
  is cheap; in constrained contexts it stays inside the safety corridor.
- **Yaw-time:** maximum duration an excursion outside the band may
  persist before action. A return inside the band before it expires
  resets the excursion.
- **Decision type:** `Hold` (inside the band), `HoldWatch` (outside
  the band, within yaw-time), and `Act(response)` (outside beyond
  yaw-time). The response is chosen by policy — accept-best,
  clarify/narrow, escalate, or refuse where no candidate is admissible.
- **No `Abstain` value:** coasting is Hold behaviour inside the band.
  Clarify is an Act response, not the definition of the deadband.
- **Parameters:** band and yaw-time are context-set and learnable.
  Conformal calibration sets band edges from receiver calibration
  data; learning the settings and the outside-band response is the ML
  problem.
- **Acceptance tests:** a transient excursion outside the band that
  returns inside within yaw-time produces `HoldWatch` then `Hold`,
  never `Act`; an excursion persisting beyond yaw-time produces
  `Act(response)`.

### State model
Not this repo's question — `StateStore` already implements SPEC P4/P5
(stored sovereign state + own log) and nothing in the foundations reopens
it here. If continuum-runtime's trajectory-only proposal were ever ruled
in, the SPEC amendment would land in beam-quilt first and this repo would
follow; until then, stored state is **SETTLED** here.

## Conflicts found against the verified foundations

1. **Envelope has no provenance field** (ARCHITECTURE.md, `core/`) while
   the foundations assign OTel span context to exactly this envelope.
   Gap, not contradiction — the tuple predates the pull list. Resolve by
   designing the OTel-shaped provenance slot, not by inventing a local
   encoding.
2. **Deadband wording superseded by ruling** (ARCHITECTURE.md,
   `seams/`): the earlier clarify/abstain framing has been replaced by
   Casey's 2026-10-21 (band, yaw-time) mechanism and the
   `Hold` / `HoldWatch` / `Act(response)` decision type. No reserved
   abstain value remains.
3. No occurrences of any refuted claim (sub-millisecond, rigid-graph,
   OPAL lineage, global optimality) exist in this repo — verified in the
   foundations pass; nothing to correct.
