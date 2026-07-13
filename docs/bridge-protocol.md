# vzor/tsup bridge protocol

**Version: 0** (draft, in progress)

TCP, loopback only (`127.0.0.1`), newline-terminated ASCII. tsup is the
server (binds once at startup); vzor is the client (connects on launch,
reconnects with backoff on any drop). Single client - a new connection
replaces, rather than being rejected by, an existing one.

| Message | Direction | Meaning |
|---|---|---|
| `GOTO <lat> <lon>` | vzor → tsup | one-shot manual target; switches tsup to MANUAL |
| `PAN <lat_rate> <lon_rate>` | vzor → tsup | continuous target nudge, deg/s; resent every tick while a key is held |
| `TRACK <name_or_norad_id>` | vzor → tsup | switch tracked satellite; switches tsup to AUTO |
| `MODE AUTO` / `MODE MANUAL` | vzor → tsup | explicit mode switch |
| `STATE <lat> <lon> <sat_name_or_dash> <mode>` | tsup → vzor | current crosshair position and mode, broadcast at tsup's own ~10Hz pace |

There is no separate `HOME` message - vzor's Home key/command just sends
`GOTO 0 0`.

## Design notes

- **Manual steering is a moving `GOTO` target, not a raw angular velocity.**
  Rotating the globe about the world vertical axis is exactly
  `docs/globus-logic.md` section 6.3's "pure spin" - it provably does not
  move the crosshair subpoint. So `GOTO`/`PAN` both just update tsup's
  `manual_target_lat`/`lon` and let the existing `shortest_arc` ->
  P-controller -> `wheel_rates` pipeline (the same one `AUTO` uses) drive
  toward it - no separate kinematics path for manual control.
- **PAN has its own watchdog**, independent of and layered above `ink`'s
  own serial watchdog: silence longer than `PAN_WATCHDOG_MS` (see
  `tsup/config.py`) means the rate reads as zero, the same "stop on
  silence" reasoning as `ink.ino`'s.
- **The bridge socket runs on its own background thread on the tsup side**
  (`tsup/bridge.py`), never inline in the 10Hz control loop - a slow or
  stalled vzor client must never be able to block wheel commands. TLE
  fetches triggered by `TRACK` also happen on that background thread, not
  the control loop.
- **STATE reports where the globe actually is** (from tsup's current
  orientation `q`), not the aspirational target - so vzor never displays
  fake motion as if it were real.
- **Disconnects freeze rather than guess.** If vzor drops while tsup is in
  MANUAL, the PAN rate reads as zero (frozen in place) rather than tsup
  reverting to AUTO on its own. If tsup drops, vzor falls back to a local
  placeholder simulation and marks itself `DISCONNECTED - simulated` in
  the title bar for as long as it isn't backed by a live feed.

## Rules

- Any breaking change (message removed, field meaning changed) bumps the
  version.
- The version lives in this file and both implementations
  (`tsup/bridge.py`, `tsup/vzor/src/bridge.rs`) - there's no version
  handshake on the wire yet (unlike `ink p0`), since both sides ship from
  the same repo today.
