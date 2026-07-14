"""
Globus Tracker.

The tsup control loop: propagates the tracked satellite's orbit, computes
the globe's target orientation, and drives the three wheels via ink over
serial. See docs/globus-logic.md section 9.2 for the pseudocode this
follows, and section 10 for how it fits the overall build order.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import time
from math import degrees, radians, asin, atan2
from pathlib import Path
from json import loads, dumps, JSONDecodeError
from queue import Empty

from numpy import array, clip
from numpy.linalg import norm
from skyfield.api import Loader

import link
from bridge import Bridge
from kinematics import (
    wheel_rates, actual_omega, rotate, conjugate, shortest_arc,
    from_axis_angle, multiply, normalize, latlon_to_body,
)
from config import (
    SATELLITES, DEFAULT_SATELLITE, TLE_MAX_AGE_DAYS, STATE_DIR,
    TICK_HZ, GAIN_K, OMEGA_MAX, OMEGA_MIN,
    DEADBAND_SLEEP_DEG, DEADBAND_WAKE_DEG, STEPS_PER_RAD, r,
)

STATE_PATH = Path(STATE_DIR) / "state.json"
# TLE cache lives separately from STATE_PATH - it's fine to lose on reboot
# (load_cached_tle() just re-fetches), so it stays under the overlay rather
# than competing for space on /boot/firmware.
TLE_CACHE_DIR = Path(__file__).parent / "data"
ZHAT = array([0, 0, 1])
DEADBAND_SLEEP = radians(DEADBAND_SLEEP_DEG)
DEADBAND_WAKE = radians(DEADBAND_WAKE_DEG)

TLE_URL_TEMPLATE = "https://celestrak.org/NORAD/elements/gp.php?CATNR={}&FORMAT=TLE"

# Shared across load_cached_tle()/now_utc() - avoid rebuilding per call.
loader = Loader(str(TLE_CACHE_DIR))
ts = loader.timescale()


def load_state():
    """Load q from STATE_PATH, or None if missing/unreadable."""
    if not STATE_PATH.exists():
        return None
    try:
        data = loads(STATE_PATH.read_text())
        if not (isinstance(data, list) and len(data) == 4):
            return None

        return array(data)
    except (JSONDecodeError, OSError):
        return None


def save_state(q):
    """Atomically persist q to STATE_PATH."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.with_suffix(".tmp").write_text(dumps(q.tolist()))
    STATE_PATH.with_suffix(".tmp").replace(STATE_PATH)


def align_ritual_returning_q0():
    """Walk the human through aligning the globe, then return q0."""
    print(
        "Rotate the globe until the (0,0) crosshair sits under the "
        "reference pointer, pole lying sideways (sec. 5.4)."
    )
    input("Press Enter once aligned: ")
    q0 = from_axis_angle(array([0, 1, 0]), -90)
    save_state(q0)
    print("Alignment complete.")

    return q0


def tle_filename(norad_id):
    """Per-satellite cache filename, so tracking a different one can't collide."""
    return f"{norad_id}.tle"


def load_cached_tle(norad_id):
    """Load/fetch a satellite's TLE by NORAD id (sec. 7). A network
    failure only raises if there's no cache to fall back on."""
    filename = tle_filename(norad_id)
    url = TLE_URL_TEMPLATE.format(norad_id)
    cache_path = Path(loader.path_to(filename))

    if not cache_path.exists():
        return loader.tle_file(url, filename=filename, reload=True)[0]

    if loader.days_old(filename) > TLE_MAX_AGE_DAYS:
        try:
            return loader.tle_file(url, filename=filename, reload=True)[0]
        except Exception:
            pass  # stale cache beats no tracking

    return loader.tle_file(filename)[0]


def subpoint_latlon(satellite, now):
    """Latitude/longitude (degrees) the satellite is currently over."""
    geo = satellite.at(now).subpoint()

    return geo.latitude.degrees, geo.longitude.degrees


def crosshair_latlon(q):
    """Lat/lon currently under the crosshair - inverts latlon_to_body via q,
    so switching into MANUAL (or reporting STATE) reflects where the globe
    actually is, not an aspirational target."""
    x, y, z = rotate(conjugate(q), ZHAT)
    lat = degrees(asin(clip(z, -1.0, 1.0)))
    lon = degrees(atan2(y, x))

    return lat, lon


def now_utc():
    """Current UTC instant, as a Skyfield time."""
    return ts.now()


def apply_commands(commands, state):
    """Drain the bridge's command queue into `state` (a dict - see main()).
    Runs on the control-loop thread; every command here is a cheap local
    update, never I/O (TLE fetches already happened on the bridge thread)."""
    while True:
        try:
            cmd = commands.get_nowait()
        except Empty:
            return
        tag = cmd[0]

        if tag == "GOTO":
            _, lat, lon = cmd
            state["manual_lat"], state["manual_lon"] = lat, lon
            state["mode"] = "MANUAL"

        elif tag == "MODE":
            _, new_mode = cmd
            if new_mode == "MANUAL" and state["mode"] != "MANUAL":
                # Seed from where the globe actually is - no jump on switch.
                state["manual_lat"], state["manual_lon"] = crosshair_latlon(state["q"])
            state["mode"] = new_mode

        elif tag == "TRACK_READY":
            _, name, satellite = cmd
            state["satellite"], state["satellite_name"] = satellite, name
            state["mode"] = "AUTO"

        elif tag == "TRACK_ERROR":
            _, message = cmd
            print(f"track failed: {message}")

        elif tag == "DISCONNECTED":
            print("vzor disconnected")


def compute_omega(axis, θ, driving):
    """Proportional ω with |ω| floors/caps and wake/sleep hysteresis.

    Returns (ω, driving_next). Sleep when θ drops below DEADBAND_SLEEP;
    only resume once θ exceeds DEADBAND_WAKE — kills the 0↔2 steps/s chatter
    around a single threshold. OMEGA_MIN makes active corrections visible.
    """
    if driving:
        if θ < DEADBAND_SLEEP:
            return array([0.0, 0.0, 0.0]), False
    else:
        if θ < DEADBAND_WAKE:
            return array([0.0, 0.0, 0.0]), False

    mag = min(GAIN_K * θ, OMEGA_MAX)
    if mag < OMEGA_MIN:
        mag = OMEGA_MIN
    return mag * axis, True


def inject_orientation_error(q, degrees_error):
    """Compose a known body-frame rotation so θ starts large (demo / recovery)."""
    if degrees_error == 0:
        return q
    δq = from_axis_angle(array([1.0, 0.0, 0.0]), degrees_error)
    return normalize(multiply(δq, q))


def main(satellite_name=None, inject_error_deg=0.0, realign=False):
    """Entry point: align/restore state, then run the tracking loop (sec. 9.2)."""
    norad_id = SATELLITES[satellite_name or DEFAULT_SATELLITE]

    q = None if realign else load_state()
    if q is None:
        q = align_ritual_returning_q0()
    if inject_error_deg:
        q = inject_orientation_error(q, inject_error_deg)
        print(
            f"Injected {inject_error_deg:.0f}° software error — expect a visible "
            f"slew (OMEGA_MIN={OMEGA_MIN} rad/s floor)."
        )
        save_state(q)

    conn = link.open_link()
    satellite = load_cached_tle(norad_id)
    manual_lat, manual_lon = crosshair_latlon(q)

    state = {
        "q": q,
        "mode": "AUTO",
        "satellite": satellite,
        "satellite_name": satellite_name or DEFAULT_SATELLITE,
        "manual_lat": manual_lat,
        "manual_lon": manual_lon,
    }

    bridge = Bridge(load_cached_tle)
    bridge.start()

    last_tick = time.monotonic()
    last_save = last_tick
    driving = False

    try:
        while True:
            tick_start = time.monotonic()
            Δt = tick_start - last_tick
            last_tick = tick_start

            apply_commands(bridge.commands, state)
            q = state["q"]

            # Target direction (sec. 3, 7): satellite subpoint in AUTO,
            # manual target (bridge-driven) in MANUAL.
            if state["mode"] == "AUTO":
                φ, λ = subpoint_latlon(state["satellite"], now_utc())
            else:
                lat_rate, lon_rate = bridge.pan_rate()
                manual_lat = clip(state["manual_lat"] + lat_rate * Δt, -90.0, 90.0)
                manual_lon = ((state["manual_lon"] + lon_rate * Δt + 180) % 360) - 180
                state["manual_lat"], state["manual_lon"] = manual_lat, manual_lon
                φ, λ = manual_lat, manual_lon

            p_b = latlon_to_body(φ, λ)
            u = rotate(q, p_b)
            axis, θ = shortest_arc(u, ZHAT)

            # Controller (sec. 4)
            ω, driving = compute_omega(axis, θ, driving)

            # Command the wheels (sec. 8, 9.1)
            rates = wheel_rates(ω)
            peak = max((abs(int(x)) for x in rates), default=0)
            rim_mm_s = (peak / STEPS_PER_RAD) * r * 1000.0
            status = "DRIVE" if driving else "HOLD"
            print(
                f"{status} θ={degrees(θ):.2f}° rates={rates} "
                f"|rates|_max={peak} rim≈{rim_mm_s:.1f}mm/s"
            )
            link.send_rates(conn, rates)

            # Integrate ω_actual, not ω - quantization fix (sec. 5.2).
            # NOTE: this assumes commanded rates were achieved. If the physical
            # globe didn't move, software q still advances → θ shrinks on paper.
            ω_actual = actual_omega(rates)
            mag = norm(ω_actual)
            if mag > 0:
                # from_axis_angle wants degrees; mag * Δt is radians.
                δq = from_axis_angle(ω_actual / mag, degrees(mag * Δt))
                q = normalize(multiply(δq, q))
            state["q"] = q

            disp_lat, disp_lon = crosshair_latlon(q)
            sat_name = state["satellite_name"] if state["mode"] == "AUTO" else None
            bridge.set_state(disp_lat, disp_lon, sat_name, state["mode"])

            if tick_start - last_save > 60:
                save_state(q)
                last_save = tick_start

            sleep_for = (1 / TICK_HZ) - (time.monotonic() - tick_start)
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        pass
    # TODO: SIGTERM won't hit this handler - add via `signal` if run as a service.
    finally:
        bridge.stop()
        link.send_rates(conn, [0, 0, 0])
        save_state(state["q"])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Globus tsup tracker")
    parser.add_argument(
        "--satellite",
        default=DEFAULT_SATELLITE,
        choices=sorted(SATELLITES),
        help="NORAD-tracked satellite name from config.SATELLITES",
    )
    parser.add_argument(
        "--inject-error-deg",
        type=float,
        default=0.0,
        help="Rotate software q by this many degrees at startup so θ is large "
             "and OMEGA_MIN produces a visible slew (e.g. 90)",
    )
    parser.add_argument(
        "--realign",
        action="store_true",
        help="Ignore saved state and re-run the alignment ritual",
    )
    args = parser.parse_args()
    main(
        satellite_name=args.satellite,
        inject_error_deg=args.inject_error_deg,
        realign=args.realign,
    )