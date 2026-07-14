"""
Globus Kinematics.

Converts angular velocity to wheel step rates (and back),
plus the quaternion helpers used to track the globe's orientation.
See docs/globus-logic.md for the derivations.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

from math import cos, sin, radians, atan2, pi

from numpy import array, cross
from numpy.linalg import inv, norm

from config import (
    R, r, α, ψ, STEPS_PER_RAD, DIR,
    RATE_OVERDRIVE_SMALL, RATE_OVERDRIVE_LARGE, RATE_SLEW_REF, RATE_CAP,
)

DIR = array(DIR)

rad_α = radians(α)
sin_α = sin(rad_α)
cos_α = cos(rad_α)

M = array([
    R * array([sin_α * cos(radians(ψ_i)), sin_α * sin(radians(ψ_i)), cos_α])
    for ψ_i in ψ
])

M_inv = inv(M)

def wheel_rates(ω):
    v = M @ ω
    Ω = v / r

    return (Ω * STEPS_PER_RAD * DIR).round().astype(int)

def actual_omega(rates):
    Ω = array(rates) / (STEPS_PER_RAD * DIR)
    v = Ω * r

    return M_inv @ v

def overdrive_scale(rates):
    """Adaptive ink scale from peak kinematic |rate| (bench-tuned on globe).

    Tiny rates → RATE_OVERDRIVE_SMALL (1×); large slews → RATE_OVERDRIVE_LARGE
    (10×). Linear in between up to RATE_SLEW_REF.
    """
    peak = max((abs(int(x)) for x in rates), default=0)
    if peak <= 0:
        return RATE_OVERDRIVE_SMALL
    t = min(peak / float(RATE_SLEW_REF), 1.0)
    return RATE_OVERDRIVE_SMALL + (RATE_OVERDRIVE_LARGE - RATE_OVERDRIVE_SMALL) * t


def overdrive_rates(rates, scale=None, cap=None):
    """Scale kinematic rates for friction-drive stiction; clamp to RATE_CAP.

    Dead reckoning must keep using the unscaled `rates` — only the serial
    command to ink is overdriven. Scale is adaptive unless overridden.
    """
    if scale is None:
        scale = overdrive_scale(rates)
    if cap is None:
        cap = RATE_CAP
    scaled = (array(rates, dtype=float) * scale).round().astype(int)
    return scaled.clip(-cap, cap)
# Quaternion helpers (globus-logic.md sec. 2.2): q = (w, x, y, z), w scalar.

def multiply(q1, q2):
    # Hamilton product; composes rotations, apply q2 first then q1.
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2

    return array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])

def conjugate(q):
    w, x, y, z = q

    return array([w, -x, -y, -z])

def rotate(q, v):
    # v' = q (x) (0,v) (x) q*; drop the (~0) scalar part of the result.
    v_pure = array([0, v[0], v[1], v[2]])

    return multiply(multiply(q, v_pure), conjugate(q))[1:]

def from_axis_angle(axis, θ):
    axis = normalize(axis)
    half_θ = radians(θ) / 2
    sin_half_θ = sin(half_θ)

    return array([cos(half_θ), axis[0] * sin_half_θ, axis[1] * sin_half_θ, axis[2] * sin_half_θ])

def normalize(vector):
    magnitude = norm(vector)

    return vector if magnitude == 0 else vector / magnitude

def latlon_to_body(φ, λ):
    rad_φ = radians(φ)
    rad_λ = radians(λ)

    x = cos(rad_φ) * cos(rad_λ)
    y = cos(rad_φ) * sin(rad_λ)
    z = sin(rad_φ)

    return array([x, y, z])

# Numerical-zero threshold for the axis/angle degeneracies below - not the
# tracking deadband (that's tracker.py's controller, sec. 4).
ANGLE_EPSILON = 1e-9

def shortest_arc(u, ẑ):
    u = normalize(u)
    ẑ = normalize(ẑ)

    cross_vec = cross(u, ẑ)
    cross_mag = norm(cross_vec)
    θ = atan2(cross_mag, u @ ẑ)          # not acos: loses precision near θ=0

    if θ < ANGLE_EPSILON:
        return array([1, 0, 0]), 0.0     # aligned; axis is 0/0, unused
    if θ > pi - ANGLE_EPSILON:
        return array([1, 0, 0]), θ       # nadir; cross~0 but θ isn't - pick x̂

    return cross_vec / cross_mag, θ
