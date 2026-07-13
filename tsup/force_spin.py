"""
Globus Force Spin.

Minimal motor test: opens the real link to ink and forces a fixed rate on
one wheel at a time, bypassing satellite tracking, the controller, and
state entirely, while printing ink's own step-by-step debug output live.
Cycles through all three motors in turn, using each one's existing wiring
unchanged, so a single run shows whether a symptom is specific to one
motor/board or common to all three.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import time

import link

RATE = 100  # steps/sec
HOLD_SECONDS = 4
PAUSE_BETWEEN_SECONDS = 1.5


def rates_for(motor, rate):
    rates = [0, 0, 0]
    rates[motor] = rate
    return rates


def run_one_motor(conn, motor):
    rates = rates_for(motor, RATE)
    print(f"\n=== Motor {motor}: V {rates[0]} {rates[1]} {rates[2]} for {HOLD_SECONDS}s ===")

    start = time.monotonic()
    last_send = 0.0
    while time.monotonic() - start < HOLD_SECONDS:
        now = time.monotonic()
        if now - last_send > 0.1:
            link.send_rates(conn, rates)  # resend - stay inside ink's watchdog
            last_send = now
        if conn.in_waiting:
            line = conn.readline().decode(errors="replace").strip()
            if line:
                print(line)
    link.send_rates(conn, [0, 0, 0])


def main():
    conn = link.open_link()
    try:
        for motor in (0, 1, 2):
            run_one_motor(conn, motor)
            time.sleep(PAUSE_BETWEEN_SECONDS)
    finally:
        print("\nStopping.")
        link.send_rates(conn, [0, 0, 0])
        conn.close()


if __name__ == "__main__":
    main()
