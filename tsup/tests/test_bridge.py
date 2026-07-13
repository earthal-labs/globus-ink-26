"""
Tests for bridge.py: the vzor<->tsup TCP bridge. Covers the line
parser/PAN watchdog directly, plus an end-to-end pass over a real loopback
socket (ephemeral port - no fixed port, no external network). Run from
anywhere with:
    python -m unittest discover -s tsup/tests -t tsup

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import socket
import sys
import time
import unittest
from pathlib import Path
from queue import Empty
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridge as bridge_module
from bridge import Bridge


def drain(q, timeout=1.0):
    """Pop one item from a Queue - raises Empty (rather than hanging the
    test) if nothing shows up within `timeout`."""
    return q.get(timeout=timeout)


class TestHandleLine(unittest.TestCase):
    def setUp(self):
        self.br = Bridge(load_satellite_fn=lambda norad_id: f"sat-{norad_id}")

    def test_goto_queues_command(self):
        self.br._handle_line("GOTO 12.5 -45.25")
        self.assertEqual(self.br.commands.get_nowait(), ("GOTO", 12.5, -45.25))

    def test_goto_malformed_is_ignored(self):
        self.br._handle_line("GOTO not-a-number 1")
        with self.assertRaises(Empty):
            self.br.commands.get_nowait()

    def test_mode_queues_command(self):
        self.br._handle_line("MODE MANUAL")
        self.assertEqual(self.br.commands.get_nowait(), ("MODE", "MANUAL"))

    def test_mode_rejects_unknown_value(self):
        self.br._handle_line("MODE SIDEWAYS")
        with self.assertRaises(Empty):
            self.br.commands.get_nowait()

    def test_unknown_command_is_ignored(self):
        self.br._handle_line("NONSENSE 1 2 3")
        with self.assertRaises(Empty):
            self.br.commands.get_nowait()

    def test_blank_line_is_ignored(self):
        self.br._handle_line("")
        with self.assertRaises(Empty):
            self.br.commands.get_nowait()

    def test_track_by_known_name(self):
        self.br._handle_line("TRACK ISS")
        self.assertEqual(self.br.commands.get_nowait(), ("TRACK_READY", "ISS", "sat-25544"))

    def test_track_by_raw_norad_id(self):
        self.br._handle_line("TRACK 12345")
        self.assertEqual(self.br.commands.get_nowait(), ("TRACK_READY", "12345", "sat-12345"))

    def test_track_unknown_token_errors(self):
        self.br._handle_line("TRACK not-a-satellite")
        tag, message = self.br.commands.get_nowait()
        self.assertEqual(tag, "TRACK_ERROR")
        self.assertIn("not-a-satellite", message)

    def test_track_loader_failure_errors(self):
        def failing_loader(norad_id):
            raise RuntimeError("no network")

        br = Bridge(load_satellite_fn=failing_loader)
        br._handle_line("TRACK ISS")
        tag, message = br.commands.get_nowait()
        self.assertEqual(tag, "TRACK_ERROR")
        self.assertIn("no network", message)


class TestPanWatchdog(unittest.TestCase):
    def setUp(self):
        self.br = Bridge(load_satellite_fn=lambda norad_id: None)

    def test_fresh_pan_is_returned(self):
        self.br._handle_line("PAN 1.5 -2.0")
        self.assertEqual(self.br.pan_rate(), (1.5, -2.0))

    def test_stale_pan_reads_as_zero(self):
        # Silence must mean stop - same reasoning as ink.ino's watchdog.
        self.br._handle_line("PAN 1.5 -2.0")
        with patch.object(bridge_module.time, "monotonic", return_value=time.monotonic() + 10):
            self.assertEqual(self.br.pan_rate(), (0.0, 0.0))

    def test_no_pan_ever_received_reads_as_zero(self):
        self.assertEqual(self.br.pan_rate(), (0.0, 0.0))


class TestStateBroadcastFormatting(unittest.TestCase):
    def test_set_state_defaults_missing_satellite_to_dash(self):
        br = Bridge(load_satellite_fn=lambda norad_id: None)
        br.set_state(1.0, 2.0, None, "MANUAL")
        self.assertEqual(br._state, (1.0, 2.0, "-", "MANUAL"))


class TestEndToEnd(unittest.TestCase):
    """Exercises the real background thread over a real loopback socket
    (ephemeral port - no fixed port, no external network)."""

    def setUp(self):
        self.br = Bridge(load_satellite_fn=lambda norad_id: f"sat-{norad_id}", port=0)
        self.br.start()
        self.addCleanup(self.br.stop)

        self.client = socket.create_connection(("127.0.0.1", self.br.bound_port), timeout=2)
        self.addCleanup(self.client.close)
        self.client_buf = b""

    def _read_line(self):
        while b"\n" not in self.client_buf:
            self.client_buf += self.client.recv(4096)
        line, self.client_buf = self.client_buf.split(b"\n", 1)
        return line.decode()

    def test_receives_state_broadcast(self):
        self.br.set_state(12.34, -56.78, "ISS", "AUTO")
        self.assertEqual(self._read_line(), "STATE 12.3400 -56.7800 ISS AUTO")

    def test_sent_goto_is_queued(self):
        self.client.sendall(b"GOTO 10 20\n")
        self.assertEqual(drain(self.br.commands), ("GOTO", 10.0, 20.0))

    def test_split_across_two_writes_still_parses(self):
        self.client.sendall(b"GOTO 10")
        time.sleep(0.05)
        self.client.sendall(b" 20\n")
        self.assertEqual(drain(self.br.commands), ("GOTO", 10.0, 20.0))

    def test_new_connection_replaces_old(self):
        second = socket.create_connection(("127.0.0.1", self.br.bound_port), timeout=2)
        self.addCleanup(second.close)
        self.client.settimeout(2)
        # recv() returning b"" is the reliable EOF signal that the bridge
        # closed this connection to replace it - a send may not surface
        # the close immediately over loopback.
        self.assertEqual(self.client.recv(4096), b"")

    def test_disconnect_freezes_pan_and_queues_event(self):
        self.client.sendall(b"PAN 5 5\n")
        # Let the recv loop actually process the PAN line before closing.
        deadline = time.monotonic() + 2
        while self.br.pan_rate() == (0.0, 0.0) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotEqual(self.br.pan_rate(), (0.0, 0.0))

        self.client.close()
        self.assertEqual(drain(self.br.commands, timeout=2), ("DISCONNECTED",))
        self.assertEqual(self.br.pan_rate(), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
