"""
Globus Config.

Physical build constants and calibration flags: the values a rebuild with
different hardware, or a fresh calibration ritual, would need to change.
See docs/globus-logic.md sections 6.1 and 6.5.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

from math import pi

R = 0.0762                          # sphere radius, m (6" sphere)
r = 0.029                           # wheel radius, m (58 mm Nexus omniwheel)
α = 40                              # wheel contact ring angle below equator, degrees
ψ = [0, 120, 240]                   # wheel azimuths, degrees (Y-drive, 120 deg apart)

STEPS_PER_RAD = 4075.77 / (2 * pi)  # 28BYJ-48 half-step count / rev, true 63.684:1 ratio

DIR = [1, 1, 1]                     # per-wheel direction sign - flip via calibration
                                    # ritual (sec. 6.5), not by guessing in your head
