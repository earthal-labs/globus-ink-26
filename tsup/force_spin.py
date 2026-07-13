"""
Globus Force Spin / sequence bench.

Opens the real link to ink and exercises coil-drive modes (nat/swap ×
full/half) on one wheel at a time, bypassing satellite tracking. Use this
to find which drive map makes the shaft rotate, then lock that mode as
production in ink.ino (default remains nat_full).

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import argparse
import time

import link

# Slow enough that the ULN LED pair walk is visible; full-step at 40 steps/s
# turns the geared output ~7°/s — obvious within a few seconds.
RATE = 40
HOLD_SECONDS = 4
PAUSE_BETWEEN_SECONDS = 1.0
MODES = ("nat_full", "nat_half", "swap_full", "swap_half")


def rates_for(motor, rate):
    rates = [0, 0, 0]
    rates[motor] = rate
    return rates


def drain(conn):
    while conn.in_waiting:
        line = conn.readline().decode(errors="replace").strip()
        if line:
            print(line)


def set_drive_mode(conn, mode):
    conn.write(f"D {mode}\n".encode())
    time.sleep(0.05)
    drain(conn)


def run_one(conn, motor, mode, rate, hold_seconds):
    rates = rates_for(motor, rate)
    print(f"\n=== mode={mode} motor={motor}: V {rates[0]} {rates[1]} {rates[2]} "
          f"for {hold_seconds}s ===")
    set_drive_mode(conn, mode)

    start = time.monotonic()
    last_send = 0.0
    while time.monotonic() - start < hold_seconds:
        now = time.monotonic()
        if now - last_send > 0.1:
            link.send_rates(conn, rates)
            last_send = now
        drain(conn)
    link.send_rates(conn, [0, 0, 0])


def run_t_command(conn, motor, mode):
    """Self-held firmware bench: no V keepalive required for BENCH_HOLD_MS."""
    print(f"\n=== T {motor} {mode} (ink self-hold) ===")
    conn.write(f"T {motor} {mode}\n".encode())
    start = time.monotonic()
    while time.monotonic() - start < HOLD_SECONDS + 0.5:
        drain(conn)
        time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(description="Bench-spin ink motors / drive modes")
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=MODES,
        default=list(MODES),
        help="Drive modes to try (default: all four)",
    )
    parser.add_argument(
        "--motors",
        nargs="+",
        type=int,
        choices=(0, 1, 2),
        default=[0, 1, 2],
        help="Motors to try (default: all three)",
    )
    parser.add_argument("--rate", type=int, default=RATE, help="Steps/sec for V commands")
    parser.add_argument("--hold", type=float, default=HOLD_SECONDS, help="Seconds per trial")
    parser.add_argument(
        "--via-t",
        action="store_true",
        help="Use ink's self-held T command instead of keepalive V",
    )
    args = parser.parse_args()

    conn = link.open_link()
    try:
        for mode in args.modes:
            for motor in args.motors:
                if args.via_t:
                    run_t_command(conn, motor, mode)
                else:
                    run_one(conn, motor, mode, args.rate, args.hold)
                time.sleep(PAUSE_BETWEEN_SECONDS)
    finally:
        print("\nStopping.")
        link.send_rates(conn, [0, 0, 0])
        set_drive_mode(conn, "nat_full")  # leave production default selected
        conn.close()


if __name__ == "__main__":
    main()
