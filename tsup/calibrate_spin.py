"""
Globus Calibration Bench.

Interactive metrology assistant for docs/calibration-bench.md: drives one
axis at constant kinematic omega (1x - no overdrive, no slew, no controller),
times full revolutions (Protocol A) or reverse take-up (Protocol B) against
your Enter key, then prints median scale factors k and suggested config
values, appending rows to data/calibration.csv.

Runs on the Pi (uses select() on stdin to keep ink's watchdog fed while
waiting for your keypress).

Protocol A (scale):   uv run python calibrate_spin.py
Protocol B (reverse): uv run python calibrate_spin.py --reverse

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import argparse
import csv
import select
import sys
import time
from math import pi, sqrt
from pathlib import Path
from statistics import median

from numpy import array

import link
from kinematics import wheel_rates

CSV_PATH = Path(__file__).parent / "data" / "calibration.csv"
SEND_INTERVAL_S = 0.1  # stay well inside ink's 500 ms watchdog

# Axis catalog (docs/calibration-bench.md): body-frame unit directions.
AXES = {
    "Z": array([0.0, 0.0, 1.0]),                    # spin / E-W (longitude)
    "X": array([1.0, 0.0, 0.0]),                    # tilt N-S
    "Y": array([0.0, 1.0, 0.0]),                    # tilt orthogonal
    "NE": array([1.0, 1.0, 0.0]) / sqrt(2.0),       # combined tilt
    "NW": array([1.0, -1.0, 0.0]) / sqrt(2.0),      # NE's mirror - hypothesis discriminator
}


def hold_rates_until_enter(conn, rates, prompt):
    """Send `rates` every SEND_INTERVAL_S until the user presses Enter.
    Returns the elapsed seconds since this call started."""
    print(prompt)
    start = time.monotonic()
    last_send = 0.0
    while True:
        now = time.monotonic()
        if now - last_send > SEND_INTERVAL_S:
            link.send_rates(conn, rates)
            last_send = now
        readable, _, _ = select.select([sys.stdin], [], [], SEND_INTERVAL_S)
        if readable:
            sys.stdin.readline()
            return time.monotonic() - start


def drain(conn):
    while conn.in_waiting:
        conn.readline()  # discard hb lines; they'd interleave with prompts


def append_csv(row):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["axis", "sign", "omega_cmd", "T_rev_s", "k", "notes"])
        writer.writerow(row)


def scale_trial(conn, axis_name, sign, omega_mag, trials):
    """Protocol A: time full revolutions at constant omega; returns list of k."""
    ω = sign * omega_mag * AXES[axis_name]
    rates = wheel_rates(ω)
    ideal_t = 2 * pi / omega_mag
    ks = []

    print(f"\n--- {axis_name}{'+' if sign > 0 else '-'}  |ω|={omega_mag} rad/s  "
          f"rates={list(rates)}  ideal T={ideal_t:.0f}s ---")

    for trial in range(1, trials + 1):
        input(f"[{trial}/{trials}] Position the timing mark ~30° BEFORE the "
              f"pointer, then press Enter to start motion: ")
        # Rolling start: get moving first (stiction take-up excluded), then
        # time pointer-crossing to pointer-crossing.
        hold_rates_until_enter(
            conn, rates,
            "Rolling... press Enter the instant the mark crosses the pointer "
            "(timer starts).",
        )
        t = hold_rates_until_enter(
            conn, rates,
            "Timing... press Enter when the SAME mark crosses the pointer "
            "again (one full lap).",
        )
        link.send_rates(conn, [0, 0, 0])
        drain(conn)
        k = t / ideal_t  # k = ω_cmd/ω_actual = T_measured/T_ideal
        ks.append(k)
        print(f"    T={t:.1f}s  k={k:.3f}")
        append_csv([axis_name, "+" if sign > 0 else "-", omega_mag,
                    round(t, 2), round(k, 4), ""])
    return ks


def reverse_trial(conn, axis_name, omega_mag, cruise_s):
    """Protocol B: cruise one way, flip, time dead-band and ramp to cruise."""
    ω = omega_mag * AXES[axis_name]
    fwd = wheel_rates(ω)
    rev = wheel_rates(-ω)
    cruise_sps = max(abs(int(x)) for x in fwd)

    print(f"\n--- reverse on {axis_name}: cruise {cruise_s}s fwd "
          f"(peak {cruise_sps} sps), then flip ---")
    end = time.monotonic() + cruise_s
    while time.monotonic() < end:
        link.send_rates(conn, fwd)
        time.sleep(SEND_INTERVAL_S)

    t_dead = hold_rates_until_enter(
        conn, rev, "FLIPPED. Press Enter at the FIRST visible motion the new way.")
    t_ramp = hold_rates_until_enter(
        conn, rev, "Now press Enter once it reaches steady cruise.")
    link.send_rates(conn, [0, 0, 0])
    drain(conn)

    print(f"    T_dead={t_dead:.2f}s  T_ramp={t_ramp:.2f}s")
    print(f"    suggest REVERSE_SETTLE_S ≈ {t_dead:.2f}")
    if t_ramp > 0:
        print(f"    suggest RATE_REVERSE_ACCEL_SPS2 ≈ {cruise_sps / t_ramp:.0f}")
    return t_dead, t_ramp


def main():
    parser = argparse.ArgumentParser(description="Calibration bench (see docs/calibration-bench.md)")
    parser.add_argument("--axes", nargs="+", choices=sorted(AXES), default=["Z", "X", "Y", "NE"],
                        help="Axes to run (default: all)")
    parser.add_argument("--omega", type=float, default=0.05, help="|ω| rad/s (default 0.05)")
    parser.add_argument("--trials", type=int, default=3, help="Trials per axis+sign")
    parser.add_argument("--reverse", action="store_true",
                        help="Protocol B (reverse take-up) instead of Protocol A")
    parser.add_argument("--cruise", type=float, default=5.0,
                        help="Protocol B: seconds of forward cruise before the flip")
    args = parser.parse_args()

    print("NOTE: this commands kinematic rates directly (1x, no overdrive).")
    print("Re-run the alignment ritual (--realign) before trusting AUTO again.\n")

    conn = link.open_link()
    try:
        if args.reverse:
            for axis_name in args.axes:
                reverse_trial(conn, axis_name, args.omega, args.cruise)
            return

        summary = {}
        for axis_name in args.axes:
            for sign in (+1, -1):
                ks = scale_trial(conn, axis_name, sign, args.omega, args.trials)
                summary[f"{axis_name}{'+' if sign > 0 else '-'}"] = median(ks)

        print("\n=== median k per axis (1.0 = ball matches model) ===")
        for name, k in summary.items():
            print(f"  {name}: {k:.3f}")
        overall = median(summary.values())
        print(f"\noverall median k = {overall:.3f}")
        print("k > 1: ball slower than model -> raise ink scale "
              "(RATE_OVERDRIVE_SMALL / MANUAL_OVERDRIVE_CAP)")
        print("axes disagreeing a lot: re-check α/R/r/contact pressure, not gain")
        print(f"rows appended to {CSV_PATH}")
    finally:
        link.send_rates(conn, [0, 0, 0])
        conn.close()


if __name__ == "__main__":
    main()
