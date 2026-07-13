"""
Globus Force Spin.

Minimal motor test: opens the real link to ink and forces a fixed rate on
one wheel at a time, bypassing satellite tracking, the controller, and
state entirely, while printing ink's own step-by-step debug output live.
For isolating whether tsup commanding ink actually turns the motors,
independent of everything else in main.py.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import time

import link

MOTOR = 0  # which wheel to test (0, 1, or 2) - the other two stay at 0
RATE = 100  # steps/sec
HOLD_SECONDS = 5


def rates_for(motor, rate):
    rates = [0, 0, 0]
    rates[motor] = rate
    return rates


def main():
    conn = link.open_link()
    rates = rates_for(MOTOR, RATE)
    print(f"Connected. Sending V {rates[0]} {rates[1]} {rates[2]} for {HOLD_SECONDS}s...")
    print("ink's own step debug output follows:")

    start = time.monotonic()
    last_send = 0.0
    try:
        while time.monotonic() - start < HOLD_SECONDS:
            now = time.monotonic()
            if now - last_send > 0.1:
                link.send_rates(conn, rates)  # resend - stay inside ink's watchdog
                last_send = now
            if conn.in_waiting:
                line = conn.readline().decode(errors="replace").strip()
                if line:
                    print(line)
    finally:
        print("Stopping.")
        link.send_rates(conn, [0, 0, 0])
        conn.close()


if __name__ == "__main__":
    main()
