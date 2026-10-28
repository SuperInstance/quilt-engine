# Design — v0.4: the fog layer, deepened (gap 1)

**Design only.** No code changes here. This note is the design for the next
deepening of beam-quilt's gap 1 — *a per-observer tensor of dated readings with
explicit staleness and a predictive model filling the fog* — in the shape gap 1
actually needs once the fog has to be **honest about how wrong it can be**.

- **Repo:** `SuperInstance/quilt-engine` @ `a54bb51` (v0.3, 85 tests green)
- **Spec of record:** beam-quilt `SPEC.md` v1.2 (P1–P6, T1–T3, §3, G1–G3, C1–C3)
- **Doctrine:** beam-quilt `FOUNDATIONS.md` — "Shelf foundations — the two active
  items"; gap 1 as stated in the Lane C/D sweep
- **Prior design:** `DESIGN-V03-contract-versioning.md` (gap 3) — the house style
  for reserving author rulings is §8 there, and this note reserves its own

Confidence tags used below, as in `ARCHITECTURE.md` and DESIGN-V03: **Settled**
(normative in the SPEC), **Proposed** (this note's design, buildable), **Speculative**
(do not build).

### Where the brief's six questions land

| # | Question | Section |
|---|---|---|
| 1 | Confidence as a function of age | [§2](#2-confidence-as-a-function-of-age) |
| 2 | Calibration from outcomes (conformal) | [§3](#3-calibration-from-outcomes) |
| 3 | Prediction records as bookable claims | [§4](#4-prediction-records) |
| 4 | Disagreement between projection and arrival | [§5](#5-disagreement-the-ordered-update) |
| 5 | Cost honesty | [§6](#6-cost-honesty) |
| 6 | Field-level diff against v0.3 | [§7](#7-minimality--field-level-changes-to-v03s-shapes) |

---

## 0. Where v0.1 actually stands — and the specific hole

v0.1 gave every reading a **vector** and let the receiver steer against a
projection of it. That is gap 1 in its smallest honest form and it is already in
the code:

- `Reading.trend` / `trend_from` / `trend_spacing` — the last-pair slope, derived
  in `StateStore._derive_trend` (`quilt_engine/core/state.py:362-380`).
- `Reading.project(at_time, horizon_factor)` — the linear extrapolation, refused
  out loud past `PROJECTION_HORIZON_FACTOR × trend_spacing` (`state.py:112-151`).
- `ReceiverJev._expected_course_at(now)` — the receiver's expected course is the
  latest reading of `course_cell`, projected to now when it is stale
  (`quilt_engine/seams/jev.py:356-379`).
- The seam log records the projection that was used, adopted *or* refused, next
  to every crossing (`quilt_engine/seams/pair.py:240-255`).

Three observations, in the order they matter.

**1. The fog can produce a point, but never an uncertainty.** `_expected_course_at`
returns a **single float** — the projected value if the projection was adopted,
the raw (stale) value if it was refused. There is no width anywhere. The
deviation is then `abs(pick.merit − expected)` (`jev.py:419`) and the band
compares against that scalar. So a receiver that is *deep in the fog* and a
receiver that has a *fresh reading* are treated as equally certain, and the
only thing the fog can do is be **wrong** (a refused projection falls back to a
stale number). It cannot be **uncertain**. That is the hole: the fog layer
prices nothing.

**2. Staleness is explicit but inert.** `Reading.is_stale(now, max_age)` exists
(`state.py:109-110`) and nothing in the decision path calls it. Age reaches the
decision only *indirectly*, through the horizon cap: inside the cap, age is
invisible; outside it, the projection is refused outright. v0.1's account of
"acting on a reading MUST account for its date" (SPEC P4) is therefore
**binary** — extend the reading, or refuse it. There is no middle. A reading
four seconds old and a reading four *hundred* seconds old get identical
treatment as long as the second one still fits inside `3 × spacing`.

**3. The projection is unfalsifiable, and so is the band.** `Projection` is a
transient object constructed at decision time and thrown away; its `used` flag
is the only trace. When a later reading contradicts it, nothing is recorded,
nothing is learned, and the band — a hand-set `Deadband(band=0.15, yaw_time=0.8)`
in the tests — is unaffected. **You cannot calibrate what you did not write
down**, and v0.1 writes down nothing that could be scored.

**The question this note answers:** what does the fog layer look like when it
has to say *how wrong it might be*, where does that number come from, and what
has to be written down for it to be learnable at all.

### A vocabulary note

The brief says "tell". This repo says **`cell`** — `Reading.cell` is the name of
the thing read. The two are the same thing and the note uses **tell** where the
sentence is about the *concept* and **cell** where it is about the *field*. A
tell's **policy** is the per-cell calibration state; the *estimator* is what
produced a reading's vector. These are two different things and §3 keeps them
apart.

---

## 1. The fog's output shape — an expectation with a width, not a point

The whole design hangs on one change of shape.

v0.1's receiver produces a **point** expectation. v0.4's receiver produces an
**expectation with a width**. Conceptually:

```
point        the expected course          (v0.1's expected_course — unchanged)
half_width   the fog's own uncertainty    (new)
source       "declared" | "reading" | "projection"
confidence   the reading's effective weight
reading_age  how old the reading is
projection   the Projection behind it, adopted or refused
```

(The concrete shape is in §7.6: the point and the projection stay exactly where
v0.1 put them, and the new half — width, confidence, age — rides along in a
`FogTrace` sub-record, so nothing v0.1 reads has to move.)

The **deviation** stays a scalar — it is still `|pick.merit − point|` — but it
is now measured against a point that is *known to be surrounded by uncertainty*.
Three consequences fall out of that, and they are the mechanism:

**(a) The band absorbs the fog.** The effective band is

```
band_effective = band + fog_allowance(now)
```

where `fog_allowance` is the half-width of the fog itself (§3). A receiver in
thick fog tolerates more deviation, because most of the deviation it sees is
its own ignorance rather than the world moving. This is the whole point of the
deepening: **the fog stops being a source of false alarms.**

**(b) The fog's width is learned, not assumed.** `fog_allowance` is not a
constant and not a guess. It is a conformal quantile of the *measured* residuals
of this tell's own past predictions (§3), scaled by an error model fitted to
those same residuals. The foundations pass named this method — "conformal
calibration sets band edges from the receiver's calibration data" (this repo's
`FOUNDATIONS-APPLICATION.md`) — and this note is the engagement with it.

**(c) The fog's width is bounded by the safety corridor, and the overflow is
not laundered into `Hold`.** This is the sharpest consequence and it needs its
own rule.

SPEC §3 (v1.1): *in a constrained context, the band MUST remain inside the
applicable safety corridor*. If the fog's allowance is wide enough that
`band + allowance` would cross the corridor, the receiver may **not** widen into
it. And it may not pretend the deviation is inside the band either — a very wide
allowance would make the deviation test vacuous, so that everything is inside
the band, so the receiver always coasts. **That is blindness dressed as
confidence**, and it is the exact failure the corridor exists to prevent.

> **Fog overflow rule.** If `band + fog_allowance > safety_corridor` (when a
> corridor is declared), the receiver is *blind*, not *certain*. The
> band-widening is refused; the effective band is clamped to the corridor; and
> the overflow is recorded as a first-class event. The deviation test is then
> **replaced** by an explicit cannot-hold condition: the excursion trigger
> fires on the fog's behalf, and the receiver's policy chooses the response.
> The SPEC's own response set already contains the right answers — **escalate**
> (call a larger model scoped to exactly this boundary) and **clarify/narrow**
> (ask a further typed question). "I am too blind to hold" is an *Act*, not a
> `Hold`.

This is what gives the fog teeth. A fog that only ever widens the band is a
fudge factor; a fog that can also *compel an Act* is a mechanism.

**What the fog must not touch.** The **comparative pick**. The foundations pass
is explicit: calibration sets where the band edges sit; *it never converts the
pick into an absolute score*. Two places where the fog could smuggle itself
into the pick, both refused here:

- **The pool TTL.** `ReceiverJev.pool_ttl` decides which candidates are still
  live. If calibration set it, the fog would be pruning the comparative set and
  the pick would no longer be comparative among live candidates. It stays a
  declared receiver policy knob.
- **The admissibility floor.** `admissibility_floor` and `viability` are the
  policy's thresholds *after* the deadband triggers. They are not calibration
  parameters and the fog does not touch them.

---

## 2. Confidence as a function of age

The brief asks three things: the decay shape, per-tell or global, and where the
weight lives. Taken in that order.

### 2.1 The shape — confidence is uncertainty, and a trust horizon

v0.1's error model is implicit and, in effect, "the error grows with the
horizon". v0.4 makes it explicit and fitted. The **scale model** `s(h)` is the
expected magnitude of the residual at elapsed time `h` seconds:

```
s(h) = sqrt( a²  +  (b·h)²  +  (σ̂_slope · h / S)² )
```

- `a` — **baseline noise.** The outcome reading's own error plus the
  projection's immediate error. Fitted from the short-horizon end of the
  residual pool.
- `b` — **drift sensitivity.** How fast the prediction error accumulates as the
  world moves on under the prediction. Fitted from the growth of the residuals
  with elapsed time.
- `σ̂_slope · h / S` — **the estimator's own extrapolation error.** A slope
  estimated from points spaced `S` apart carries slope noise `σ̂_slope`; a
  linear extrapolation to `h` beyond the last point amplifies it by `h / S`.
  This term is what makes v0.1's "noisy close readings cannot project far" guard
  principled instead of arbitrary: with a small `S` the term is large, so the
  usable horizon collapses on its own.
  `σ̂_slope` is estimated from the spread of the tell's own successive slopes and
  is available **immediately** — it does not wait for any outcome.

The three terms are in quadrature because they are independent sources of error,
and the model is multiplicative in `h` because error *accumulates*, it does not
add linearly to the value.

The **confidence of a reading at age `u`** is then the width of the fog around
it:

```
fog_allowance(u) = q_α · s(u)
```

where `q_α` is the conformal quantile factor (§3). Note the direction: **the
reading's confidence decays as uncertainty grows.** Staleness does not shrink
the reading; it widens the region in which the reading might be right.

Alongside that, the reading carries a **trust** scalar for the log and for one
genuinely binding case:

```
trust(u) = max(0, 1 − u / T_tell)
```

linear to zero at the tell's **trust horizon** `T_tell`. `trust` is a summary;
the *width* is what binds. But at `u = T_tell` the reading's *value* is no
longer a point estimate at all — the receiver must stop quoting it as one and
fall back to its declared course, or to a projection that is still inside the
calibrated cap.

### 2.2 Per-tell, not global

**The decay policy is per-tell**, with a patch-level default as the fallback for
a tell that has never been calibrated.

The reasoning is that a tell's reliability and dynamics are its own. A single
global trust horizon would be a claim about every tell at once, and it would be
wrong for at least one of them — and wrong in the dangerous direction, because
a global decay cannot be *worse* for the tell that deserves it. The per-tell
policy is also nearly free: the tell is named by `Reading.cell`, so the policy
is a lookup, not a copy (§6.1).

The fallback for an uncalibrated tell is **no allowance at all** — v0.1's
behaviour exactly — and every crossing that relies on it says so
(`fog.calibrated = false`). The patch does not invent a number it has not
measured. A patch may *declare* a conservative default allowance as a policy;
that is a declared parameter, not a measurement, and the seam log distinguishes
the two.

### 2.3 Where it lives — the reading record carries only what is knowable at write time

This is the part worth being precise about, because the naive answer is wrong.

**Nothing age-dependent is stored in the reading record.** A stored weight would
have to be rewritten as the reading ages, and an append-only log may not be
rewritten — v0.3 established the discipline ("state is a fold of records",
"going forward, not backward"), and a reading whose confidence field is edited
as it ages is a mutable record pretending to be an immutable one. The age of a
reading is already recoverable from `taken_at`; the policy is already reachable
from `cell`; the estimator's basis is already in `trend_from` / `trend_spacing`.

So the reading record gains exactly two things:

```
+ estimator:  str | None      # which estimator produced this vector
+ confidence: float | None    # the confidence the patch held in this reading,
                              #   at the moment it was taken (tell reliability,
                              #   age zero). Historical fact, not a live weight.
```

- `estimator` exists so that a residual can be **attributed** to the estimator
  that made it. With v0.1's single estimator it is always
  `"last-pair-linear"`; it earns its keep the moment a second estimator
  appears (§3.3). One cheap field now, rather than a migration later.
- `confidence` records the trust the patch *held* when it read the cell. That is
  knowable at write time and it is historically meaningful: a reading taken
  while the tell's reliability was 0.4 and one taken at 0.95 are different
  evidence, and the reading-reliability statistic wants to know which was which.

**The effective weight lives in the decision.** At decision time the receiver
computes `trust(u)` and `fog_allowance(u)` from the tell's policy and the
reading's age, and writes both into the decision's fog trace and the seam log.
The reading is a fact; the weight is a fact *about now*, and it is recorded
where it was used.

### 2.4 The one place the weight actually binds

A scalar weight on the reading would be the intuitive design and it is the wrong
one: scaling a reading's value toward zero makes the *expectation* wrong
rather than the *comparison* honest. A reading that is 40 % trusted is not a
reading of 40 % value; it is a reading whose neighborhood is wider.

So the mechanism is: **the reading's weight is expressed as the width of the
expectation, and the comparison is made against the widened expectation.** The
scalar `trust(u)` survives for two jobs — it is the audit summary in the seam
log, and it marks the trust horizon at which the reading stops being quotable
as a point at all.

---

## 3. Calibration from outcomes

### 3.1 The training signal — what confirms a reading or a projection

The brief's signal is "readings later confirmed or refuted by agreed ledger
entries". This note takes that literally, because the programme's agreement
standard is the only credibility standard it has:

- **Primary outcome — an agreed arrival about the cell.** When a crossing about
  the tell arrives and **both books** book it (SEND by one side, RECEIVE by the
  other, matching on id, payload hash and contract version — the v0.3
  `agreed_crossings` rule), then that crossing carries the cell's value, and it
  is the outcome. It is a fact about the world that *two sovereign books
  agree on*.
- **Secondary outcome — the patch's own later reading of the cell.** Available
  sooner, but it is the patch's own perspective and nothing more. It is
  recorded with `source: "self"` and is **not** admitted into the conformal pool
  by default (§3.5).

What is *not* a signal: another patch's log. P4 forbids reading it. The
outcome arrives **through the seam**, as a crossing, or not at all. The fog
never reaches sideways into a peer's state.

Note that one outcome scores **two** claims, and they are different claims:

- The residual against the tell's **latest reading** measures the *reading*
  error — the tell's reliability (§2.3's `confidence` is its historical form).
  This residual is **age-tagged too**: the last reading may be several seconds
  older than the arrival, so the reading residual carries the world's movement
  over that gap as well as the tell's own error. It is therefore *not* a
  measurement of the tell's precision at the instant of reading, and it feeds
  the tell's reliability trend rather than the fog's width — a different
  statistic, from the same stream.
- The residual against a **live projection** measures the *projection* error —
  what the fog's half-width must cover.

Same outcome stream, two populations, two statistics. That is why the pool is
partitioned by *what was claimed*, not by *when it was claimed* (§3.3).

### 3.2 The prediction is the unit of scoring

A residual is not defined against a reading; it is defined against a **claim
that was written down at a time and can be checked later**. That is §4's
prediction record. §2's and §3's calibration is impossible without it, and §3
is impossible without §4 — the note's central structural claim is that these
are one mechanism, not two features.

The residual is defined precisely:

- A prediction `i` about cell `c` is issued at `t_i`, from the reading taken at
  `t_r`, claiming that the value at the **claimed instant** `τ_i` is `p_i` with
  half-width `w_i`. In the current design the fog projects to *now*, so
  `τ_i = t_i`; the **reach** — how far beyond the reading the fog had to
  extrapolate — is `τ_i − t_r`, and that is `Projection.horizon` as the code
  already computes it.
- The outcome is the **first** agreed arrival about `c` at time `t_j ≥ τ_i`.
- `residual_i = v_j − p_i` (signed).
- `elapsed_i = t_j − t_r` — the **total extrapolation distance at the moment of
  scoring**: how far beyond the reading the fog reached, plus any wait between
  the claimed instant and the arrival. The error grows with how far the fog
  reached, so the scale model is fitted against `elapsed` — not against the
  claim's optimism, and not against the wait alone. Charging the whole
  distance is deliberately conservative: a claim that had to wait a long time
  for its truth is charged for the wait, in the direction of a wider band
  rather than a narrower one. (A refinement would split reach from latency as
  two terms in the scale model; the single `elapsed` is the simpler shape and
  errs on the safe side.)
- `covered_i = |residual_i| ≤ w_i` — did the outcome land inside the claimed
  width? This is the **coverage** measurement, and it is the thing that says
  whether the fog's uncertainty estimate is honest, as opposed to merely
  well-sized. Coverage is defined only for claims that carried a width: an
  uncalibrated tell claims none (§3.4), so its claims are scored for residual
  but not for coverage, and the coverage report counts only the claims that
  could be covered.

The offset between `t_j` and the claimed instant `τ_i` is real and is *not*
papered over: it mixes the projection's error with the world moving in the
gap. That mixture is exactly what the scale model's `a` (baseline) and `b`
(drift) terms are for. The scale model is fitted on residuals **as measured**,
so it absorbs the mixture instead of pretending it is absent.

### 3.3 The pool, the partition, and the quantile

The pool for a `(tell, estimator)` pair is the set of **resolved predictions**
about that tell whose residual was measured against a **projection** claim.
(The claims measured against a *reading* — the reading-error population — form
a second pool; it feeds the tell's reliability, not the fog's width.)

Two partitions, both necessary:

- **By claim kind** (projection vs. reading) — different populations, different
  statistics.
- **By estimator** — a residual is only comparable to a residual from the same
  estimator. When the estimator changes (§5.3), the old residuals describe a
  different mechanism and must not be pooled with the new ones.

**The quantile.** The conformal quantile factor `q_α` is the standard
split-conformal rank (the binomial-bound construction this repo's
`FOUNDATIONS-APPLICATION.md` already names, from arXiv:1705.08500 and
arXiv:2107.07511):

```
q_α = the ⌈(n+1)(1−α)⌉-th smallest of |u_1| … |u_n|,
      where u_i = residual_i / s(elapsed_i)          (normalized residual)
```

and the fog's half-width at elapsed `h` is

```
fog_allowance(h) = q_α · s(h)
```

Two properties of this construction matter here and are used deliberately:

- **Normalization is what makes one quantile work across horizons.** The raw
  residuals are heteroscedastic — they grow with `h` — so a quantile over raw
  residuals would only be valid at the horizon it was computed at. Dividing by
  the scale model first makes the normalized residuals comparable, so one
  quantile serves the whole range of horizons. This is the answer to the brief's
  "quantiles over what residuals": **over normalized residuals of projection
  claims for the same (tell, estimator)**.
- **The quantile is infinite for small `n`.** `q_α` is finite only when
  `n ≥ ⌈1/α⌉ − 1`. Below that, there is no finite band the data supports. The
  honest behaviour is not a wide guess but **no calibrated allowance at all**:
  the tell falls back to the prior (§3.4) and is reported as uncalibrated. This
  is the same discipline as `Projection.refused` — a refusal is a result.

### 3.4 The window — over predictions and over time, whichever binds first

The brief asks "over what window". Both bounds, because each catches a failure
the other misses:

- **A count bound** — at most `N` most recent resolved predictions per
  `(tell, estimator)`. Default `N = 50`, floor `N = 20`.
- **A time bound** — no residual older than `W` seconds counts. Default `W` is
  context-set.

The count bound alone lets a quiet tell keep a stale calibration forever: the
teller goes silent, the pool never changes, and the band is set by a regime
that ended months ago. The time bound alone lets a busy tell's window span a
regime change invisibly. Together they bound the pool in both events and
epochs.

**The `n` used in the rank is the pool size after truncation** — the conformal
rank is computed over exactly the residuals in the pool, and `n` is reported
with the band so the reader can see the evidence behind it.

**The prior, and the caps.** Before a tell is calibrated (or when the quantile
is infinite), the fog runs on v0.1's structural prior:

- the **structural cap**: `PROJECTION_HORIZON_FACTOR (3.0) × trend_spacing` —
  v0.1's rule, unchanged, and the reason v0.1's tests keep passing;
- **no fog allowance** — the band is exactly what the receiver declared, and
  the seam log marks the crossing `calibrated: false`. A *declared* default
  allowance is permitted as policy (§2.2), but it is a declared parameter, not
  a measurement, and the log says which one was used.

Once a tell *is* calibrated, a second cap appears — the **calibrated horizon** —
the `h` at which `q_α · s(h)` would equal the band: past it, the fog's own
width would swallow the whole band, and a projection that far out is refused
("the fog here is wider than the band"). The binding cap is the **smaller** of
the structural and calibrated caps. The structural cap stays in force as the
floor even when the measured error is *smaller*; whether a well-calibrated tell
may project *further* than `3 × spacing` is an author ruling (§9.7).

The prior and the measurement are not the same kind of thing, and the note keeps
them visibly separate: the structural cap is a **safety** rule ("a slope from
two points cannot support this"), the calibrated cap is an **accuracy** rule
("this tell's error reaches this width at this horizon"). A calibrated tell
that is worse than the prior gets the worse cap; a calibrated tell that is
better than the prior does not get the better cap without a ruling.

### 3.5 What the calibration feeds

The calibrated quantities have exactly three consumers:

1. **The fog allowance** → added to the band (§1a).
2. **The calibrated horizon** → the second cap on projections (§3.4).
3. **The coverage report** → the fraction of resolved predictions whose outcome
   landed inside the claimed half-width, and the fraction of predictions that
   were *never* resolved.

The third is not a footnote. **A pool of only the predictions that happened to
be scored is a survivorship-biased sample**, and a reliability number computed
from it is a flattering lie. The band-setting record therefore carries both the
resolved count and the unresolved fraction, so the reader can see how much of
the tell's forecast record was actually checkable.

### 3.6 The two inversions, restated

The foundations pass required two inversions for conformal calibration in this
repo, and v0.4 keeps both:

- **Band the receiver's own scores, not a model's self-assessment.** The
  residual is `actual − predicted`, where `predicted` is *the patch's own
  projection* and `actual` is *an agreed arrival*. Neither is a model's
  confidence number. Nothing in this design reads a self-reported confidence.
- **Calibration informs the Act trigger, not the Act response.** `q_α` sets how
  wide the band is, which is the trigger. The response — accept-best,
  clarify/narrow, escalate, refuse — is still the policy's choice, and the fog
  overflow (§1c) is a *trigger*, not a mandate to escalate. The policy chooses
  among the SPEC's four responses.

### 3.7 The guarantee, and its condition

The conformal coverage guarantee holds **under exchangeability** of the
calibration residuals and the live residuals. In this system that assumption is
**not free**, and the note says so rather than hiding behind the word
"calibrated":

- Residuals from different regimes are not exchangeable (a tell that changed
  behaviour has a different error distribution).
- Residuals at different horizons are not exchangeable either — which is
  precisely why they are *normalized* before the quantile is taken.
- The patch's own behaviour can change what it observes, so the sample is not
  drawn independently of the mechanism being measured.

So the band is a **well-founded heuristic with a stated condition**, and the
design's obligation is to *measure the condition's violation* rather than
assert it. That measurement is the **coverage report** (§3.5): when the
observed coverage falls below `1 − α`, the calibration is **drifting**, and
that fact must be loud in the seam log — it is G3's spot-check duty turned
onto the fog itself.

**The honest limitation, stated plainly:** when a tell's behaviour changes, the
band is wrong *until enough wrong outcomes accumulate to show it*. The
calibration is a lagging indicator. It cannot protect the patch from the first
failure of a new regime; it can only make the failures countable and the
correction bounded.

---

## 4. Prediction records

### 4.1 Should projections be bookable? Yes — and the argument is structural

A projection is a claim about the world: *at time `t + H`, the value of cell
`c` will be `p`, and I am not more than `w` away from that.* A claim like that
is **falsifiable**, and a falsifiable claim that is not written down cannot be
scored, cannot be audited, and cannot be learned from.

The brief asks two things: should they be bookable, and what does the seam log
gain. The answers are the same answer seen from two sides:

**Bookable — because calibration has nothing else to work with.** §3's entire
training signal is residuals, and a residual is defined against a written
claim. Without the record, the fog is a *prior* forever: it can be conservative
but it can never be *right*, because "right" is only definable against a
recorded claim that a later observation can check.

**Bookable — because the programme's ledger is the only place truth is
settled.** A prediction about another cell is exactly the thing SPEC T2 already
has an ending for: **PROJECT** — "influence as proposal". A projection that
*crosses* to another cell is a PROJECT-ended transmission, booked by both
sides like any other crossing, and the receiving cell may later refute it. The
envelope needs no new field for this: the claim rides in the payload, which is
already free-form.

**Bookable — because it is the only way the fog can be audited under C2.** An
owner must be able to read, for any patch, every influence it sent and
received (§3 of the SPEC). The fog's calibration state is a first-class
influence on the receiver's behaviour: it decides whether a crossing produces
a `Hold` or an `Act`. If the calibration is not readable, the receiver's
decisions are not readable, and C2 is only half met.

### 4.2 What the seam log gains

Concretely — five new seam events and one enriched crossing event:

| event | when | what it carries |
|---|---|---|
| `prediction` | the fog issues a claim at a decision | prediction id, cell, the reading it came from, the claimed instant, the reach, the predicted value, the half-width, the band and allowance in force, the confidence |
| `forecast` | the claim is scored | residual, elapsed, covered (bool), the tell's updated `q_α` and pool size |
| `fog_disagreement` | an arrival contradicts a live projection | the projection, the arrival, the residual, and the ordered update trail (§5) |
| `band_set` | the band or the allowance changes | the formula, the evidence (`n`, the quantile, the scale model), the context, the corridor check (G2) |
| `fog_overflow` | the allowance exceeds the corridor | the overflow amount, the clamped band, and the consequence |
| `crossing` (enriched) | every crossing | `fog` — the fog trace: the allowance, the effective band, the confidence, the overflow, the prediction id, and whether the tell was calibrated or running on the prior |

The last row is the one that changes the seam log's character. Today a crossing
records **what was decided and against what**. After v0.4 it also records **how
much of the decision was made on incomplete information** — the seam log
becomes a record of the receiver's blindness alongside its behaviour.

### 4.3 The discipline: booked before the outcome, never edited

A prediction record is append-only and is **written before the outcome exists**.
This is what makes it falsifiable rather than a rationalisation:

- The issue record carries the claim, the reading it came from, the claimed
  instant, the half-width, and the band in force. Those are the *terms* the
  claim was made under.
- The resolution is a **second record** with the same `prediction_id` and a
  later timestamp — never an edit of the first. This is v0.3's rule ("positions
  are booked, never edited; the fold reads the latest per version") applied to
  claims.
- The residual is computed against the **frozen** half-width from the issue
  record. If the calibration updated between the claim and its outcome, the
  score is still computed against the width the claim was made under —
  otherwise every prediction would be scored against a width chosen after the
  fact, and the coverage number would be meaningless.

### 4.4 Which claims get booked

**Every projection that is adopted and actually steers a decision.** If the fog
produced the expectation a decision was made against, the claim is booked. If
the projection was refused, there is no claim to book — but the **refusal is
already in the seam log** (v0.1's behaviour, unchanged) and it is the negative
evidence that matters just as much. A tell that refuses to project *constantly*
is a tell whose readings arrive too close together to support a projection this
far ahead (`3 × S` is smaller than the decision interval) — the receiver is too
blind to steer, not too fuzzy, and that is visible in the log as a run of
refusals.

**The volume control is upstream, not in the sampling.** A patch that decides
very often books many claims; the honest fix is a coarser decision cadence (the
fog refreshes less often), **not** a filter that books only the claims which
looked interesting — that would bias the residual sample toward the material
cases and break §3.5's coverage accounting. §6 prices this.

---

## 5. Disagreement: the ordered update

The brief's question: when a fresh reading contradicts a live projection, what
updates — the tell's reliability, the projection horizon, the band — and in what
order?

### 5.1 The disagreement is a first-class event, not a silent fallback

SPEC §3.4 already governs the sender/receiver divergence: *logged as a
first-class event, never averaged away*. The projection/arrival disagreement is
the same species of thing and gets the same treatment: a `fog_disagreement`
event in the seam log, carrying the projection, the arrival, and the residual —
both sides kept verbatim, no averaging, no smoothing.

### 5.2 The order, and why the order is the design

The order matters because **each step destroys the evidence the next one
would need**.

> **Update order.**
>
> 1. **Score the prediction first.** The claim is resolved against the
>    *frozen* terms it was issued under: residual, elapsed, covered. Nothing is
>    updated before this, because updating first would re-score the prediction
>    against a model that had already been corrected by the very outcome being
>    scored. *This is the same rule as v0.3's "per-book folds that never re-fold
>    history".*
> 2. **Then update the tell's error estimate.** The residual joins the pool for
>    that `(tell, estimator)`. The *estimate* changes; no behaviour changes yet.
> 3. **Then recompute the band.** The scale model is refitted, `q_α` is
>    recomputed over the updated pool, and the fog allowance and the calibrated
>    horizon follow. This is the new current calibration, and it governs
>    subsequent decisions only.
> 4. **Then, and only then, consider the horizon.** And the horizon moves only
>    under a *structural* fact, not under a single disagreement.

The consequence: **the disagreement's effect on behaviour is always
prospective.** A prediction that turns out wrong does not retroactively widen
the band that was in force when it was made. The band in force at issue time is
recorded in the prediction record, and the band after is recorded in the
`band_set` event. Two numbers, both visible, neither retroactive.

### 5.3 Why the horizon moves last, and moves least

Of the three candidates — reliability, horizon, band — the band is the right
place for a statistical update, the reliability statistic is the right place for
the raw evidence, and the **horizon is the wrong place for a per-event
reaction**.

The horizon cap exists because of a structural fact: a slope estimated from
two points spaced `S` apart cannot support a long extrapolation. That is a
statement about the *estimator*, not about the *last observation*. Shrinking the
cap because one prediction missed is a reaction to noise, and it sets up a
positive feedback loop in the wrong direction: a smaller cap refuses the
projection sooner, and a refused projection falls back to the **raw, staler
value** (`ReceiverJev._expected_course_at` returns `float(r.value)` on a
refusal) — so the expectation gets *worse*, the next residual comes out
*larger*, and the loop asks for a smaller cap again. A self-correcting
mechanism degenerates into a self-cancelling one, and the receiver ends up
steering on the stalest number it has.

So:

- **The band absorbs the disagreement** (statistically, through the residual).
- **The reliability statistic absorbs it** (the reading-error pool).
- **The horizon moves only when the estimator itself changes.** If the
  estimator changes from a two-point slope to a k-point least-squares fit, the
  extrapolation variance changes (`σ̂_slope · h / S` changes), and the
  calibrated horizon moves with it. That is a structural update, recorded as
  such, and the residuals of the old estimator stay in their own pool (§3.3).
- **The structural cap `3 × S` does not move on a disagreement.** It is a
  safety rule about the estimator, and only a change to the estimator (or an
  author ruling — §9.7) touches it.

### 5.4 The exception — when the disagreement is not about error but about regime

Some contradictions are not noise. If the residual is *wildly* outside the
claimed half-width — by more than the scale model says is possible at that
elapsed time — the natural reading is not "the tell got noisier" but "the
world changed". Those two are indistinguishable from inside the patch, and the
note will not pretend otherwise (§8.3).

What the design *can* do is make the distinction **visible and cheap to act
on**: a residual beyond the scale model is recorded as an **outlier**, counted
separately, and reported in the coverage report. The outlier count is the
signal that a regime break may have happened; the patch's response to it (widen
the window, reset the pool, raise the band) is a policy choice — and §9.5 puts
the reset policy to the author, because resetting the pool makes the receiver
temporarily blind at exactly the moment it most needs to see.

---

## 6. Cost honesty

The programme's honesty bar is the one `FOUNDATIONS.md` names: *rr's
cost-up-front documentation style*. So: what does the deepened fog cost, and
what is the smallest version that is still the real mechanism?

### 6.1 Storage

Measured against the shapes in §7 — the current reading line serialized with
the store's own `json.dumps(record, sort_keys=True)`, and the proposed records
with the same encoder.

| thing | v0.3 | v0.4 | delta |
|---|---|---|---|
| one reading record | **158 B** | **210 B** | **+52 B (+33 %)** — the `estimator` and `confidence` fields |
| one prediction issue record (full shape, resolution half null) | — | **403 B** | new |
| one prediction resolution record (full shape) | — | **426 B** | new |
| **one booked-and-scored claim, total** | — | **829 B** | new |
| one band-setting record | — | **350 B** | new, and rare |

The reading record grows by **one estimator id and one confidence number**, and
that is the *whole* per-reading cost. The reason it is so small is §2.3's
answer: the policy is looked up by `cell`, the age is derived from `taken_at`,
and the estimator's basis is already in `trend_from` / `trend_spacing`.

This is a direct answer to the brief's "per-tell or global", and the byte
count is the *weak* half of the argument. Storing the policy per reading
instead would cost **230 B** per reading — only ~20 B more — so bytes are not
what decides it. What decides it is that **a per-reading policy is frozen at
write time**, so it can never be improved by later calibration: it would have
to be rewritten into an append-only log, which §2.3 already forbids. The
per-tell policy is the only one of the three that can *learn*. The per-tell
choice costs one policy record per tell and nothing per reading.

The larger cost is **per decision**, not per reading. A patch deciding at 1 Hz
books a claim and a resolution — **829 B** per decided claim — which is roughly
**72 MB/day**. That is not a rounding error, so the growth must be bounded:

- **Predictions are garbage-collected at the patch's declared reading-GC rate**
  (P5 already lets the patch declare one). The calibration fold needs only the
  window (§3.4), so GC beyond the window costs the deep history, not the
  calibration.
- **Band-setting records are kept**, not GC'd — they are the G2 evidence, and
  evidence that evaporates is not evidence.

So the honest statement is: **the log grows at a constant multiple of the
decision rate, bounded by the declared GC rate.** It is not unbounded growth,
and it is not free.

### 6.2 Compute

| operation | cost | note |
|---|---|---|
| projection | **O(1)** | one multiply-add and one comparison — unchanged from v0.1 |
| scale model evaluation | **O(1)** | three terms |
| conformal quantile | **O(n)** per *pool change*, **O(1)** per decision if cached | recompute only when a prediction resolves; cache the factor between changes. `n ≤ 50` |
| pool update + refit | **O(n)** per scored prediction | negligible |
| `StateStore.latest_reading` | **O(N)** per call | **this one is worth fixing** — see below |

**The real finding is `latest_reading`.** It is a linear scan of the whole
reading list (`state.py:382-386`), and `_derive_trend` calls it on every
recorded reading (`state.py:372`). With the GC keeping `N` readings that is
`O(N)` per reading, and v0.4 will call it *per decision* for the course cell.
The fix is a `cell → latest reading` index maintained on write: `O(1)` lookup,
`O(1)` maintenance. This is a v0.4 prerequisite, not an optimisation — the fog
consults the log on every decision, and the demo's small reading counts hide
the problem until it is real.

Beyond that, the compute is genuinely trivial. **The fog's cost is not
compute.**

### 6.3 The real cost is acts

This is the part a cost table hides. The fog does not cost bytes; **it costs
acts.**

Every `Act` is a round trip (clarify/narrow), a bigger model (escalate), or a
refusal. Calibration *moves* the Act rate:

- A **wider** fog allowance → fewer Act triggers on noise → fewer round trips.
  But it also **suppresses real excursions**, and the cost of a suppressed real
  excursion is invisible in every log, because nothing happened.
- A **narrower** band → more Acts → more cost, visibly, in the seam log.

So the calibration is a **trade between visible cost (Acts) and invisible cost
(missed excursions)**, and any honest report of the fog's effect must carry
**the Act rate before and after**, not just the width. The `band_set` record
should therefore carry the Act count in the window it replaced, so the reader
can see the trade being made and not merely the number that changed.

This is also why the fog overflow rule (§1c) is designed the way it is. The
alternative — letting a very wide allowance make the deviation test vacuous —
would be a way of **buying quietness with blindness**, and it would show up in
no log at all.

### 6.4 The smallest version that is still the real mechanism

The programme's habit is minimal form first, but the full mechanism named. Here
the minimum is unusually forced, and the reason is worth stating plainly:

> **The minimal version is not "widen the band when the reading is stale".**
> Without the booked claim there is no residual; without the residual there is
> no calibration; without the calibration the allowance is a guess. The chain
> is forced by the mechanism, not by convenience.

**v0.4-min — build this first:**

1. **`PredictionEntry`** — the booked claim and its resolution. One record
   kind, one fold. *(§4)*
2. **The pool and the conformal factor** — residuals per `(tell, estimator)`,
   the scale model, `q_α` at the finite-sample rank. *(§3)*
3. **The fog allowance** added to the band, and the **calibrated horizon** as
   the second cap. *(§1a, §3.4)*
4. **The band-setting record** (G2 evidence) and the **fog trace** in the seam
   log. *(§4.2)*
5. **The fog overflow rule.** *(§1c)*

**Named but not built (the full mechanism):** multi-estimator attribution beyond
the one estimator, the trust horizon as a separate per-tell parameter, the
`self_reading` fallback source, the crossed-prediction (PROJECT ending) path
from §4.1, the coverage report as a standalone artifact rather than a field,
owner-set per-context `α`, and the reset-on-regime-break policy.

What v0.4-min does **not** do: it does not pretend the tell is calibrated
before it is. Below the finite-sample threshold, the fog runs on v0.1's prior
and says so in the seam log (`calibrated: false`). A fog that claims
calibration it does not have is the exact failure this whole note exists to
prevent.

---

## 7. Minimality — field-level changes to v0.3's shapes

Shapes, not code. Every field below is either one new optional field, one new
record kind, or one new sub-record on an existing type. Nothing in the
envelope's existing tuple moves.

### 7.1 `Reading` — two optional fields, and nothing age-dependent

```
  cell, value, taken_at, note
  trend, trend_from, trend_spacing
+ estimator:  str | None      # which estimator produced this vector
+ confidence: float | None    # the confidence the patch held in this reading
                              #   when it was taken (tell reliability, age 0)
```

- `to_dict`/`from_dict` gain the two keys; missing keys read as `None`, so a
  v0.3 reading still parses.
- **Nothing age-dependent is stored.** The live weight is derived at decision
  time and recorded in the decision (§2.3).
- **Considered and rejected:** storing the live weight on the reading and
  rewriting it as the reading ages. Rejected: an append-only log may not be
  rewritten (v0.3 §4.2), and a mutable field in an immutable record is worse
  than no field at all — it looks auditable and is not.

### 7.2 `Projection` — the width and the caps become visible

```
  value, trend, horizon, max_horizon, based_on, refused, reason, used
+ half_width: float | None          # the fog's uncertainty at this horizon
+ confidence: float | None          # the reading's effective weight at decision time
+ calibrated_max_horizon: float | None   # the calibrated cap, when the tell is calibrated
+ bound_by: str                     # "structural" | "calibrated" | "none" | "refused"
+ prediction_id: str | None         # the booked claim this projection became
```

- `max_horizon` keeps its meaning (the binding cap) and `bound_by` says which
  cap bound — so the reader sees whether the structural or the calibrated rule
  stopped the projection.
- `half_width` is `None` when the tell runs on the prior (no calibration): the
  fog is *absent*, not *zero*. A zero width would claim certainty the prior
  does not have.

### 7.3 New record kind: `PredictionEntry`

```
kind: "prediction"
prediction_id: str
cell: str
estimator: str
issued_at: float
read_at: float                    # the reading the claim was derived from (t_r)
claimed_at: float                # the instant the claim is about (τ)
from_reading: tuple[float, float] | None   # the trend basis
predicted: float
half_width: float | None         # claimed uncertainty; None on the prior (§3.4)
band_at_issue: float             # the effective band in force when issued
confidence: float | None
outcome: str | None              # "confirmed" | "refuted" | "unresolved"
resolved_at: float | None
source: str | None               # "agreed_arrival" | "self_reading"
outcome_transmission_id: str | None
residual: float | None
elapsed: float | None            # t_j - read_at: the extrapolation distance (§3.2)
covered: bool | None             # was the outcome inside the claimed half-width
```

- The issue record and the resolution record share a `prediction_id`; the fold
  reads the latest per id. Positions are booked, never edited (§4.3). The
  resolution carries the **full shape** — the same precedent as v0.3's
  `RevisionEntry`, where a position change is a second full record — so a
  resolved claim is self-describing without reaching back for its issue record.
- `outcome_transmission_id` names the **agreed crossing** that settled the
  claim — so the calibration's evidence is traceable to a crossing both books
  booked, which is the same standard `agreed_crossings` applies.
- `"unresolved"` is the **derived** state, not a third booked position: a
  prediction with no resolution record whose claimed instant has long passed.
  The fold reports it, which is how §3.5's unresolved fraction is counted — and
  it is exactly the count that makes the reliability number honest rather than
  survivorship-biased.
- `StateStore` grows a `predictions` list and a `book_prediction()`; `load()`
  reads the new `kind`. The JSONL file already carries a `kind` discriminator
  (`reading` / `ledger` / `revision` / `event`), so this is a **fifth** kind on
  the same file, not a new file — and `band_setting` (§7.4) a sixth.

### 7.4 New record kind: `BandSetting`

```
kind: "band_setting"
at: float
context: str
band: float
yaw_time: float
fog_allowance: float
effective_band: float
alpha: float
n_residuals: int
quantile: float
scale_model: dict              # {a, b, sigma_slope}
formula: str                   # the formula id, for G2
safety_corridor: float | None
overflow: float
acts_in_window: int | None     # the Act count in the window it replaced (§6.3)
```

- This is the **G2 graduation record**: SPEC §3 already requires that
  "the parameter values, context, excursion start and duration, and resulting
  outcome MUST be available to the seam log; when the parameter-setting
  decisions become formula-shaped, G2 graduation applies". The conformal band
  **is** formula-shaped, so this record is compliance with an existing SPEC
  clause, not a new requirement.
- `formula` names the formula so a future reader knows *which* graduated
  equation produced the number (G3's spot-check duty).

### 7.5 `Deadband` — one optional field

```
  band, yaw_time, context, safety_corridor
+ alpha: float | None      # the target miscoverage of the calibrated band
```

- `alpha` is **per context** (the band is context-set, per SPEC §3); the
  residual pool it is applied to is **per tell**. The two are different scopes
  and the note keeps them apart.
- The **effective** band is computed at decision time, not stored on the
  deadband — a deadband is a declaration, an effective band is a measurement.
- The `safety_corridor` check moves from the declared band to the **effective**
  band, because the effective band is what the receiver actually applies
  (§1c). The constructor check stays for the declared band (a declared band
  outside the corridor is a configuration error).

### 7.6 New sub-record: `FogTrace`, and one field on each decision

```
class FogTrace:
    allowed: float            # the fog allowance added
    band_effective: float
    confidence: float | None  # the reading's effective weight
    reading_age: float | None # how old the reading was at decision time
    overflow: float           # the corridor overflow, 0 when none
    prediction_id: str | None
    calibrated: bool          # measured, or running on the structural prior
```

```
  Hold / HoldWatch / Act
+ fog: FogTrace | None
```

- One new field on each of the three decision types, carrying the whole fog
  story, rather than five new loose fields repeated three times.
- `expected_course` and `projection` **stay as they are** — they are the
  inputs, and v0.1's tests read them directly. Together with `FogTrace` they
  carry the whole of §1's conceptual expectation: `point` →
  `expected_course`, `half_width` → `fog.allowed`, `confidence` →
  `fog.confidence`, `reading_age` → `fog.reading_age`, `projection` →
  `projection`.

### 7.7 `ReceiverJev` — one constructor field

```
+ fog: FogModel | None     # the calibration state it consults
```

- `FogModel` is the **per-tell calibration state**: the pool of resolved
  predictions for a `(tell, estimator)` pair, the fitted scale model, the
  conformal factor `q_α`, and the trust horizon — all *derived* by folds over
  the store's records. It is a **fold, not a record** (v0.3's rule): it is
  rebuilt from the log rather than stored, so a calibration can never drift
  away from the predictions it was computed from.

- The fog is consulted in `_expected_course_at` (which returns the expectation
  *with* its width), and the decision applies `band_effective = band +
  fog.allowed`, clamped by the corridor.
- The `pool_ttl` / `admissibility_floor` / `viability` knobs are untouched, and
  the calibration must not set them (§1, "what the fog must not touch").

### 7.8 `JevPair` and the seam log

- The crossing event gains `fog` (the `FogTrace`) and `prediction_id`.
- Five new seam events: `prediction`, `forecast`, `fog_disagreement`,
  `band_set`, `fog_overflow` (§4.2).
- `JevPair.cross` books the prediction when the decision consulted an adopted
  projection, and books the resolution when the agreed arrival about that cell
  lands.
- The sender/receiver disagreement record (§3.4) is **unchanged** — the fog
  disagreement is a different species of event and gets its own record.

### 7.9 `StateStore` — two lists, two bookers, two GC rules

- `predictions: list[PredictionEntry]` and `band_settings: list[BandSetting]`.
- `book_prediction()` / `book_prediction_resolution()` — the second appends,
  never edits.
- `load()` reads the two new `kind`s.
- **GC:** predictions are subject to the patch's declared reading-GC rate — the
  calibration fold needs only the window, so GC beyond it costs history, not
  calibration. `band_settings` are **never** GC'd here: they are the G2
  evidence.

### 7.10 What does not change

- The envelope's existing tuple — **no new fields**. A projection that crosses
  is a PROJECT-ended transmission and carries its claim in the payload.
- The ending set (T2), the typed rule (T1), the profiled rule (T3).
- The **comparative pick**, the decision types' *semantics*
  (`Hold`/`HoldWatch`/`Act(response)`), the policy's response set, and the
  `Abstain`-free ruling.
- The disagreement record shape (SPEC §3.4) and the `pool_ttl` /
  `admissibility_floor` / `viability` knobs.
- `agreed_crossings`'s return shape — predictions are not crossings.
- The JSONL form (new `kind`s on the same discriminator), the demo's transport,
  and the no-shared-memory invariant.
- v0.1's `Projection` fields and their semantics, and the structural cap's
  meaning.

---

## 8. What this design does not solve

1. **It does not make the fog correct.** The calibration measures the *recent*
   error of a specific estimator under a specific regime. A tell that was
   well-calibrated yesterday can be badly calibrated today, and the band is
   wrong until the residuals show it — which takes `n` residuals, i.e. *after*
   the failure. The fog is a lagging indicator, always.
2. **It cannot separate "the tell got noisier" from "the world changed."**
   Both look like residual growth, and the patch has no outside information
   that would distinguish them (§5.4 records the outlier; it cannot resolve it).
3. **It does not restore the conformal guarantee.** The guarantee is
   conditional on exchangeability, and drift, horizon-heteroscedasticity and
   self-observation all violate it in different degrees (§3.7). The coverage
   report *detects* the violation; it does not repair it.
4. **It gives no shared clock and no global order.** Scoring a prediction
   requires knowing which later reading answers which claim. That is done by
   **cell and order within one patch's own log** — never by comparing two
   patches' clocks. A prediction scored across patches would need a shared
   timeline the programme does not have (v0.3 §7.4).
5. **It does not calibrate the response.** `q_α` sets the *trigger*; the
   policy still chooses accept-best, clarify/narrow, escalate, or refuse, and
   the fog says nothing about which is right.
6. **It does not protect against a corrupted outcome.** If the agreed arrival
   is itself wrong, the calibration learns the wrong thing — confidently, and
   faster. Garbage in, better-calibrated garbage out.
7. **It does not eliminate the selection bias; it reports it.** The unresolved
   fraction (§3.5) makes the survivorship visible. It does not make the pool
   representative.
8. **It does not fix v0.1's unit conflation.** v0.1 measures the deviation
   between a *pick's merit* (a score) and a *course cell's value* (whatever the
   cell holds). The demo works because both are dimensionless in [0, 1]. A
   receiver whose course is in physical units and whose pool is scored in merit
   units needs a declared conversion between the two, and **v0.4 does not
   invent one** — the fog's half-width is in the course cell's units and must
   be commensurate with the receiver's declared deviation units.
9. **It does not reduce the fog.** It prices it. A calibrated fog is not a
   clearer fog; it is a fog whose width is known, and whose width is in the
   log.

### And the SPEC dependency

The standing discipline: **the SPEC is the citation; this repo follows it.**
v0.4 needs two amendments in beam-quilt before anything here is built, and one
of them is small.

**P4 — readings and predictions.** P4 currently says the active log holds
"its current readings of every cell from its own perspective, each reading
dated — readings age, and acting on a reading MUST account for its date".
Two changes, in one clause: the date must be accounted for *with the reading's
confidence*, and the log may hold dated predictions beside the readings.

> P4 — *...its current readings of every cell from its own perspective, each
> reading **dated** — readings age, and acting on a reading MUST account for
> its date **and its confidence**. Its active log MAY also hold **dated
> predictions**: claims about a cell's future value, each falsifiable, each
> scored by a later reading or an agreed arrival about that cell...*

**§3 — the deadband's calibration and the corridor.** SPEC §3 already names
conformal calibration as the method and already requires the band-setting
decision to be logged. v0.4 adds the fog's contribution to the band, and the
corridor's effect on the *effective* band:

> §3 — *The band is the maximum tolerated deviation... **The receiver's
> uncertainty about its own expected course MAY widen the effective band; that
> widening MUST itself be calibrated, MUST be logged with its evidence, and
> MUST NOT carry the effective band outside the applicable safety corridor.
> Where the fog's uncertainty would breach the corridor, the receiver is
> **blind rather than certain**, and the excursion trigger MUST fire on the
> fog's behalf.***

Neither amendment changes the deadband's semantics, the decision types, or the
comparative pick. Both make explicit what v0.1's binary account ("extend the
reading, or refuse it") was silently doing.

**What needs no amendment:** the agreement standard (P5 is unchanged; the
outcome must be an *agreed* crossing or nothing), the ending set (T2 already
has PROJECT for a crossed prediction), and the G2/G3 graduation duty (§3
already requires the band-setting decision to be logged with its evidence —
v0.4 is *complying* with it, not extending it).

---

## 9. Open questions for the author

These are genuine rulings, not design choices. Each one changes what gets
built, and each is the author's to make.

1. **May the fog allowance widen the effective band at all, or may it only ever
   narrow the decision?** If widening is permitted, then in thick fog the
   effective band can reach the safety corridor, and the overflow rule (§1c)
   forces an `Act` — the receiver escalates *because* it cannot see. If
   widening is forbidden, the receiver has exactly two behaviours in thick fog:
   coast blind (`Hold`), or act on everything. The first is unsafe and the
   second is expensive. *Consequence: whether the fog can compel an action, or
   only excuse one.*
2. **What is `α` — the target miscoverage of the calibrated band — and who
   sets it per context?** This single number sets how often the band admits a
   wrong arrival. It is the seam's false-alarm rate. Owner-set per context
   (C1-consistent, and the only place an owner can express risk appetite) or a
   patch default with an owner override? *Consequence: the false-alarm rate of
   the whole seam, and whether C1 reaches the band.*
3. **Where does a prediction that *crosses* the seam live — the sender's log,
   the receiver's log, or both?** A crossed prediction is a claim about another
   cell, and the sending side is the one making the claim. But the receiving
   side is the one that can score it. Both means double-counting the same
   residual into two pools; one side means choosing whose calibration it
   enters. *Consequence: whether the calibration is a per-perspective property
   (as this note assumes) or a shared one.*
4. **Must unresolved predictions be counted and reported, or may they be
   dropped?** §3.5 reports the unresolved fraction because a pool of only
   scored predictions is survivorship-biased. But counting them makes the
   reliability number honest and the band more conservative. *Consequence:
   whether the reported reliability is a measurement or a compliment.*
5. **What is the calibration window, and what happens to it on a regime
   break?** The count bound and the time bound are both proposed, with defaults
   `N = 50` and `W` context-set. The harder half: on a detected break, does the
   pool reset (making the receiver briefly blind at the moment it most needs to
   see), roll over into a second pool, or be kept and merely flagged?
   *Consequence: the receiver's behaviour in the first hours after a change —
   the period when it is most wrong.*
6. **Is the receiver's calibration state visible to the sender?** P4 makes the
   receiver's log its own; the seam log is per-patch. But if a sender could
   see "this receiver's fog is currently 0.3 wide and its last eight
   predictions missed", the sender could calibrate *itself* — spend less on
   proposals to a blind receiver, and know when its proposal will be judged
   against a stale expectation. That is real information crossing a seam, and
   it is not obviously the receiver's to give. *Consequence: whether the fog is
   a private property or a shared one, and whether the envelope can carry it.*
7. **May the calibrated horizon ever exceed the structural cap?** §3.4 keeps
   `3 × spacing` as a floor, so a tell that has demonstrated it is *more*
   accurate than the prior still may not project further. Loosening it would
   let a well-calibrated tell look further into the fog — but the structural
   cap is v0.1's protection against a wild slope from two noisy, close
   readings, and that protection was not conditional on accuracy. *Consequence:
   whether the structural cap is a law or a default.*

---

## Appendix A — a worked timeline

One patch, `R`, reads a tell `course`. Its calibration is empty. `α = 0.10`,
so the conformal quantile is finite only from `n ≥ 9` scored predictions.

### The uncalibrated phase — v0.1 behaviour, unchanged

| when | what happens | what is logged |
|---|---|---|
| t=0 | `R` reads `course = 0.30`. No trend (first reading). | reading |
| t=2 | `R` reads `course = 0.40`. Trend `+0.05/s`, spacing `S = 2.0`. Structural cap = `3 × 2.0 = 6.0`. | reading |
| t=6 | A crossing arrives carrying `merit = 0.60` and `course = 0.60`. | SEND / RECEIVE, agreed |
| t=6 | `R`'s expectation: the projection is adopted (`0.40 + 0.05×4 = 0.60`, reach 4.0 ≤ the structural cap 6.0). Band 0.15, **no fog allowance** (uncalibrated). Deviation 0. | `crossing` with `fog.calibrated = false` |
| t=6 | The decision **adopted** the projection and steered on it, so a claim is **booked**: read_at 2.0, claimed_at 6.0, predicted 0.60, half-width *none* (uncalibrated → no width claimed). | `prediction` |
| t=6 | The same arrival is the first agreed arrival at or after the claimed instant (`t_j ≥ 6`), so it **scores** that claim: residual `0.60 − 0.60 = 0.00`, elapsed `6 − 2 = 4.0`. **The fog is scored by the arrival it judged.** | `forecast` |
| t=6 | The arrival also produces a **reading-residual** against `R`'s last reading of `course` (0.40 vs 0.60 — residual **+0.20**). That is a *reading*-residual, so it joins the **reading pool**, not the projection pool. | reading-residual |
| t=6 | The projection pool now has one residual; `q_α` is **infinite** at `n = 1`, so the tell is still uncalibrated. The band is unchanged — one residual supports no finite band. | — |

**Note the discipline at t=6:** the residuals are recorded *before* anything is
recalibrated, and the reading-residual updates the reading pool — not the band.
A reading's error does not widen the fog; only a *projection*'s error does.

### The scoring of a projection

| when | what happens | what is logged |
|---|---|---|
| t=7 | `R` decides again. Its reading of `course` (t=2) is 5 s old; the projection to t=7 is adopted (`0.40 + 0.05×5 = 0.65`, reach 5.0 ≤ 6.0). The expectation steers the decision, so a second claim is booked — claimed instant `t=7`. | `prediction` — cell `course`, read_at 2.0, claimed_at 7.0, predicted 0.65, half-width *none*, band 0.15 |
| t=7 | The crossing's merit (0.60) is 0.05 from the expectation — inside the band → `Hold`. | `crossing` with `fog` trace |
| t=12 | An agreed arrival about `course` reports `course = 0.62`. It is the first arrival at or after the t=7 claim's claimed instant, so it **scores** that claim: residual `0.62 − 0.65 = −0.03`, elapsed `12 − 2 = 10.0`. Note the elapsed is charged from the *reading*, not from the claim — the five seconds the truth waited are charged to the fog, in the direction of a wider band. | `forecast` — residual, elapsed, covered *n/a* (no width was claimed) |
| t=12 | The residual joins the **projection pool** for (`course`, `last-pair-linear`). Pool size 2. `q_α` is still **infinite** — no finite band is supported by two residuals. | — |
| t=12 | The tell remains **uncalibrated**: the fog runs on the structural prior, the band is unchanged, and the log says so. | `crossing` with `fog.calibrated = false` |

### The calibrated phase

`R` keeps reading `course` every 2 s, so `S` stays 2.0 and the structural cap
stays 6.0.

| when | what happens | what is logged |
|---|---|---|
| t≈70 | The 9th projection about `course` has been scored. `n = 9 ≥ ⌈1/α⌉ − 1 = 9`, so `q_α` is finite. The scale model is fitted from the 9 residuals: `a = 0.02`, `b = 0.004/s`, `σ̂_slope = 0.01`. `q_α = 2.21`. | — |
| t≈73 | The fog allowance at the current age (the reading from t=70 is 3 s old) is `2.21 × s(3) = 2.21 × sqrt(0.0004 + 0.000144 + 0.000225) ≈ 2.21 × 0.0277 ≈ 0.061`. The effective band is `0.15 + 0.061 = 0.211`. The calibrated horizon — the `h` at which `2.21 × s(h) = 0.15` — works out to **h ≈ 10 s**, so the **structural** cap still binds (`3 × 2.0 = 6.0` < 10). | `band_set` — `n = 9`, `quantile = 2.21`, `scale_model = {a: 0.02, b: 0.004, sigma_slope: 0.01}`, `effective_band = 0.211`, `acts_in_window = 3` |
| t≈73 | The decision now runs against the *widened* band, and the seam log shows the weight that was used: `trust(3 s) = 1 − 3/T`. | `crossing` with `fog.allowed = 0.061`, `fog.confidence`, `fog.calibrated = true` |
| t≈76 | An arrival contradicts a live projection. The t=76 projection claimed `course = 0.70 ± 0.09`; the arrival reports `0.58`. | `fog_disagreement` — projection, arrival, residual `−0.12` |
| t≈76 | **The update order runs:** (1) the t=76 prediction is **scored** against its frozen width — residual `−0.12`, elapsed 4 s, `covered = false` (the outcome fell outside the claimed `±0.09`); (2) the residual joins the pool; (3) the scale model is refitted and `q_α` recomputed — the new allowance is **larger**, and a `band_set` record is written with the new evidence; (4) the horizon is **not** moved — the estimator did not change. | `forecast`, then `band_set` |
| t≈76 | The decision that used the t=76 projection was made under the **old** band and is **not** re-judged. v0.3's discipline: per-book folds that never re-fold history. | the old crossing stands |

### The overflow

| when | what happens | what is logged |
|---|---|---|
| later | The tell degrades; the fitted `b` rises; the allowance at the current age reaches 0.40, and the declared band is 0.15 with a safety corridor of 0.30. `0.15 + 0.40 = 0.55 > 0.30`. | `fog_overflow` — overflow 0.25, clamped effective band 0.30 |
| later | The receiver is **blind, not certain**. The band is clamped to the corridor; the widening is refused; the deviation test is replaced by the cannot-hold condition; the excursion trigger fires and the **policy** chooses — `Escalate` (call a larger model scoped to exactly this boundary) or `ClarifyNarrow`. Not `Hold`. | `crossing` with `fog.overflow = 0.25`, decision `act` |

---

## Appendix B — the one-paragraph version

v0.1 gave readings a vector and let the receiver steer against a projection of
it, but the projection produced a *point* and never an *uncertainty*, so a
receiver deep in the fog and a receiver with a fresh reading were treated as
equally certain — the fog could be wrong but never uncertain, and staleness
reached the decision only as a binary "extend it or refuse it". v0.4 makes the
fog output an expectation with a width: the width is a conformal quantile
(`q_α`) of the *normalized* residuals of the tell's own past predictions,
scaled by a fitted error model (baseline noise, drift sensitivity, and the
estimator's own extrapolation variance), pooled per `(tell, estimator)` over a
window bounded by both a count and a time. That width is added to the deadband
— so the fog absorbs its own ignorance instead of manufacturing false alarms —
and it yields a calibrated horizon as a second cap beside v0.1's structural
one. But the calibration has nothing to work with unless the claims are
written down, so the projection becomes a **booked prediction**: a dated,
append-only, falsifiable record in the patch's active log, issued before the
outcome exists and scored afterwards by the first *agreed* arrival about that
cell, with the residual measured against the width frozen at issue time. When an
arrival contradicts a live projection, the update order is fixed: score the
prediction first (updating first would re-score it against a model already
corrected by the outcome being scored), then update the error estimate, then
recompute the band, and only then consider the horizon — which moves only when
the *estimator* changes, because shrinking it on a single miss is a feedback
loop in the wrong direction. The fog is bounded by the safety corridor: where
its width would breach it, the receiver is **blind rather than certain**, the
widening is refused, and the excursion trigger fires on the fog's behalf so the
policy can escalate or narrow. The cost is honest and mostly not compute: two
fields per reading, ~830 B per decided claim, one `O(N)` log scan that should
be an index, and above all **acts** — calibration trades visible Act-triggering
against the invisible cost of suppressed real excursions, so every band change
is logged with the Act rate it replaced. And the smallest version that is still
the real mechanism is not "widen the band when stale": without the booked claim
there is no residual, without the residual no calibration, and without the
calibration the allowance is a guess. The chain is forced by the mechanism, not
by convenience.
