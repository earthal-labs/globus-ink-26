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
STEPS_PER_RAD = 4075.77 / (2 * pi)  # 28BYJ-48 half-step count / rev, true 63.684:1 ratio

# --- Calibration (sec. 6.5) ---
DIR = [1, 1, 1]  # per-wheel direction sign - flip via calibration ritual, not by guessing in your head

# --- Controller (sec. 4) ---
TICK_HZ = 10
GAIN_K = 0.5         # 1/s; larger = snappier retargets
OMEGA_MAX = 0.26      # rad/s; matches the ~450 steps/s wheel cap
DEADBAND_DEG = 0.05   # TODO: asymmetric wake/sleep thresholds instead of one

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
