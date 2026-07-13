// SPDX-License-Identifier: GPL-3.0-or-later

//! Vzor: the human-facing terminal for the Globus INK replica. Tab toggles
//! Auto/Manual, arrows steer in Manual (diagonals included), PageUp/
//! PageDown zoom, Home eases back to (0, 0), End quits, "/" opens a
//! command line.
//!
//! While connected to tsup's bridge (see docs/bridge-protocol.md), the
//! globe shown is the real one: `goto`/`lat`/`lon`/`home`/arrow-key
//! steering and `track` all drive it over the wire, and the rendered
//! position comes from tsup's own `STATE` broadcasts rather than being
//! simulated here. When the bridge is unreachable, vzor falls back to a
//! local placeholder simulation (unchanged from the pre-bridge dev demo)
//! and marks the title bar `DISCONNECTED - simulated` for the whole time
//! it isn't backed by a live feed - this app never quietly shows fake
//! motion as if it were the real globe.
//!
//! Commands (typed after "/", Enter to run, Esc to cancel):
//!   home              - same as the Home key
//!   end               - same as the End key
//!   mode [auto|manual] - toggles with no argument, or sets explicitly
//!   zin / zout        - same as PageUp/PageDown
//!   lon <deg>         - go to longitude <deg>, keep current latitude
//!   lat <deg>         - go to latitude <deg>, keep current longitude
//!   goto <lon> <lat>  - go to both
//!   track <id>        - switch tracked satellite by name or NORAD id
//!   info              - what this terminal is and why it's named Vzor
//!   help              - this command list, in-app
//!
//! Tracking is cleared as soon as the user takes back manual control - an
//! arrow key, Home, or a lat/lon/goto command - since at that point the
//! globe is no longer following whatever `track` last named.
//!
//! Manual mode moves at a fixed angular rate while an arrow key is held,
//! rather than tracking a continuous drag delta, since the real globe is
//! driven by stepper motors with a bounded angular rate. Local simulated
//! easing (used only while disconnected) follows the same reasoning.
//!
//! Pitch is unclamped and wraps like yaw in the local simulation: holding
//! Up past the pole keeps rotating rather than hard-stopping, since the
//! widget's rotation math is plain trigonometry with no gimbal singularity
//! to protect against.
//!
//! Terminals only report raw OS key-repeat as repeated Press events, with no
//! true key-up - so "held" is inferred from how recently a Press for that
//! direction arrived (see [`HOLD_GRACE`]). Multiple simultaneous arrows work
//! because each physical key repeats independently; the events just
//! interleave in the same queue, which is why every queued event is drained
//! each frame instead of reading one per frame.

use std::{
    io,
    time::{Duration, Instant},
};

use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{
    Terminal,
    backend::CrosstermBackend,
    layout::Rect,
    style::{Color, Modifier, Style},
    text::Line,
    widgets::{Block, Borders, Clear, Paragraph, Wrap},
};
use vzor::{Bridge, BridgeCommand, BridgeEvent, BridgeMode, Camera, Globe, MapData};

const TAU: f32 = std::f32::consts::TAU;
const TARGET_FPS: u64 = 30;
/// Must match `BRIDGE_HOST`/`BRIDGE_PORT` in tsup/config.py.
const BRIDGE_HOST: &str = "127.0.0.1";
const BRIDGE_PORT: u16 = 8765;
/// Auto mode's placeholder spin rate while disconnected, until a real
/// `STATE` broadcast takes over.
const AUTO_RADIANS_PER_SECOND: f32 = TAU / 25.0;
/// Manual mode's local steering rate while disconnected (radians/sec).
const MANUAL_RADIANS_PER_SECOND: f32 = 1.0;
/// Rate for local command-driven eases (home/lon/lat/goto) while
/// disconnected - deliberately slower than manual steering.
const EASE_RADIANS_PER_SECOND: f32 = 0.6;
/// Once within this many radians of the target on both axes, an ease snaps
/// exactly there.
const EASE_EPSILON: f32 = 0.01;
/// Real-world steering rate (degrees/sec) sent to tsup as `PAN` while an
/// arrow key is held and connected - independent of the local simulation's
/// radians-based rate above.
const PAN_DEGREES_PER_SECOND: f32 = 30.0;
/// A direction counts as "held" if a Press for it arrived within this
/// window. Tune up if release still feels laggy on a given terminal.
const HOLD_GRACE: Duration = Duration::from_millis(150);
const ZOOM_STEP: f32 = 1.15;
const ZOOM_MIN: f32 = 0.5;
const ZOOM_MAX: f32 = 16.0;
/// How long a command-line status/error message stays visible.
const STATUS_TTL: Duration = Duration::from_millis(2500);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Mode {
    Auto,
    Manual,
}

impl Mode {
    fn to_bridge(self) -> BridgeMode {
        match self {
            Self::Auto => BridgeMode::Auto,
            Self::Manual => BridgeMode::Manual,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Overlay {
    Info,
    Help,
}

const INFO_TEXT: &str = "\
Vzor - named for \u{0412}\u{0437}\u{043e}\u{0440} (\"Sight\"), the periscope-style optical \
sighting instrument flown alongside the real Globus INK aboard Soyuz, \
used for manual visual reference alongside the automated dead-\
reckoning globe.

This terminal is the human-facing control surface for this project's \
physical Globus INK replica: tsup (\u{0426}\u{0423}\u{041f}, Mission Control) computes \
orbits and drives the globe; ink (\u{0418}\u{041d}\u{041a}) is the Arduino firmware \
that turns wheel commands into motion. Vzor is where a person looks \
and steers - mirroring the real split between automated tracking and \
manual sighting on the original spacecraft.

Press any key to close.";

const HELP_TEXT: &str = "\
Keys: Tab mode - Arrows steer (Manual) - PgUp/PgDn zoom
Home center - End quit - / command line

Commands (Enter to run, Esc to cancel):
  home               same as the Home key
  end                same as the End key
  mode [auto|manual] toggle, or set explicitly
  zin / zout         same as PageUp/PageDown
  lon <deg>          go to longitude, keep latitude
  lat <deg>          go to latitude, keep longitude
  goto <lon> <lat>   go to both
  track <id>         switch tracked satellite (name or NORAD id)
  info               about this terminal
  help               this list

Press any key to close.";

#[derive(Debug, Default)]
struct HeldKeys {
    left: Option<Instant>,
    right: Option<Instant>,
    up: Option<Instant>,
    down: Option<Instant>,
}

#[derive(Debug)]
struct ViewState {
    mode: Mode,
    /// Target (yaw, pitch) currently being eased toward - only used by the
    /// local fallback simulation while disconnected. Set by Home or by the
    /// home/lon/lat/goto commands, cleared once reached or by any arrow key.
    ease_target: Option<(f32, f32)>,
    yaw: f32,
    pitch: f32,
    zoom: f32,
    held: HeldKeys,
    last_tick: Instant,
    /// Set by the "/" command line; None when it isn't open.
    command_input: Option<String>,
    /// Last command-line feedback message and when it was set; faded out
    /// after [`STATUS_TTL`].
    status: Option<(String, Instant)>,
    /// While connected, mirrors tsup's own `STATE` broadcasts. While
    /// disconnected, only set locally by the `track` command as a display
    /// placeholder.
    tracked_satellite: Option<String>,
    /// Set by the `info`/`help` commands; dismissed by any key press.
    overlay: Option<Overlay>,
    /// True from the moment the bridge thread reports a live connection
    /// until it reports a disconnect - see the module doc comment's
    /// "honesty" note.
    connected: bool,
}

impl ViewState {
    fn new(now: Instant) -> Self {
        Self {
            mode: Mode::Auto,
            ease_target: None,
            yaw: 0.0,
            pitch: 0.0,
            zoom: 1.0,
            held: HeldKeys::default(),
            last_tick: now,
            command_input: None,
            status: None,
            tracked_satellite: None,
            overlay: None,
            connected: false,
        }
    }
}

fn main() -> io::Result<()> {
    let map = MapData::embedded();
    let bridge = Bridge::connect(BRIDGE_HOST, BRIDGE_PORT);
    let mut stdout = io::stdout();
    enable_raw_mode()?;
    execute!(stdout, EnterAlternateScreen)?;
    let mut term = Terminal::new(CrosstermBackend::new(stdout))?;
    let result = run(&mut term, &map, &bridge);
    disable_raw_mode()?;
    execute!(term.backend_mut(), LeaveAlternateScreen)?;
    term.show_cursor()?;
    result
}

fn run(term: &mut Terminal<CrosstermBackend<io::Stdout>>, map: &MapData, bridge: &Bridge) -> io::Result<()> {
    let frame_dt = Duration::from_millis(1000 / TARGET_FPS);
    let mut state = ViewState::new(Instant::now());
    loop {
        let now = Instant::now();
        let dt = now.duration_since(state.last_tick).as_secs_f32();
        state.last_tick = now;

        for event in bridge.poll_events() {
            apply_bridge_event(&mut state, event);
        }

        // Real STATE broadcasts drive yaw/pitch while connected; the local
        // simulation only runs as the disconnected fallback (see module docs).
        if !state.connected {
            update(&mut state, now, dt);
        }
        maybe_send_pan(&state, bridge, now);

        let camera = Camera {
            yaw: state.yaw,
            pitch: state.pitch,
            zoom: state.zoom,
        };
        let (lat, lon) = center_latlon(state.yaw, state.pitch);
        let bottom_left = match (&state.command_input, &state.status) {
            (Some(buf), _) => format!("/{buf}_"),
            (None, Some((msg, t))) if now.duration_since(*t) < STATUS_TTL => msg.clone(),
            _ => String::new(),
        };
        term.draw(|f| {
            let area = f.area();
            let mode_label = match state.mode {
                Mode::Auto => "auto".to_string(),
                Mode::Manual if state.ease_target.is_some() => "calibrating...".to_string(),
                Mode::Manual => "manual".to_string(),
            };
            let title = format!(" Vzor Terminal - {mode_label} - home - end ");
            // The disconnected badge takes priority over the tracking badge
            // in the same corner - while disconnected, "tracking X" would
            // itself be a simulated claim, which is exactly what this
            // indicator exists to never show unmarked.
            let status_badge = if !state.connected {
                " DISCONNECTED - simulated ".to_string()
            } else if let Some(id) = &state.tracked_satellite {
                format!(" tracking {id} ")
            } else {
                String::new()
            };
            let block = Block::default()
                .borders(Borders::ALL)
                .title_top(Line::from(title).left_aligned())
                .title_top(Line::from(status_badge).right_aligned())
                .title_bottom(Line::from(bottom_left).left_aligned())
                .title_bottom(Line::from(format_latlon(lat, lon)).right_aligned());
            let inner = block.inner(area);
            f.render_widget(block, area);
            f.render_widget(Globe::new(map, camera), inner);
            draw_crosshair(f.buffer_mut(), inner);
            if let Some(overlay) = state.overlay {
                draw_overlay(f, overlay);
            }
        })?;

        // Drain every queued event before the next frame. Reading only one
        // per iteration let a burst of key-repeat events (which can arrive
        // faster than the frame period) pile up in the queue and keep
        // getting processed well after the physical key was released.
        while event::poll(Duration::ZERO)? {
            if let Event::Key(k) = event::read()?
                && k.kind == KeyEventKind::Press
                && handle_key(&mut state, bridge, k.code, k.modifiers, now)
            {
                return Ok(());
            }
        }
        // Idle-wait for the rest of the frame so we're not busy-polling.
        event::poll(frame_dt.saturating_sub(now.elapsed()))?;
    }
}

/// Applies one bridge connection/state event to `state`.
fn apply_bridge_event(state: &mut ViewState, event: BridgeEvent) {
    match event {
        BridgeEvent::Connected => {
            state.connected = true;
            // Local easing is suspended while connected (see `update`), but
            // any target left over from just before connecting would
            // otherwise sit stale and then resume driving the simulation
            // from an outdated position if the connection later drops.
            state.ease_target = None;
        }
        BridgeEvent::Disconnected => state.connected = false,
        BridgeEvent::State(s) => {
            state.yaw = lon_to_yaw(s.lon);
            state.pitch = lat_to_pitch(s.lat);
            state.tracked_satellite = s.satellite;
            state.mode = match s.mode.as_str() {
                "MANUAL" => Mode::Manual,
                _ => Mode::Auto,
            };
        }
    }
}

/// Sends a `PAN` command for the currently-held arrow keys, every frame
/// they're held (matching tsup's own PAN watchdog: silence means stop, so
/// this can't be a send-once-on-press event). No-op while disconnected or
/// not in Manual - there's nothing live to steer.
fn maybe_send_pan(state: &ViewState, bridge: &Bridge, now: Instant) {
    if !state.connected || state.mode != Mode::Manual {
        return;
    }
    let is_held = |t: Option<Instant>| t.is_some_and(|t| now.duration_since(t) < HOLD_GRACE);
    let mut lat_rate = 0.0;
    let mut lon_rate = 0.0;
    if is_held(state.held.up) {
        lat_rate += PAN_DEGREES_PER_SECOND;
    }
    if is_held(state.held.down) {
        lat_rate -= PAN_DEGREES_PER_SECOND;
    }
    // Right/Left map to -/+ longitude, matching this crate's existing
    // yaw<->longitude convention (`lon_to_yaw` negates) so connected and
    // locally-simulated steering feel identical to the same key presses.
    if is_held(state.held.right) {
        lon_rate -= PAN_DEGREES_PER_SECOND;
    }
    if is_held(state.held.left) {
        lon_rate += PAN_DEGREES_PER_SECOND;
    }
    if lat_rate != 0.0 || lon_rate != 0.0 {
        bridge.send(BridgeCommand::Pan { lat_rate, lon_rate });
    }
}

/// Applies a key press to `state`. Returns `true` if the program should quit.
fn handle_key(
    state: &mut ViewState,
    bridge: &Bridge,
    code: KeyCode,
    modifiers: KeyModifiers,
    now: Instant,
) -> bool {
    if code == KeyCode::Char('c') && modifiers.contains(KeyModifiers::CONTROL) {
        return true;
    }
    if state.overlay.is_some() {
        state.overlay = None;
        return false;
    }
    if state.command_input.is_some() {
        match code {
            KeyCode::Esc => state.command_input = None,
            KeyCode::Enter => {
                let cmd = state.command_input.take().unwrap_or_default();
                return execute_command(state, bridge, &cmd, now);
            }
            KeyCode::Backspace => {
                if let Some(buf) = state.command_input.as_mut() {
                    buf.pop();
                }
            }
            KeyCode::Char(c) => {
                if let Some(buf) = state.command_input.as_mut() {
                    buf.push(c);
                }
            }
            _ => {}
        }
        return false;
    }
    match code {
        KeyCode::End => return true,
        KeyCode::Esc => return true,
        KeyCode::Char('/') => state.command_input = Some(String::new()),
        KeyCode::Tab => {
            state.mode = match state.mode {
                Mode::Auto => Mode::Manual,
                Mode::Manual => Mode::Auto,
            };
            state.ease_target = None;
            if state.connected {
                bridge.send(BridgeCommand::Mode(state.mode.to_bridge()));
            }
        }
        KeyCode::PageUp => state.zoom = (state.zoom * ZOOM_STEP).min(ZOOM_MAX),
        KeyCode::PageDown => state.zoom = (state.zoom / ZOOM_STEP).max(ZOOM_MIN),
        KeyCode::Home if state.mode == Mode::Manual => {
            state.tracked_satellite = None;
            if state.connected {
                bridge.send(BridgeCommand::Goto { lat: 0.0, lon: 0.0 });
            } else {
                state.ease_target = Some((0.0, 0.0));
            }
        }
        KeyCode::Left if state.mode == Mode::Manual => {
            state.ease_target = None;
            state.held.left = Some(now);
            state.tracked_satellite = None;
        }
        KeyCode::Right if state.mode == Mode::Manual => {
            state.ease_target = None;
            state.held.right = Some(now);
            state.tracked_satellite = None;
        }
        KeyCode::Up if state.mode == Mode::Manual => {
            state.ease_target = None;
            state.held.up = Some(now);
            state.tracked_satellite = None;
        }
        KeyCode::Down if state.mode == Mode::Manual => {
            state.ease_target = None;
            state.held.down = Some(now);
            state.tracked_satellite = None;
        }
        _ => {}
    }
    false
}

/// Parses and runs one command-line entry. Returns `true` if the program
/// should quit. Unrecognized commands or bad arguments set `state.status`
/// with a usage/error message instead of taking any action.
fn execute_command(state: &mut ViewState, bridge: &Bridge, input: &str, now: Instant) -> bool {
    let mut parts = input.split_whitespace();
    let Some(cmd) = parts.next() else {
        return false;
    };
    let args: Vec<&str> = parts.collect();
    match cmd {
        "home" => {
            state.mode = Mode::Manual;
            state.tracked_satellite = None;
            if state.connected {
                bridge.send(BridgeCommand::Goto { lat: 0.0, lon: 0.0 });
            } else {
                state.ease_target = Some((0.0, 0.0));
            }
        }
        "end" => return true,
        "mode" => {
            let new_mode = match args.first().copied() {
                None => Some(match state.mode {
                    Mode::Auto => Mode::Manual,
                    Mode::Manual => Mode::Auto,
                }),
                Some("auto") => Some(Mode::Auto),
                Some("manual") => Some(Mode::Manual),
                Some(other) => {
                    state.status = Some((format!("mode: unknown '{other}' (use auto/manual)"), now));
                    None
                }
            };
            if let Some(new_mode) = new_mode {
                state.mode = new_mode;
                if state.connected {
                    bridge.send(BridgeCommand::Mode(new_mode.to_bridge()));
                }
            }
        }
        "zin" => state.zoom = (state.zoom * ZOOM_STEP).min(ZOOM_MAX),
        "zout" => state.zoom = (state.zoom / ZOOM_STEP).max(ZOOM_MIN),
        "lon" => match args.first().and_then(|s| s.parse::<f32>().ok()) {
            Some(lon) => {
                state.mode = Mode::Manual;
                state.tracked_satellite = None;
                if state.connected {
                    let (lat, _) = center_latlon(state.yaw, state.pitch);
                    bridge.send(BridgeCommand::Goto { lat, lon });
                } else {
                    state.ease_target = Some((lon_to_yaw(lon), state.pitch));
                }
            }
            None => state.status = Some(("lon: usage: lon <degrees>".to_string(), now)),
        },
        "lat" => match args.first().and_then(|s| s.parse::<f32>().ok()) {
            Some(lat) => {
                state.mode = Mode::Manual;
                state.tracked_satellite = None;
                if state.connected {
                    let (_, lon) = center_latlon(state.yaw, state.pitch);
                    bridge.send(BridgeCommand::Goto { lat, lon });
                } else {
                    state.ease_target = Some((state.yaw, lat_to_pitch(lat)));
                }
            }
            None => state.status = Some(("lat: usage: lat <degrees>".to_string(), now)),
        },
        "goto" => {
            let lon = args.first().and_then(|s| s.parse::<f32>().ok());
            let lat = args.get(1).and_then(|s| s.parse::<f32>().ok());
            match (lon, lat) {
                (Some(lon), Some(lat)) => {
                    state.mode = Mode::Manual;
                    state.tracked_satellite = None;
                    if state.connected {
                        bridge.send(BridgeCommand::Goto { lat, lon });
                    } else {
                        state.ease_target = Some((lon_to_yaw(lon), lat_to_pitch(lat)));
                    }
                }
                _ => {
                    state.status = Some(("goto: usage: goto <lon> <lat>".to_string(), now));
                }
            }
        }
        "track" => match args.first() {
            Some(id) => {
                if state.connected {
                    bridge.send(BridgeCommand::Track((*id).to_string()));
                    state.status = Some((format!("requested track '{id}'"), now));
                } else {
                    state.tracked_satellite = Some((*id).to_string());
                    state.status = Some((
                        format!("tracking '{id}' (simulated - not connected to tsup)"),
                        now,
                    ));
                }
            }
            None => state.status = Some(("track: usage: track <satellite id>".to_string(), now)),
        },
        "info" => state.overlay = Some(Overlay::Info),
        "help" => state.overlay = Some(Overlay::Help),
        other => state.status = Some((format!("unknown command: {other}"), now)),
    }
    false
}

fn lon_to_yaw(lon_deg: f32) -> f32 {
    (-lon_deg.to_radians()).rem_euclid(TAU)
}

fn lat_to_pitch(lat_deg: f32) -> f32 {
    lat_deg.to_radians().rem_euclid(TAU)
}

/// Advances auto-spin, manual steering, and command-driven easing by one
/// frame of `dt`. Only runs while disconnected - see the module doc comment.
fn update(state: &mut ViewState, now: Instant, dt: f32) {
    match state.mode {
        Mode::Auto => {
            state.yaw = (state.yaw + dt * AUTO_RADIANS_PER_SECOND).rem_euclid(TAU);
        }
        Mode::Manual => {
            if let Some((target_yaw, target_pitch)) = state.ease_target {
                let step = dt * EASE_RADIANS_PER_SECOND;
                let yaw_delta = shortest_delta(state.yaw, target_yaw);
                let pitch_delta = shortest_delta(state.pitch, target_pitch);
                if yaw_delta.abs() <= EASE_EPSILON && pitch_delta.abs() <= EASE_EPSILON {
                    state.yaw = target_yaw.rem_euclid(TAU);
                    state.pitch = target_pitch.rem_euclid(TAU);
                    state.ease_target = None;
                } else {
                    state.yaw = (state.yaw + step.min(yaw_delta.abs()) * yaw_delta.signum())
                        .rem_euclid(TAU);
                    state.pitch = (state.pitch
                        + step.min(pitch_delta.abs()) * pitch_delta.signum())
                    .rem_euclid(TAU);
                }
            } else {
                let is_held =
                    |t: Option<Instant>| t.is_some_and(|t| now.duration_since(t) < HOLD_GRACE);
                let step = dt * MANUAL_RADIANS_PER_SECOND;
                // Independent checks (not else-if), so opposite arrows held
                // together (e.g. Up+Left) move both axes at once - diagonal.
                if is_held(state.held.left) {
                    state.yaw = (state.yaw - step).rem_euclid(TAU);
                }
                if is_held(state.held.right) {
                    state.yaw = (state.yaw + step).rem_euclid(TAU);
                }
                if is_held(state.held.up) {
                    state.pitch = (state.pitch + step).rem_euclid(TAU);
                }
                if is_held(state.held.down) {
                    state.pitch = (state.pitch - step).rem_euclid(TAU);
                }
            }
        }
    }
}

/// Shortest signed delta (magnitude <= PI) to add to `from` (a `[0, TAU)`
/// angle) to reach the nearest equivalent of `to`.
fn shortest_delta(from: f32, to: f32) -> f32 {
    let diff = (to - from).rem_euclid(TAU);
    if diff <= std::f32::consts::PI {
        diff
    } else {
        diff - TAU
    }
}

/// Latitude/longitude (degrees) of the point at the dead center of the view,
/// derived by inverting `vzor`'s yaw-then-pitch rotation against its
/// documented sphere convention (`x=cos(lat)sin(lon)`, `y=sin(lat)`,
/// `z=cos(lat)cos(lon)`). Valid for any yaw/pitch, including past the poles:
/// `asin`/`atan2` fold the latitude back down and flip the longitude by 180
/// degrees exactly as a physical crossing of the pole would.
fn center_latlon(yaw: f32, pitch: f32) -> (f32, f32) {
    let y = pitch.sin();
    let x = -yaw.sin() * pitch.cos();
    let z = yaw.cos() * pitch.cos();
    (y.asin().to_degrees(), x.atan2(z).to_degrees())
}

fn format_latlon(lat_deg: f32, lon_deg: f32) -> String {
    let lat_hemi = if lat_deg >= 0.0 { 'N' } else { 'S' };
    let lon_hemi = if lon_deg >= 0.0 { 'E' } else { 'W' };
    format!(
        "{:.1}°{lat_hemi} {:.1}°{lon_hemi} ",
        lat_deg.abs(),
        lon_deg.abs()
    )
}

/// Returns a `width`x`height` rect centered within `area`, clamped to fit.
fn centered_rect(area: Rect, width: u16, height: u16) -> Rect {
    let width = width.min(area.width);
    let height = height.min(area.height);
    Rect {
        x: area.x + (area.width - width) / 2,
        y: area.y + (area.height - height) / 2,
        width,
        height,
    }
}

/// Renders `overlay`'s text in a bordered box centered over the globe.
fn draw_overlay(f: &mut ratatui::Frame<'_>, overlay: Overlay) {
    let (title, text) = match overlay {
        Overlay::Info => (" Vzor ", INFO_TEXT),
        Overlay::Help => (" Help ", HELP_TEXT),
    };
    let area = centered_rect(f.area(), 64, 16);
    let block = Block::bordered().title(title);
    let inner = block.inner(area);
    f.render_widget(Clear, area);
    f.render_widget(block, area);
    f.render_widget(Paragraph::new(text).wrap(Wrap { trim: true }), inner);
}

/// Marks the dead center of `area` with a single "+".
fn draw_crosshair(buf: &mut ratatui::buffer::Buffer, area: Rect) {
    if area.width == 0 || area.height == 0 {
        return;
    }
    let cx = area.x + area.width / 2;
    let cy = area.y + area.height / 2;
    buf[(cx, cy)].set_symbol("+").set_style(
        Style::default()
            .fg(Color::White)
            .add_modifier(Modifier::BOLD),
    );
}
