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
GAIN_K = 2.0     # 1/s; larger = snappier retargets
OMEGA_MAX = 0.20  # rad/s globe cap; keeps peak rates under the proven 833 sps
# Asymmetric deadband: sleep under SLEEP, only resume past WAKE.
DEADBAND_SLEEP_DEG = 0.20
DEADBAND_WAKE_DEG = 0.60

# --- Friction-drive overdrive (docs/calibration-bench.md) ---
# Adaptive ink boost by peak kin |rate|: ~1x on nudges, up to LARGE on big
# slews (breaking the steel sphere free under load). DR always integrates
# the UNSCALED rates, so overdrive != 1x desyncs q from the ball - see doc.
RATE_OVERDRIVE_SMALL = 1.0   # scale when kin rates are tiny
RATE_OVERDRIVE_LARGE = 10.0  # scale once peak reaches RATE_SLEW_REF
RATE_SLEW_REF = 250          # kin |rate| peak (sps) at which scale = LARGE
RATE_CAP = 833               # half-steps/s - bringup-proven ceiling
MANUAL_OVERDRIVE_CAP = 1.0   # MANUAL stays ~1x so vzor STATE stays faithful

# --- Rate slew (reverse take-up; docs/calibration-bench.md) ---
# Decelerate through 0, settle, then soft climb-out - never slam a gearbox
# straight to the opposite sign.
RATE_ACCEL_SPS2 = 200.0          # same-sign approach (steps/s^2)
RATE_REVERSE_ACCEL_SPS2 = 80.0   # climb-out after stop/reverse
REVERSE_SETTLE_S = 0.35          # dwell at 0 when crossing directions

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
PAN_WATCHDOG_MS = 300  # no PAN in this long -> treat rate as 0 (silence means stop)
PAN_RATE_SCALE = 1.0   # scales vzor's PAN deg/s (ships 10) without rebuilding Rust

# --- Persisted state (sec. 5.3) ---
# On a Pi running the overlay filesystem (root is RAM-backed, changes don't
# survive reboot), this must point somewhere outside the overlay or q0 gets
# silently lost every reboot. /boot/firmware is deliberately left off the
# overlay for exactly this reason - see docs/globus-logic.md sec. 5.3.
# On any other machine (dev/test), just a local path - override if needed.
STATE_DIR = "/boot/firmware/globus-state"
