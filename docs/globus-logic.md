# Globus: The Logic, From Physics to Pseudocode

This is the theory manual for the tracking system. It explains *why* each piece
exists, derives the math you will implement, and ends with pseudocode for the
three modules you will write: `kinematics.py`, `main.py` (tsup), and
`ink.ino` (firmware). Nothing here is code you can paste; everything here is
reasoning you can code against.

The chain we are building:

```
time ─▶ orbit model ─▶ subpoint (lat, lon) ─▶ target direction ─▶ error
rotation ─▶ angular velocity ω ─▶ wheel speeds ─▶ step rates ─▶ coil pulses
```

Every arrow is a small, testable transformation. The rest of this document
walks the chain left to right.

---

## 1. The physics of a sphere on three wheels

### 1.1 Rolling contact

A rigid sphere rotating with angular velocity vector **ω** (radians/second,
pointing along the rotation axis, right-hand rule) has a surface velocity at
any point **p** on its surface (measured from the sphere's center) of:

```
v_surface(p) = ω × p
```

This one cross product is the entire physics of the drive. A wheel touching
the sphere at point **p_i** grips the surface there. If the wheel's rim moves
at exactly `v_surface(p_i)`, it rolls without slipping and the sphere doesn't
care the wheel exists. If the wheel moves differently, friction forces the
sphere to change its motion. Drive three points, and you dictate **ω**
completely.

### 1.2 Why omniwheels, precisely

An ordinary wheel constrains the surface in *two* directions: along its
rolling direction (driven) and along its axle direction (blocked by friction).
Three ordinary wheels would over-constrain the sphere — six constraints on
three degrees of freedom — and the system would bind, scrub, or stall.

An omniwheel's rim is made of free-spinning rollers whose axes are
perpendicular to the wheel's rolling direction. It therefore constrains the
surface in only *one* direction — its drive direction **d_i** — and is
transparent in the perpendicular direction. Three omniwheels give exactly
three constraints for three degrees of freedom. That is the entire trick,
and it is why the math below comes out as a clean, invertible 3×3 system.

### 1.3 The traction budget

Friction drive works only if the required tangential force stays below the
friction limit `μ N` at each contact. Your 3 lb steel sphere presses ~4.5 N
onto each of three contacts (a bit more on geometry, but same order). Rubber
on matte paint gives μ of roughly 0.6–0.8, so each wheel can transmit on the
order of 3 N before slipping. The torque needed to angularly accelerate a
hollow steel sphere at your speeds is milli-newton-scale. The budget is
enormous — which is why dead reckoning (Section 5) is trustworthy. The one
thing that destroys the budget is a glossy surface, hence matte paint and
matte clearcoat.

---

## 2. Representing orientation: why quaternions

### 2.1 The problem with latitude/longitude thinking

The instinct is to track the globe as "which lat/lon faces up" plus maybe a
twist angle. This is three angles — an Euler-angle representation — and all
Euler representations share a disease: somewhere in their range, two of the
angles collapse into the same physical rotation and the math divides by zero.
That is gimbal lock, in software form. Your globe will routinely carry poles
through the zenith (any high-inclination satellite does this), which is
exactly where lat/lon-style bookkeeping degenerates.

### 2.2 The quaternion, minimally

A unit quaternion `q = (w, x, y, z)` with `w² + x² + y² + z² = 1` encodes a
rotation by angle θ about unit axis **a**:

```
q = ( cos(θ/2),  a_x·sin(θ/2),  a_y·sin(θ/2),  a_z·sin(θ/2) )
```

You need exactly five operations, all short enough to write yourself:

1. **multiply(q1, q2)** — compose rotations (apply q2 first, then q1)
2. **conjugate(q)** = `(w, −x, −y, −z)` — the inverse rotation (for unit q)
3. **rotate(q, v)** — apply to a vector: `v' = q ⊗ (0,v) ⊗ q*`
   (or expand to the direct 9-multiplication formula; both are fine)
4. **from_axis_angle(a, θ)** — the boxed formula above
5. **normalize(q)** — divide by its magnitude; call it after every integration
   step, because floating-point drift slowly denormalizes q and a
   non-unit quaternion silently scales your vectors

No library needed. Writing these five functions (~40 lines) and unit-testing
them against known cases (90° about Z should send x̂ to ŷ, etc.) is the single
best afternoon investment in this project: every bug you prevent here would
otherwise appear as "the globe drifts weirdly" three modules later.

### 2.3 Frames and conventions (pin these to the wall)

**World frame** (fixed to the room): ẑ = straight up. x̂ = toward the back of
the unit, ŷ = follows right-handed convention. Wheel 1 (ψ=0°) sits on the x̂
side.

**Body frame** (fixed to the globe, i.e. to Earth geography): a point at
latitude φ, longitude λ is the unit vector

```
p_body(φ, λ) = ( cos φ · cos λ,  cos φ · sin λ,  sin φ )
```

so body-x̂ pierces (0°, 0°) in the Gulf of Guinea, body-ẑ pierces the North
Pole.

**The state** is one quaternion `q`, defined as the rotation taking body
vectors to world vectors:

```
p_world = rotate(q, p_body)
```

**Angular velocity ω is expressed in the world frame** throughout. This
choice determines the integration formula in Section 5 — mixing frames is the
most common quaternion bug, so write the convention as a comment at the top
of `kinematics.py`.

### 2.4 The initial condition

"(0°, 0°) faces straight up" means body-x̂ must map to world-ẑ. The rotation
that does this is −90° about the world Y axis:

```
q0 = from_axis_angle( (0,1,0), −90° ) = ( √2/2,  0,  −√2/2,  0 )
```

Sanity checks you should code as a test: `rotate(q0, (1,0,0))` must return
(0,0,1) — Gulf of Guinea at zenith — and `rotate(q0, (0,0,1))` must return
(−1,0,0) — the North Pole lying sideways, pointing at −x̂. The pole *must* be
on the horizon in this pose; if your intuition rebels, hold a real globe with
Africa up and look where the pole went.

---

## 3. The target: a direction, not an orientation

Each control tick, the orbit model hands you the satellite's subpoint
(φ, λ). Convert it to a body vector `p_b` with the formula in 2.3. The
display requirement is only:

```
rotate(q, p_b) = ẑ        "the subpoint faces up"
```

Notice this constrains two degrees of freedom, not three — the globe's spin
*about* the vertical axis is unconstrained. Do not fight this freedom;
exploit it. Instead of constructing a full target quaternion (which would
force an arbitrary choice of that spin), compute the **shortest arc** that
carries the subpoint's *current* world position to the zenith:

```
u = rotate(q, p_b)                 where is the subpoint now?
axis  a = (u × ẑ) / |u × ẑ|        the rotation axis
angle θ = atan2( |u × ẑ| ,  u·ẑ )  how far off vertical
```

Use `atan2(|cross|, dot)` rather than `acos(dot)` — identical answer,
numerically well-behaved near θ = 0 where acos loses precision.

Two edge cases to handle explicitly:

- **θ ≈ 0** (already aligned): the axis is 0/0. Set ω = 0 and skip.
- **θ ≈ 180°** (subpoint at nadir): every axis works equally; the cross
  product is zero. Pick any horizontal axis, e.g. x̂. This occurs only after
  a state reset, never during tracking.

A pleasant property falls out for free: the shortest arc from u to ẑ never
contains a component about ẑ itself (a spin about vertical doesn't move u
toward the zenith, so the shortest path never includes one). The globe
therefore never twirls pointlessly — a behavior you'd otherwise have to
engineer.

---

## 4. The controller: proportional, in rotation space

Given axis **a** and angle θ, the commanded angular velocity is:

```
ω = clamp( k · θ , ω_max ) · a
```

That's the whole controller. Reasoning:

- **Why proportional?** The error θ shrinks exponentially with time constant
  1/k — fast when far, gentle when close, no overshoot at these speeds
  (steppers have no momentum dynamics worth modeling; the plant is nearly
  ideal velocity control, and P-control of an integrator is unconditionally
  stable).
- **Gain k** (units 1/s): start at 0.5. Larger = snappier retargets.
- **ω_max**: protects the motors from step-rate demands they can't meet.
  With a cap of ~450 steps/s at the wheels (28BYJ-48s are happy there),
  ω_max works out to about 0.26 rad/s at the globe — a worst-case 180°
  retarget takes ~12 s of slew plus a graceful exponential landing.
- **Tracking mode**, once aligned, is this same law: θ stays a fraction of a
  degree and ω settles to almost exactly the ground-track rate. There is no
  separate "tracking controller" — the P-law does both jobs.

The deadband question: below some θ (say 0.05°), set ω = 0 so the motors
rest and coils can de-energize. This trades a hair of accuracy for silence,
zero idle heat, and battery-friendliness. The subpoint moves ~0.07°/s at ISS
rates, so a 0.05° deadband wakes the motors about once a second — consider a
slightly asymmetric scheme (wake at 0.1°, sleep below 0.02°) for calmer
behavior.

---

## 5. Dead reckoning: the globe's position lives in software

There is no orientation sensor (until the phase-2 optical mice). The state q
is maintained by integrating what you *command* — legitimate because stepper
+ huge traction margin + non-backdrivable gearbox means commanded steps ≈
physical steps to within one step.

### 5.1 The integration formula

Over a tick of duration Δt with world-frame angular velocity ω:

```
δq = from_axis_angle( ω/|ω| ,  |ω|·Δt )
q  = normalize( multiply(δq, q) )        ← δq on the LEFT
```

Left-multiplication is forced by our conventions: ω is a world-frame
quantity, and world-frame increments compose on the world side of q. (If you
ever see the globe's error grow *faster* under control instead of shrinking,
you have multiplied on the wrong side — it is the classic symptom.)

If |ω| = 0, skip the update.

### 5.2 The quantization subtlety (this is the one that bites)

You will convert ω to wheel speeds and then to **integer** step rates for
ink. The motors execute the quantized rates, not your ideal ω. If you
integrate the *ideal* ω while the motors run the *quantized* version, q
drifts away from the physical globe — slowly, invisibly, permanently.

The fix uses the fact that the kinematics matrix (Section 6) is invertible:
after quantizing, map the step rates *back* to the ω they actually represent,
and integrate that:

```
v_ideal   = M · ω                      wheel rim speeds
rates     = quantize(v_ideal)          what ink will actually do
v_actual  = un-quantize(rates)
ω_actual  = M⁻¹ · v_actual
integrate ω_actual                     ← not ω
```

Now software q and physical globe agree to within one motor step, forever.

### 5.3 Persistence

Write q to `state.json` every ~60 s and on clean shutdown — atomically: write
to `state.json.tmp`, then `os.replace()`. The steel sphere cannot move while
unpowered (gearboxes don't backdrive), so a reloaded q is trustworthy. Treat
a missing, empty, or corrupt state file as "unknown orientation": ask the
human to align the globe to the reference pose and press go.

### 5.4 The reference pose and the alignment ritual

The (0,0)-up pose q0 is your zero. First boot, and any recovery: human
rotates the globe by hand until the marked (0°,0°) point sits under a fixed
pointer/reticle on the chassis with the pole toward −x̂, then confirms. The
software sets q = q0 and trusts dead reckoning from there. Put a small
painted crosshair at (0°,0°) when you paint the sphere — future you will be
grateful.

---

## 6. Kinematics: from ω to three wheel speeds

### 6.1 Geometry

Wheels contact the sphere on a ring at angle α below the equator (α = 40°),
at azimuths ψ = 0°, 120°, 240°. Sphere radius R = 76.2 mm (6" sphere), wheel
radius r = 29 mm (58 mm Nexus). Contact points, from sphere center:

```
p_i = R · ( cos α · cos ψ_i ,  cos α · sin ψ_i ,  −sin α )
```

Each wheel is mounted to drive *azimuthally* — its drive direction is the
horizontal tangent to the contact ring:

```
d_i = ( −sin ψ_i ,  cos ψ_i ,  0 )
```

### 6.2 Derivation (three lines)

The wheel must match the sphere's surface velocity along its drive direction:

```
v_i = (ω × p_i) · d_i
    = ω · (p_i × d_i)            (scalar triple product identity)
```

Compute the constant vector `p_i × d_i` for the geometry above (do this by
hand once — it's a satisfying exercise) and you get:

```
p_i × d_i = R · ( sin α · cos ψ_i ,  sin α · sin ψ_i ,  cos α )
```

Therefore each wheel's rim speed is a fixed linear function of ω:

```
v_i = R · [ sin α · (ω_x cos ψ_i + ω_y sin ψ_i)  +  cos α · ω_z ]
```

### 6.3 Matrix form

Stack the three rows into the constant 3×3 matrix M so that `v = M ω`:

```
        ⎡ sinα·cosψ₁   sinα·sinψ₁   cosα ⎤
M = R · ⎢ sinα·cosψ₂   sinα·sinψ₂   cosα ⎥
        ⎣ sinα·cosψ₃   sinα·sinψ₃   cosα ⎦
```

M is invertible whenever sin α ≠ 0 and cos α ≠ 0 — i.e., wheels neither on
the equator nor at the pole — which is the mathematical statement of the
design rule "contact ring between ~30° and ~50°." Compute M and M⁻¹ once at
startup; they never change.

Sanity identities to unit-test your implementation against:

- ω = (0, 0, w): all three v_i equal `R·cosα·w` — pure spin, wheels in unison
- ω = (w, 0, 0): v ∝ (cos 0°, cos 120°, cos 240°) = (1, −½, −½) — wheel 1
  forward, wheels 2 and 3 backward at half speed

### 6.4 Wheel speed to step rate

Wheel angular speed Ω_i = v_i / r. The 28BYJ-48's true reduction is 63.684:1
(not 64:1), giving **2037.89 full steps per output revolution** — not the
folklore 2048. The 0.5 % discrepancy is invisible in projects that move and
stop; yours integrates forever, and at 2048 the dead-reckoned q would drift
~1° per 200° of globe travel. So:

```
STEPS_PER_RAD = 2037.89 / 2π ≈ 324.3
rate_i (steps/s) = Ω_i · STEPS_PER_RAD        signed; sign = direction
```

(Full-step drive — two coils always energized — not half-step: the
single-coil phases of half-stepping have ~30% less torque, and under the
globe's real load those weak phases stall. If the drive mode in `ink.ino`
ever changes, this constant changes with it.)

Worked example so you know what "normal" looks like: the ISS ground track
moves at |ω| ≈ 1.2 mrad/s. Wheel rim speeds ≈ R·1.2 mrad/s → Ω ≈ 3.2 mrad/s
→ **≈ 2 steps/second per motor** while tracking. Slews run up to the ~450
steps/s cap. Both are trivially within the motor's ability; the design
margin lives everywhere.

### 6.5 Calibration day-one ritual

Sign conventions never survive first contact with wiring. On first light,
command pure +ω_z: the globe must spin counterclockwise seen from above,
all wheels running the same direction. Any wheel fighting the others gets
its direction flag flipped in config (three booleans). Then command +ω_x and
confirm wheel 1 leads. Five minutes, done once, and encode the flags in
`config.py`, not in your head.

---

## 7. Time and the orbit model

Skyfield (wrapping SGP4) turns a TLE — the standard two-line orbital element
set, fetched from Celestrak — into a position at any instant. Your loop asks
one question per tick: *where is the subpoint right now?*

```
t         = now (UTC)                    ← RTC makes this correct pre-network
satellite = EarthSatellite(TLE lines)
geo       = satellite.at(t).subpoint()   → latitude, longitude
```

Reasoning about rates and freshness:

- **Tick rate: 10 Hz.** The subpoint moves ~0.07°/s; at 10 Hz each tick sees
  ~0.007° of motion — far finer than a motor step's worth of globe surface.
  Faster buys nothing; slower (even 1 Hz) would honestly still work. 10 Hz is
  chosen so the *controller* feels continuous, not because the orbit demands
  it.
- **TLE freshness:** TLEs age; ISS elements are good to a few km of subpoint
  error for several days. Refresh from Celestrak daily-ish, cache the last
  good set in `data/`, and never let a network failure stop tracking — stale
  TLEs degrade gracefully, no TLE at all is the only fatal case.
- **All internal time is UTC.** Local timezone exists only for humans and
  logs.

---

## 8. The division of labor, restated as an interface

tsup thinks; ink pulses. The interface between them is three signed integers
at 115200 baud (protocol v0):

```
tsup → ink : "V s1 s2 s3\n"      step rates, steps/s, signed
ink → tsup : "ink p0\n"          hello + protocol version, on boot
ink → tsup : (fault lines, later)
```

Two firmware behaviors that are policies, not conveniences:

- **Watchdog:** if no `V` command arrives for ~500 ms, ink sets all rates to
  zero. A crashed tsup must never leave motors spinning open-loop.
- **Coil release:** after ~2 s at zero rate on a motor, de-energize its
  coils. The gearbox holds position physically; holding current buys nothing
  but heat. Re-energize on the next nonzero command.

---

## 9. Pseudocode

### 9.1 `kinematics.py` — pure math, no I/O, unit-test everything

```
constants:
    R = 0.0762          # sphere radius, m
    r = 0.029           # wheel radius, m
    alpha = 40°
    psi = [0°, 120°, 240°]
    STEPS_PER_RAD = 2037.89 / (2π)   # full-step count/rev - matches ink.ino's drive mode
    DIR = [+1, +1, +1]  # per-wheel sign flips, set on calibration day

build M:                # 3×3, constant
    row i = R * [ sinα·cosψᵢ ,  sinα·sinψᵢ ,  cosα ]
precompute M_inv

function wheel_rates(ω) -> (int s1, s2, s3):
    v = M · ω                          # rim speeds, m/s
    Ω = v / r                          # wheel rad/s
    rates = round(Ω * STEPS_PER_RAD)   # integer steps/s
    return rates * DIR

function actual_omega(rates) -> ω:     # undo quantization, Section 5.2
    Ω = (rates * DIR) / STEPS_PER_RAD
    v = Ω * r
    return M_inv · v

quaternion helpers (Section 2.2):
    multiply, conjugate, rotate, from_axis_angle, normalize

function latlon_to_body(φ, λ) -> unit vector      # Section 2.3

function shortest_arc(u, ẑ) -> (axis, θ):         # Section 3
    c = cross(u, ẑ);  handle |c| ≈ 0 edge cases
    return c/|c|, atan2(|c|, dot(u, ẑ))
```

### 9.2 `main.py` — the tsup loop

```
startup:
    q = load_state() or align_ritual_returning(q0)
    open serial /dev/ttyACM0 @ 115200, wait 2 s      # port-open resets ink
    hello = readline(); assert protocol version matches or REFUSE
    sat = load_cached_TLE(); spawn background TLE refresher

loop at 10 Hz:
    Δt   = actual elapsed since last tick            # measure, don't assume
    φ, λ = subpoint(sat, now_utc())
    p_b  = latlon_to_body(φ, λ)
    u    = rotate(q, p_b)
    axis, θ = shortest_arc(u, ẑ)

    if θ < deadband:  ω = 0
    else:             ω = min(k·θ, ω_max) · axis

    rates = wheel_rates(ω)
    send "V {rates}\n"

    ω_act = actual_omega(rates)                      # the quantized truth
    if |ω_act| > 0:
        δq = from_axis_angle(ω_act/|ω_act|, |ω_act|·Δt)
        q  = normalize(multiply(δq, q))              # δq on the LEFT

    every ~60 s: atomic_save(q)

on exit / signal:  send "V 0 0 0", atomic_save(q)
```

This is the AUTO-mode core; the manual/vzor extension (a second target source
feeding the same `shortest_arc`/controller pipeline, plus the bridge socket
that drives it) is documented separately in `docs/bridge-protocol.md` rather
than folded into this pseudocode.

### 9.3 `ink.ino` — the firmware

```
constants:
    FULLSTEP[4] = { 1100, 0110, 0011, 1001 }          # coil patterns,
                                                      # IN1..IN4 per motor -
                                                      # two coils always on
                                                      # (max torque; see 6.4)
    pins[3][4] = { ... }                              # 12 GPIO assignments

state per motor: rate (steps/s, signed), phase (0..3),
                 next_step_time (µs), idle_since

setup:
    pins to OUTPUT, Serial.begin(115200)
    print "ink p0"

loop:                                                 # no delay() anywhere
    if serial line available:
        parse "V s1 s2 s3"; on success update rates, last_cmd_time
        (on parse failure: ignore line, optionally report fault)

    if now − last_cmd_time > 500 ms:                  # watchdog
        all rates = 0

    for each motor m:
        if rate[m] == 0:
            if idle > 2 s: write coils 0000            # release
            continue
        interval = 1e6 / |rate[m]|                     # µs per step
        if now ≥ next_step_time[m]:
            phase[m] += sign(rate[m])  (mod 4)
            write FULLSTEP[phase[m]] to pins[m]
            next_step_time[m] += interval              # += not =, no drift
```

The `+=` in the last line matters: setting `next = now + interval` accumulates
scheduling jitter into position error; `next += interval` makes the long-run
average rate exact — the firmware-level cousin of Section 5.2.

---

## 10. The order to build and test it

1. Quaternion helpers + unit tests (known rotations in, known vectors out)
2. `latlon_to_body`, `shortest_arc` + tests (edge cases: θ=0, θ=π)
3. M, `wheel_rates`, `actual_omega` + the two sanity identities from 6.3
4. Skyfield console test: print live ISS subpoint (no hardware)
5. ink: hello + `V` parser + one motor stepping on the bench
6. Three motors + calibration ritual (signs) — first globe motion
7. Full loop against q0, watch it track; add persistence; then deadband tuning

Each stage is verifiable without the stages after it. If something looks
wrong at stage 7, the bug is almost never in stage 7 — it is a convention
violated in stage 1–3, which is why the unit tests exist.
