"""
Globus Tracker.

The tsup control loop: propagates the tracked satellite's orbit, computes
the globe's target orientation, and drives the three wheels via ink over
serial. See docs/globus-logic.md section 9.2 for the pseudocode this
follows, and section 10 for how it fits the overall build order.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import time
from math import degrees, radians
from pathlib import Path
from json import loads, dumps, JSONDecodeError

from numpy import array
from numpy.linalg import norm
from skyfield.api import Loader

import link
from kinematics import (
    wheel_rates, actual_omega, rotate, shortest_arc,
    from_axis_angle, multiply, normalize, latlon_to_body,
)
from config import (
    SATELLITES, DEFAULT_SATELLITE, TLE_MAX_AGE_DAYS,
    TICK_HZ, GAIN_K, OMEGA_MAX, DEADBAND_DEG,
)

STATE_PATH = Path(__file__).parent / "data" / "state.json"
ZHAT = array([0, 0, 1])
DEADBAND = radians(DEADBAND_DEG)

TLE_URL_TEMPLATE = "https://celestrak.org/NORAD/elements/gp.php?CATNR={}&FORMAT=TLE"

# Shared across load_cached_tle()/now_utc() - avoid rebuilding per call.
loader = Loader(str(STATE_PATH.parent))
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


def now_utc():
    """Current UTC instant, as a Skyfield time."""
    return ts.now()


def main(satellite_name=None):
    """Entry point: align/restore state, then run the tracking loop (sec. 9.2)."""
    norad_id = SATELLITES[satellite_name or DEFAULT_SATELLITE]

    q = load_state()
    if q is None:
        q = align_ritual_returning_q0()

    conn = link.open_link()
    satellite = load_cached_tle(norad_id)

    last_tick = time.monotonic()
    last_save = last_tick

    try:
        while True:
            tick_start = time.monotonic()
            Δt = tick_start - last_tick
            last_tick = tick_start

            # Orbit -> target direction (sec. 3, 7)
            φ, λ = subpoint_latlon(satellite, now_utc())
            p_b = latlon_to_body(φ, λ)
            u = rotate(q, p_b)
            axis, θ = shortest_arc(u, ZHAT)

            # Controller (sec. 4)
            if θ < DEADBAND:
                ω = array([0.0, 0.0, 0.0])
            else:
                ω = min(GAIN_K * θ, OMEGA_MAX) * axis

            # Command the wheels (sec. 8, 9.1)
            rates = wheel_rates(ω)
            link.send_rates(conn, rates)

            # Integrate ω_actual, not ω - quantization fix (sec. 5.2).
            ω_actual = actual_omega(rates)
            mag = norm(ω_actual)
            if mag > 0:
                # from_axis_angle wants degrees; mag * Δt is radians.
                δq = from_axis_angle(ω_actual / mag, degrees(mag * Δt))
                q = normalize(multiply(δq, q))

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
        link.send_rates(conn, [0, 0, 0])
        save_state(q)


if __name__ == "__main__":
    main()
