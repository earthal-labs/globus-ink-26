# Calibration bench: sync software to the steel ball

Open-loop metrology for the friction-drive Globus. Use this when tuning
scale (how fast the ball turns vs commanded ω), direction signs, and reverse
take-up — **not** while chasing closed-loop GOTO/AUTO feel.

Closed-loop mixes `GAIN_K`, overdrive, slew, and PAN. Measure the **plant**
first; set coefficients from stopwatch data; only then retune the controller.

Related: `docs/globus-logic.md` §§5.4, 6.3–6.5; knobs live in `tsup/config.py`.

---

## Three layers (don’t conflate)

| Layer | Question | Knobs |
|---|---|---|
| **Kinematic scale** | At commanded ω, how fast does the ball *actually* rotate? | Effective scale / overdrive (`RATE_OVERDRIVE_*`, `MANUAL_OVERDRIVE_CAP`) |
| **Direction map** | Does +ω spin the way we think? | `DIR[]` |
| **Reverse dynamics** | After a sign flip, how long until motion the other way? | `REVERSE_SETTLE_S`, `RATE_REVERSE_ACCEL_SPS2`, `RATE_ACCEL_SPS2` |

Full-revolution timing → **scale**. Separate reverse trials → **warmup**.

---

## Before you start

1. **DIR ritual** (`globus-logic.md` §6.5): command pure `+ω_z` — globe spins
   CCW from above, all wheels same way. Flip fighters in `DIR`. Then `+ω_x`
   and confirm wheel 0 leads. Encode in `config.py`.
2. **Marks on the ball**
   - (0°, 0°) under the chassis reticle (alignment / `q0`)
   - A **meridian** stripe (N–S feature)
   - An **equator hash** every 90° (easy full-turn timing)
3. **Stopwatch** (phone is fine) + fixed pointer on the chassis.
4. **Force 1× overdrive for scale trials** so ink and dead-reckoning agree:
   set `MANUAL_OVERDRIVE_CAP = 1.0` and/or temporarily hold
   `RATE_OVERDRIVE_SMALL = RATE_OVERDRIVE_LARGE = 1.0`. Restore AUTO adaptive
   values after you’ve written down \(k\).
5. Prefer **open-loop constant ω → `V` rates** (kinematics only). Do **not**
   use vzor GOTO/PAN or AUTO tracking for scale measurement.

The dedicated script is `tsup/calibrate_spin.py` — it drives each axis at
constant kinematic ω (1×, bypassing overdrive/slew/controller entirely),
times laps and reversals against your Enter key, appends
`tsup/data/calibration.csv`, and prints median \(k\) per axis plus
suggested config values:

```bash
cd ~/globus/tsup
uv run python calibrate_spin.py                 # Protocol A, all axes ± @ 0.05
uv run python calibrate_spin.py --reverse       # Protocol B on the same axes
uv run python calibrate_spin.py --axes Z --omega 0.03 --trials 3
```

---

## Axis catalog (N/S, E/W, NE/SW)

Command a **unit direction × |ω|**. Start with `|ω| = 0.05 rad/s`
(~2.9 °/s → **ideal full rev ≈ 126 s**). Slow enough to time by eye; fast
enough to escape pure stiction.

| Trial name | ω direction (body) | What to watch |
|---|---|---|
| **Spin / E–W (longitude family)** | `(0, 0, ±1)` | Equator hash walks past pointer (pure yaw about vertical) |
| **Tilt N–S** | `(±1, 0, 0)` | Meridian rolls N↔S under reticle |
| **Tilt (orthogonal)** | `(0, ±1, 0)` | Other pure tilt — confirm vs chassis axes |
| **NE / SW** | `(±1, ±1, 0) / √2` | Combined tilt — catches multi-wheel slip |
| **Optional 3D mix** | `(1, 1, 1) / √3` | Uneven three-wheel load |

Always run **both signs** for each axis. +/− scale disagreement is often
reverse take-up eating the start of the timed lap.

---

## Protocol A — scale (full revolution)

For each row in the catalog (both signs):

1. Align so the start mark sits under the pointer.
2. Command constant ω for that axis (open loop, 1× scale).
3. Start the watch when motion is clearly underway **or** when the mark
   leaves the pointer (pick one rule and stick to it).
4. Stop when the same mark completes **one full** return.
5. Record: axis, sign, `|ω|_cmd`, measured `T` (s), peak wheel rate if known,
   notes (cold start, slip, chatter).
6. Repeat **3×**; use the **median** `T`.

### Translate to a scale factor

\[
\omega_{\text{actual}} = \frac{2\pi}{T}
\qquad
k = \frac{\omega_{\text{commanded}}}{\omega_{\text{actual}}}
\]

| Result | Meaning | Action |
|---|---|---|
| \(k > 1\) | Ball **slower** than model | Raise ink scale (overdrive / kinematic scale) |
| \(k < 1\) | Ball **faster** than model | Lower scale, or you slipped / mistimed |
| Axes disagree a lot | Geometry / contact / slip | Re-check `α`, `R`, `r`, pressure — not just gain |
| NE \(k\) ≈ mean of X,Y | Healthy coupling | Good |
| NE \(k\) far from mean | Slipping under load | Contact pressure / overdrive under multi-wheel |

**Worked example:** `|ω|_cmd = 0.05`, `T = 180 s` →  
\(\omega_{\text{actual}} = 2π/180 ≈ 0.035\) → \(k ≈ 1.43\) → run ink ~**1.43×**
in that regime (or bake a global `KINEMATIC_SCALE` if you add one).

Write the medians into a table (or `calibration.csv`):

```text
axis,sign,omega_cmd,T_rev_s,k,notes
Z,+,0.05,126.0,1.00,
Z,-,0.05,131.0,1.04,slight lag at start
X,+,0.05,180.0,1.43,cold
...
```

Suggested bake-in:

- Global \(k\) at slow rates → `MANUAL_OVERDRIVE_CAP` / `RATE_OVERDRIVE_SMALL`
- \(k\) grows under load → keep adaptive `RATE_OVERDRIVE_LARGE` + `RATE_SLEW_REF`
- Only after open-loop \(k\) is honest, retune `GAIN_K` / `OMEGA_MAX` / vzor PAN

---

## Protocol B — reverse / warmup (separate)

Do **not** extract settle time from full-spin `T`.

1. Spin steadily one way ≥ 5 s at a known cruise.
2. Instantly command equal magnitude **opposite**.
3. Time command flip → **first visible motion** the new way → `T_dead`.
4. Time first motion → **stable cruise** → `T_ramp`.
5. Repeat at a few rate levels (e.g. peak ~50 / 150 / 400 steps/s).
6. 3× each; median.

### Translate to config

| Measured | Config |
|---|---|
| `T_dead` | `REVERSE_SETTLE_S` (≈ that, or slightly less if soft-accel also runs) |
| `T_ramp` to cruise_sps | `RATE_REVERSE_ACCEL_SPS2 ≈ cruise_sps / T_ramp` |
| Same-sign snappiness | `RATE_ACCEL_SPS2` |
| Vzor arrow feel (after plant is known) | `PAN_DEGREES_PER_SECOND`, `PAN_ACCEL_DPS2` |

If +/− full-spin \(k\) still disagree after settle is set: start the revolution
stopwatch only after motion is visible, or time mark-to-mark with a rolling
start (no zero-crossing in the middle of the lap).

---

## One-evening matrix

```text
for axis in [+Z, -Z, +X, -X, +Y, -Y, +(X+Y)/√2, -(X+Y)/√2]:
    3× Protocol A at |ω| = 0.05
    (optional) 3× Protocol A at |ω| = 0.03

for reverse in [+Z→-Z, +X→-X]:
    for cruise in [low, mid]:
        3× Protocol B
```

Then:

1. Compute median \(k\) per axis → set overdrive / scale.
2. Set `REVERSE_SETTLE_S` / reverse accel from Protocol B.
3. Re-run **one** full-spin axis as a confirmation lap.
4. Only then poke MANUAL PAN / AUTO tracking.

---

## Operational notes

- **Cold vs warm:** first reverse after sitting idle is worst; note it.
- **Slip:** if the wheels scrub and the mark stalls, `T` is meaningless —
  reduce `|ω|` or load / increase contact pressure, don’t raise overdrive yet.
- **STATE / DR:** with overdrive ≠ 1×, software `q` drifts from the steel ball.
  Scale trials at 1×; live AUTO may still use adaptive boost afterward, but
  know that high AUTO overdrive **desyncs** dead reckoning unless you later
  model slip explicitly.
- **Alignment:** after big open-loop spins, re-run `--realign` (or the
  alignment ritual) before trusting AUTO again.

---

## Checklist for tomorrow

- [ ] DIR signs still correct (`+ω_z` / `+ω_x`)
- [ ] Marks + pointer visible
- [ ] Overdrive forced to **1×** for Protocol A
- [ ] Log sheet / CSV ready
- [ ] Protocol A all axes + both signs @ 0.05 rad/s
- [ ] Protocol B on Z and X (two rate levels)
- [ ] Write medians → `config.py` knobs
- [ ] Confirm lap + restore AUTO overdrive if desired
- [ ] Re-align globe / `q0` before tracking

`tsup/calibrate_spin.py` implements exactly this: per-axis constant-ω
drive, expected-`T` printout, Enter-prompt timing for both protocols, and
`calibration.csv` output with suggested \(k\) / settle values.
