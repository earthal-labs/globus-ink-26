# Globus INK 2026

A working replica of the Soviet **Globus INK** (Индикатор Навигационный Космический,
"space navigation indicator"), the rotating-globe instrument that showed Soyuz
cosmonauts their position over Earth.

## The original

The Globus was an electromechanical analog computer. Inside the case, gear trains,
shaped cams, and differential gears computed the spacecraft's predicted position
and turned a small painted globe beneath fixed crosshairs, so the map under the
crosshairs matched the view out the window. It took no sensor input. Cosmonauts
dialed in the starting position and orbital period by hand, and the mechanism dead
reckoned from there.

The design has one famous constraint: the globe's rotation axis was built at a fixed
51.8°, the standard Soyuz inclination, frozen into the metal. A different orbit
required building a different Globus. A second mode spun the globe forward through a
set angle to preview the landing site if retrorockets were fired at that moment.

The first version (the IMP) was developed starting in 1960 and flew on Vostok and
Voskhod. The INK model flew on Soyuz from 1967 and stayed on the console until
Soyuz-TMA replaced it with a digital display in 2002. Ken Shirriff's
[teardown](https://www.righto.com/2023/01/inside-globus-ink-mechanical-navigation.html)
of a surviving unit inspired this project.

## This replica

Same idea, modern parts. A Raspberry Pi propagates real orbits from live TLE data
and a globe physically follows the satellite's ground track. Where the original froze
its orbit into gear ratios, this version drives the globe with three omniwheels, so
it can rotate about any axis and track any satellite. Starting with the ISS.

## Architecture

Two computers, named for their Soviet counterparts:

- **`tsup/`** — ЦУП (*Tsentr Upravleniya Polyotami*, Mission Control Center).
  Python on the Pi. Propagates orbits with Skyfield, computes the target globe
  orientation, runs the quaternion controller, streams wheel speeds over serial.
  - **`tsup/vzor/`** — Vzor (Взор, "Sight"), terminal UI forked from
    [tui-globe](https://github.com/d10n/tui-globe) (see [Acknowledgments](#acknowledgments)).
    Named for the periscope-style optical sighting instrument flown alongside the real
    Globus INK aboard Soyuz. Renders the live globe orientation as a braille globe in the
    terminal and feeds manual-control input (goto/pan/track) back into the tracker loop
    over a local TCP bridge. Rust/Cargo.
- **`ink/`** — ИНК (the instrument). C++ firmware on the Arduino Nano. Deliberately
  dumb: receives three signed wheel speeds, generates step pulses, reports faults.
  Knows nothing about satellites.

The serial contract lives in [`docs/protocol.md`](docs/protocol.md); changing that
file is the only event that requires a firmware reflash. The vzor/tsup bridge
contract lives in [`docs/bridge-protocol.md`](docs/bridge-protocol.md).

## Structure

| Path | Contents |
|---|---|
| `tsup/` | Python: main tracker loop, kinematics, config, serial link |
| `tsup/vzor/` | Rust: terminal UI (Vzor), forked from `tui-globe` |
| `ink/` | Arduino firmware (`arduino-cli`, fqbn `arduino:renesas_uno:nanor4`) |
| `ink/bringup/` | Freerunning motor-0 A/B sketch (`scripts/ink.sh bringup`) |
| `docs/` | `protocol.md` (tsup/ink serial), `bridge-protocol.md` (vzor/tsup), `calibration-bench.md` (steel-ball scale / reverse timing) |
| `hardware/` | Mount STLs, wiring notes, BOM |
| `scripts/` | `ink.sh` - compile/upload/monitor the firmware, run on the Pi |

Python env is managed with [uv](https://docs.astral.sh/uv/); the TUI is managed with
[Cargo](https://doc.rust-lang.org/cargo/).

## Hardware

| Component | Role |
|---|---|
| Raspberry Pi Zero 2 WH | tsup: orbit computation, control loop |
| Arduino Nano R4 H | ink: step pulse generation |
| Adafruit PiRTC (PCF8523) | correct time before network |
| 3× 28BYJ-48 + ULN2003 (JSumo kit) | 5 V geared steppers and drivers |
| 3× Nexus 58 mm double-row omniwheels | friction drive, 120° contact ring |
| 3× Nexus 5 mm mounting hubs | wheel-to-shaft coupling |

The globe rests on the three omniwheels at about 40° below its equator. Wheel speeds
are computed from the desired angular velocity vector, so the globe needs no gimbal
and no internal wiring.

## Status

- [x] Pi flashed, SSH up, uv environment
- [x] Monorepo and toolchains (uv, arduino-cli) on PC
- [x] Arduino flashed from the Pi over USB (arduino-cli + udev rules on Pi)
- [x] ink v1 firmware (serial protocol, steppers)
- [x] tsup tracking loop (Skyfield to wheel speeds)
- [ ] Mechanical build (deck, wedge mounts, cradle)
- [x] vzor manual-control bridge into the tracker loop

## Acknowledgments

`tsup/vzor/` is a fork of [tui-globe](https://github.com/d10n/tui-globe) by
[d10n](https://github.com/d10n), a ratatui widget that renders a 3D globe in the
terminal with braille characters. This project is licensed GPL-3.0-or-later to
build on that work. The map data under `tsup/vzor/assets/` is Natural Earth data
(public domain / CC0-1.0); see [`tsup/vzor/REUSE.toml`](tsup/vzor/REUSE.toml).

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).