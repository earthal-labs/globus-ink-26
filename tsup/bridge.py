"""
Globus Bridge.

Background-thread TCP server that lets vzor drive the globe manually and
switch tracked satellites at runtime. The socket lives entirely on its own
thread so a slow/stalled client can never block the 10Hz control loop; see
docs/bridge-protocol.md for the wire format and the reasoning.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import socket
import threading
import time
from queue import Queue

from config import BRIDGE_HOST, BRIDGE_PORT, PAN_WATCHDOG_MS, TICK_HZ, SATELLITES

PAN_WATCHDOG_S = PAN_WATCHDOG_MS / 1000


class Bridge:
    """Owns the listening socket and its background accept/serve thread.

    `load_satellite_fn(norad_id)` is injected rather than imported from
    main.py, to dodge a circular import (main.py starts the bridge, the
    bridge resolves TRACK) and to keep this testable without skyfield.
    """

    def __init__(self, load_satellite_fn, host=None, port=None):
        self._load_satellite = load_satellite_fn
        self._host = host or BRIDGE_HOST
        self._port = port or BRIDGE_PORT

        self.commands = Queue()  # discrete events for main.py to drain each tick

        self._pan_lock = threading.Lock()
        self._pan_rate = (0.0, 0.0)
        self._pan_time = 0.0

        self._state_lock = threading.Lock()
        self._state = None  # (lat, lon, sat_name_or_dash, mode); set by main.py each tick

        self._sock = None
        self._conn = None
        self._conn_lock = threading.Lock()
        self._stop = threading.Event()
        self._accept_thread = None

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.listen(1)
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    @property
    def bound_port(self):
        """The actual listening port - useful when constructed with port=0."""
        return self._sock.getsockname()[1]

    def stop(self):
        self._stop.set()
        if self._sock:
            self._sock.close()
        with self._conn_lock:
            if self._conn:
                self._conn.close()

    def set_state(self, lat, lon, satellite_name, mode):
        """Called once per control-loop tick; a lock-protected assignment,
        never blocks on I/O."""
        with self._state_lock:
            self._state = (lat, lon, satellite_name or "-", mode)

    def pan_rate(self):
        """Latest (lat_rate, lon_rate), or (0.0, 0.0) if stale - the PAN
        watchdog. Silence must mean stop, same reasoning as ink.ino's."""
        with self._pan_lock:
            rate, ts = self._pan_rate, self._pan_time
        if time.monotonic() - ts > PAN_WATCHDOG_S:
            return (0.0, 0.0)
        return rate

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # socket closed by stop()

            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._conn_lock:
                if self._conn:
                    self._conn.close()  # single client: replace, don't reject
                self._conn = conn

            # Both loops run on their own threads so this one can loop
            # straight back to accept() - otherwise a still-connected first
            # client would block the second from ever being accepted.
            threading.Thread(target=self._send_loop, args=(conn,), daemon=True).start()
            threading.Thread(target=self._recv_loop, args=(conn,), daemon=True).start()

    def _send_loop(self, conn):
        while not self._stop.is_set():
            with self._state_lock:
                state = self._state
            if state is not None:
                lat, lon, sat_name, mode = state
                line = f"STATE {lat:.4f} {lon:.4f} {sat_name} {mode}\n"
                try:
                    conn.sendall(line.encode())
                except OSError:
                    return
            time.sleep(1 / TICK_HZ)

    def _recv_loop(self, conn):
        try:
            conn.settimeout(1.0)
        except OSError:
            return  # already replaced/closed before this thread got to run

        buf = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break  # client disconnected

            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle_line(line.decode(errors="replace").strip())

        with self._conn_lock:
            if self._conn is conn:
                self._conn = None
        with self._pan_lock:
            self._pan_rate = (0.0, 0.0)  # disconnect freezes any in-flight PAN immediately
            self._pan_time = 0.0
        self.commands.put(("DISCONNECTED",))

    def _handle_line(self, line):
        parts = line.split()
        if not parts:
            return
        cmd = parts[0]

        if cmd == "GOTO" and len(parts) == 3:
            try:
                lat, lon = float(parts[1]), float(parts[2])
            except ValueError:
                return
            self.commands.put(("GOTO", lat, lon))

        elif cmd == "PAN" and len(parts) == 3:
            try:
                lat_rate, lon_rate = float(parts[1]), float(parts[2])
            except ValueError:
                return
            with self._pan_lock:
                self._pan_rate = (lat_rate, lon_rate)
                self._pan_time = time.monotonic()

        elif cmd == "TRACK" and len(parts) == 2:
            self._resolve_and_queue_track(parts[1])

        elif cmd == "MODE" and len(parts) == 2 and parts[1] in ("AUTO", "MANUAL"):
            self.commands.put(("MODE", parts[1]))

    def _resolve_and_queue_track(self, token):
        """Runs on this (background) thread deliberately - a TLE fetch is a
        synchronous network call that must never reach the control loop."""
        norad_id = SATELLITES.get(token)
        name = token
        if norad_id is None:
            try:
                norad_id = int(token)
            except ValueError:
                self.commands.put(("TRACK_ERROR", f"unknown satellite: {token!r}"))
                return

        try:
            satellite = self._load_satellite(norad_id)
        except Exception as exc:
            self.commands.put(("TRACK_ERROR", str(exc)))
            return

        self.commands.put(("TRACK_READY", name, satellite))
