"""
Globus Force Spin.

Minimal motor test: opens the real link to ink and forces a fixed rate on
all three wheels for a few seconds, bypassing satellite tracking, the
controller, and state entirely. For isolating whether tsup commanding ink
actually turns the motors, independent of everything else in main.py.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import time

import link

RATE = 100  # steps/sec, same on all three wheels
HOLD_SECONDS = 5


def main():
    conn = link.open_link()
    print(f"Connected. Sending V {RATE} {RATE} {RATE} for {HOLD_SECONDS}s...")

    start = time.monotonic()
    try:
        while time.monotonic() - start < HOLD_SECONDS:
            link.send_rates(conn, [RATE, RATE, RATE])  # resend - stay inside ink's watchdog
            time.sleep(0.1)
    finally:
        print("Stopping.")
        link.send_rates(conn, [0, 0, 0])
        conn.close()


if __name__ == "__main__":
    main()
