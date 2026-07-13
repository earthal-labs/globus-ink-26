// SPDX-License-Identifier: GPL-3.0-or-later

//! Blocking TCP client for the tsup bridge (see docs/bridge-protocol.md).
//! Connects and reconnects on its own background threads, handing events
//! to the render loop over a channel - a stalled or dropped connection can
//! never block rendering. No async runtime: fits this crate's
//! `unused_async` lint.

use std::{
    io::{BufRead, BufReader, Write},
    net::TcpStream,
    sync::mpsc::{self, Receiver, RecvTimeoutError, Sender},
    thread,
    time::Duration,
};

const RECONNECT_DELAY: Duration = Duration::from_millis(500);
/// How often the write loop wakes on its own, so it can notice the reader
/// thread closing the connection even with no outbound command pending.
const WRITE_LOOP_POLL: Duration = Duration::from_millis(200);

/// State broadcast by tsup, parsed from one `STATE` line.
#[derive(Debug, Clone, PartialEq)]
pub struct BridgeState {
    pub lat: f32,
    pub lon: f32,
    pub satellite: Option<String>,
    pub mode: String, // "AUTO" | "MANUAL", passed through as tsup sends it
}

/// Connection-level events surfaced to the render loop.
#[derive(Debug, Clone)]
pub enum BridgeEvent {
    Connected,
    Disconnected,
    State(BridgeState),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BridgeMode {
    Auto,
    Manual,
}

/// Commands the render loop can send toward tsup.
#[derive(Debug, Clone)]
pub enum BridgeCommand {
    Goto { lat: f32, lon: f32 },
    Pan { lat_rate: f32, lon_rate: f32 },
    Track(String),
    Mode(BridgeMode),
}

impl BridgeCommand {
    fn to_line(&self) -> String {
        match self {
            Self::Goto { lat, lon } => format!("GOTO {lat} {lon}\n"),
            Self::Pan { lat_rate, lon_rate } => format!("PAN {lat_rate} {lon_rate}\n"),
            Self::Track(token) => format!("TRACK {token}\n"),
            Self::Mode(BridgeMode::Auto) => "MODE AUTO\n".to_string(),
            Self::Mode(BridgeMode::Manual) => "MODE MANUAL\n".to_string(),
        }
    }
}

/// A handle to the background connect/reconnect thread. There is no
/// explicit shutdown - it runs for the process's lifetime, same as this
/// being a TUI app whose process exit tears down its threads regardless.
pub struct Bridge {
    events: Receiver<BridgeEvent>,
    commands: Sender<BridgeCommand>,
}

impl Bridge {
    /// Spawns the background connect/reconnect thread and returns a handle.
    #[must_use]
    pub fn connect(host: &str, port: u16) -> Self {
        let (event_tx, event_rx) = mpsc::channel();
        let (command_tx, command_rx) = mpsc::channel();
        let host = host.to_string();
        thread::spawn(move || run(&host, port, &event_tx, &command_rx));
        Self {
            events: event_rx,
            commands: command_tx,
        }
    }

    /// Drains every event that's arrived since the last call - never blocks.
    pub fn poll_events(&self) -> Vec<BridgeEvent> {
        let mut out = Vec::new();
        while let Ok(event) = self.events.try_recv() {
            out.push(event);
        }
        out
    }

    /// Queues a command for the writer thread - never blocks the caller.
    /// Silently dropped if the background thread has gone away.
    pub fn send(&self, command: BridgeCommand) {
        let _ = self.commands.send(command);
    }
}

fn run(host: &str, port: u16, events: &Sender<BridgeEvent>, commands: &Receiver<BridgeCommand>) {
    loop {
        if let Ok(stream) = TcpStream::connect((host, port)) {
            let _ = stream.set_nodelay(true);
            let _ = events.send(BridgeEvent::Connected);
            serve_connection(stream, events, commands);
            let _ = events.send(BridgeEvent::Disconnected);
        }
        thread::sleep(RECONNECT_DELAY);
    }
}

/// Runs one connection's read/write loops until either side drops it, then
/// returns so the caller can retry.
fn serve_connection(stream: TcpStream, events: &Sender<BridgeEvent>, commands: &Receiver<BridgeCommand>) {
    let Ok(read_stream) = stream.try_clone() else {
        return;
    };
    let mut write_stream = stream;

    // Reader owns its own thread (blocking on socket reads); this thread
    // drives the writer. `closed_tx` is how the reader tells the writer the
    // connection died, so the writer isn't stuck in recv_timeout() forever.
    let (closed_tx, closed_rx) = mpsc::channel::<()>();
    let reader_events = events.clone();
    let reader = thread::spawn(move || {
        let mut lines = BufReader::new(read_stream).lines();
        while let Some(Ok(line)) = lines.next() {
            if let Some(state) = parse_state(&line) {
                let _ = reader_events.send(BridgeEvent::State(state));
            }
        }
        let _ = closed_tx.send(());
    });

    loop {
        match commands.recv_timeout(WRITE_LOOP_POLL) {
            Ok(command) => {
                if write_stream.write_all(command.to_line().as_bytes()).is_err() {
                    break;
                }
            }
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => break, // Bridge handle dropped
        }
        if closed_rx.try_recv().is_ok() {
            break;
        }
    }

    let _ = write_stream.shutdown(std::net::Shutdown::Both);
    let _ = reader.join();
}

fn parse_state(line: &str) -> Option<BridgeState> {
    let mut parts = line.split_whitespace();
    if parts.next()? != "STATE" {
        return None;
    }
    let lat: f32 = parts.next()?.parse().ok()?;
    let lon: f32 = parts.next()?.parse().ok()?;
    let sat = parts.next()?;
    let mode = parts.next()?.to_string();
    let satellite = if sat == "-" { None } else { Some(sat.to_string()) };
    Some(BridgeState { lat, lon, satellite, mode })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_well_formed_state_line() {
        let s = parse_state("STATE 12.5 -45.25 ISS AUTO").unwrap();
        assert_eq!(s.lat, 12.5);
        assert_eq!(s.lon, -45.25);
        assert_eq!(s.satellite.as_deref(), Some("ISS"));
        assert_eq!(s.mode, "AUTO");
    }

    #[test]
    fn dash_satellite_becomes_none() {
        let s = parse_state("STATE 0 0 - MANUAL").unwrap();
        assert_eq!(s.satellite, None);
    }

    #[test]
    fn rejects_non_state_lines() {
        assert!(parse_state("HELLO world").is_none());
    }

    #[test]
    fn rejects_short_lines() {
        assert!(parse_state("STATE 1 2").is_none());
    }

    #[test]
    fn formats_goto_command() {
        let line = BridgeCommand::Goto { lat: 1.5, lon: -2.5 }.to_line();
        assert_eq!(line, "GOTO 1.5 -2.5\n");
    }

    #[test]
    fn formats_pan_command() {
        let line = BridgeCommand::Pan { lat_rate: 10.0, lon_rate: -10.0 }.to_line();
        assert_eq!(line, "PAN 10 -10\n");
    }

    #[test]
    fn formats_track_command() {
        assert_eq!(BridgeCommand::Track("ISS".to_string()).to_line(), "TRACK ISS\n");
    }

    #[test]
    fn formats_mode_command() {
        assert_eq!(BridgeCommand::Mode(BridgeMode::Auto).to_line(), "MODE AUTO\n");
        assert_eq!(BridgeCommand::Mode(BridgeMode::Manual).to_line(), "MODE MANUAL\n");
    }
}
