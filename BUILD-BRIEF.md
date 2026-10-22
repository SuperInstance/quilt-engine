# Build brief — version zero

The smallest build that proves the seam works. Source: the build order the Gemini rounds converged on at their most grounded (gemini6), which matches what the SPEC already implies. Everything here is in scope; everything in the non-goals is explicitly somebody else's job or a later version.

## Deliverables

1. **`TransmissionEnvelope` + `JevPair` classes** (`quilt_engine/core/`, `quilt_engine/seams/`)
   - Envelope carries `{payload, type, sender-profile, ending, cost-budget}` (SPEC T1–T3), JSON-serializable.
   - `SenderJev.project()` attaches the typed profile; `ReceiverJev.evaluate()` decides **comparatively against the competing pool** and applies the receiver's deadband — a context-set, learnable **(band, yaw-time)** pair around its predicted/expected course — returning `Hold`, `HoldWatch`, or `Act(response)`. There is no reserved or producible `Abstain` value: coasting is `Hold` behaviour inside the band, and clarify/narrow is one possible `Act` response.
   - Sender/receiver divergence beyond a declared tolerance writes a disagreement record.
2. **One patch manifest** (`quilt_engine/patches/`) — a single Markdown or JSON `PatchManifest` for one example patch: name, accepts, emits (typed), cost, log GC rate (SPEC P1, P5).
3. **A two-process demonstration** (`examples/`) — two OS processes, **no shared memory**, passing one transmission through the arbitrator over a local transport (pipe, socket, or file drop — builder's choice, stated in the example). The run must exhibit **Hold** (inside the band), **HoldWatch → Hold** (a transient excursion returns inside before the yaw-time expires), and **Act(response)** (an excursion persists beyond the yaw-time). Its Act scenarios include clarify/narrow — a further typed question is asked — and refusal, logged with the profile returned to the sender.

## Acceptance tests

- [ ] Envelope round-trips through JSON with type, profile, ending, and cost-budget intact; an envelope with zero or two endings is rejected (T2).
- [ ] COPY ending cannot be produced by default construction — it requires an explicit choice (T2).
- [ ] Receiver decision demonstrably depends on the competing pool: the same payload produces different policy outcomes against a weak pool and against a strong one (comparative pick, SPEC §3). A test that passes with an absolute threshold does not satisfy this.
- [ ] A transient excursion outside the band that returns inside before the yaw-time expires produces `HoldWatch` then `Hold`, never `Act`.
- [ ] An excursion persisting beyond the yaw-time produces `Act(response)`; when the policy chooses clarify/narrow, it produces a further typed question (or a scoped escalation stub), never a silent accept or refuse.
- [ ] Sender/receiver divergence beyond tolerance writes a disagreement record to the seam log; the record is not an average of the two measurements (§3.4).
- [ ] Refusal returns the refusal profile to the sender and is logged at the seam (C2).
- [ ] The two processes share no memory: killing either process mid-run leaves the other intact, and the survivor's log accounts for the interrupted transmission (P4, P6).
- [ ] Either process can be halted externally and stays halted (C3, demo-harness level).
- [ ] Every crossing in the demo run appears in a log readable after the fact, with payload type, profile, pick, ending, and cost (C2).

## Explicit non-goals

- **No latent-vector seams.** Passing raw activations/embeddings between different models is a research option from the proposals, unpriced and unproven; v0 seams carry typed payloads and profiles only.
- **No networking beyond local transport.** No mesh, no discovery, no remote nodes in v0.
- **No runtime or daemon.** Process lifecycle, supervision, hot-swapping → **continuum-runtime**.
- **No recording / flight recorder.** Event-stream recording, replay, scrubbing → **chrono-flight** (chrono-tensor). This library emits seam logs (C2); it does not build the recorder.
- **No tick economy, no resonance scheduling, no lattice/wave machinery** — speculative in the proposals, unmechanized, out.
- **No real model calls required.** Judges in v0 may be small deterministic stand-ins, provided the comparative-pick test above is genuinely exercised; wiring a real Jev (see the sibling `jev-semantic`) is the next build, not this one.
