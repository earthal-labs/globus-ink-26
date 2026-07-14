"""
Globus Force Spin.

Minimal motor smoke test: opens the real link to ink and holds a constant
V rate on one wheel at a time (forward, then reverse), bypassing satellite
tracking, the controller, and overdrive entirely. For proving the
tsup -> ink -> motor path; for scale/reverse metrology use
calibrate_spin.py (docs/calibration-bench.md).

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import argparse
import time

import link

RATE = 833  # half-steps/s, the bringup-proven cruise
HOLD_SECONDS = 4
PAUSE_BETWEEN_SECONDS = 1.0


def rates_for(motor, rate):
    rates = [0, 0, 0]
    rates[motor] = rate
    return rates


def drain(conn):
    while conn.in_waiting:
        line = conn.readline().decode(errors="replace").strip()
        if line:
            print(line)


def run_one(conn, motor, rate, hold_seconds):
    rates = rates_for(motor, rate)
    print(f"\n=== motor {motor}: V {rates[0]} {rates[1]} {rates[2]} for {hold_seconds}s ===")

    start = time.monotonic()
    last_send = 0.0
    while time.monotonic() - start < hold_seconds:
        now = time.monotonic()
        if now - last_send > 0.1:
            link.send_rates(conn, rates)  # resend - stay inside ink's watchdog
            last_send = now
        drain(conn)
    link.send_rates(conn, [0, 0, 0])


def main():
    parser = argparse.ArgumentParser(description="Smoke-test the tsup->ink->motor path")
    parser.add_argument("--motors", nargs="+", type=int, choices=(0, 1, 2),
                        default=[0, 1, 2], help="Motors to spin (default: all three)")
    parser.add_argument("--rate", type=int, default=RATE, help="Half-steps/sec")
    parser.add_argument("--hold", type=float, default=HOLD_SECONDS, help="Seconds per direction")
    args = parser.parse_args()

    conn = link.open_link()
    try:
        for motor in args.motors:
            run_one(conn, motor, args.rate, args.hold)
            run_one(conn, motor, -args.rate, args.hold)
            time.sleep(PAUSE_BETWEEN_SECONDS)
    finally:
        print("\nStopping.")
        link.send_rates(conn, [0, 0, 0])
        conn.close()


if __name__ == "__main__":
    main()
