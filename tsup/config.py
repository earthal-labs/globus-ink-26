"""
Globus Config.

Physical build constants, calibration flags, and tuning parameters: the
values you'd want to change without touching logic. See
docs/globus-logic.md sections 6.1, 6.5, and 4.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

from math import pi

# --- Physical build (sec. 6.1) ---
R = 0.0762                          # sphere radius, m (6" sphere)
r = 0.029                           # wheel radius, m (58 mm Nexus omniwheel)
α = 40                              # wheel contact ring angle below equator, degrees
ψ = [0, 120, 240]                   # wheel azimuths, degrees (Y-drive, 120 deg apart)
# Must match ink.ino production drive mode (default MODE_NAT_HALF).
# Half-step: 4075.78 steps/rev (bringup-proven). Full-step: 2037.89.
STEPS_PER_RAD = 4075.78 / (2 * pi)  # 28BYJ-48 HALF-step count / rev (true 63.684:1)

# --- Calibration (sec. 6.5) ---
DIR = [1, 1, 1]  # per-wheel direction sign - flip via calibration ritual, not by guessing in your head

# --- Controller (sec. 4) ---
TICK_HZ = 10
# With θ only ~1–2° (nearly aligned), ω = GAIN_K·θ stays tiny unless GAIN is
# assertive. Brought up for visible slews now that half-step @ ~833 is proven.
GAIN_K = 2.0         # 1/s; larger = snappier retargets
# Globe |ω| cap (rad/s). 0.20 → roughly hundreds of half-steps/s (under the
# bringup-proven 833). Raise toward 0.26 if slews feel slow; drop if cold
# starts under load chug again.
OMEGA_MAX = 0.20
# Asymmetric deadband (wake/sleep). Sleep when closer than SLEEP; only resume
# once error exceeds WAKE. Wake used to be 1.5° which parked ISS tracking in
# HOLD for a long time — small corrections use RATE_OVERDRIVE_SMALL instead.
DEADBAND_SLEEP_DEG = 0.20
DEADBAND_WAKE_DEG = 0.60
# Friction-drive overdrive (adaptive). Big slews overshot at 2–3× constant
# scale; tiny tracking nudges need *more* boost to break stiction/inertia.
# Interpolate by peak |kinematic| rate: small → OVERDRIVE_SMALL, large → OVERDRIVE_LARGE.
RATE_OVERDRIVE_SMALL = 3.5   # boost when kin rates are tiny (tracking / catch-up)
RATE_OVERDRIVE_LARGE = 1.5   # closer to 1× on big slews (less overshoot)
RATE_SLEW_REF = 250          # kin |rate| peak at which scale reaches LARGE
RATE_CAP = 833               # half-steps/s — bringup-proven ceiling

# --- Satellite tracking (sec. 7) ---
SATELLITES = {"ISS": 25544}  # name -> NORAD catalog id; add more here
DEFAULT_SATELLITE = "ISS"
TLE_MAX_AGE_DAYS = 1.0

# --- Serial link to ink (docs/protocol.md) ---
SERIAL_PORT = "/dev/ttyACM0"  # varies per machine; ink.sh auto-detects this at flash time
SERIAL_BAUD = 115200          # fixed by the wire protocol - don't change without ink.ino too
SERIAL_BOOT_WAIT_S = 2        # ink resets when the port opens; wait for it before reading hello

# --- vzor bridge (docs/bridge-protocol.md) ---
BRIDGE_HOST = "127.0.0.1"  # loopback only - this drives real motors, never LAN-reachable
BRIDGE_PORT = 8765
PAN_WATCHDOG_MS = 300       # no PAN in this long -> treat rate as 0 (silence means stop)

# --- Persisted state (sec. 5.3) ---
# On a Pi running the overlay filesystem (root is RAM-backed, changes don't
# survive reboot), this must point somewhere outside the overlay or q0 gets
# silently lost every reboot. /boot/firmware is deliberately left off the
# overlay for exactly this reason - see docs/globus-logic.md sec. 5.3.
# On any other machine (dev/test), just a local path - override if needed.
STATE_DIR = "/boot/firmware/globus-state"
