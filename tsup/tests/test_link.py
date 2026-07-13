"""
Tests for link.py: the serial handshake and message-formatting logic,
against a mocked Serial connection - no real hardware involved. Run from
anywhere with:
    python -m unittest discover -s tsup/tests -t tsup

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from numpy import array

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import link


def fake_serial(hello_line):
    """A stand-in for a pyserial Serial connection whose first readline()
    returns `hello_line` (bytes)."""
    conn = MagicMock()
    conn.readline.return_value = hello_line
    return conn


class TestOpenLink(unittest.TestCase):
    def setUp(self):
        # open_link() really calls time.sleep(SERIAL_BOOT_WAIT_S) - don't
        # actually wait 2s per test.
        patcher = patch("link.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_correct_hello_returns_connection(self):
        fake = fake_serial(b"ink p0\n")
        with patch("link.Serial", return_value=fake):
            conn = link.open_link()
        self.assertIs(conn, fake)

    def test_uses_config_defaults_when_not_given(self):
        fake = fake_serial(b"ink p0\n")
        with patch("link.Serial", return_value=fake) as mock_serial:
            link.open_link()
        args, _ = mock_serial.call_args
        self.assertEqual(args[0], link.SERIAL_PORT)
        self.assertEqual(args[1], link.SERIAL_BAUD)

    def test_explicit_port_and_baud_override_config(self):
        fake = fake_serial(b"ink p0\n")
        with patch("link.Serial", return_value=fake) as mock_serial:
            link.open_link(port="COM7", baud=9600)
        args, _ = mock_serial.call_args
        self.assertEqual(args[0], "COM7")
        self.assertEqual(args[1], 9600)

    def test_sets_an_explicit_timeout(self):
        # pyserial defaults to timeout=None (blocks forever) - open_link
        # must always pass a real one instead.
        fake = fake_serial(b"ink p0\n")
        with patch("link.Serial", return_value=fake) as mock_serial:
            link.open_link()
        _, kwargs = mock_serial.call_args
        self.assertIsNotNone(kwargs.get("timeout"))

    def test_sends_a_version_query(self):
        # open_link() actively asks rather than relying on catching ink's
        # one-shot boot hello at exactly the right moment (unreliable on
        # native-USB boards, which drop the connection on reset).
        fake = fake_serial(b"ink p0\n")
        with patch("link.Serial", return_value=fake):
            link.open_link()
        fake.write.assert_called_once_with(b"P\n")

    def test_clears_stale_input_before_querying(self):
        # A stray hello from setup() may already be sitting in the buffer -
        # discard it so the next readline() is definitely the query response.
        fake = fake_serial(b"ink p0\n")
        with patch("link.Serial", return_value=fake):
            link.open_link()
        self.assertTrue(fake.reset_input_buffer.called)

    def test_version_mismatch_raises_and_closes(self):
        fake = fake_serial(b"ink p1\n")
        with patch("link.Serial", return_value=fake):
            with self.assertRaises(RuntimeError):
                link.open_link()
        self.assertTrue(fake.close.called)

    def test_garbage_boot_line_raises_and_closes(self):
        fake = fake_serial(b"not a hello\n")
        with patch("link.Serial", return_value=fake):
            with self.assertRaises(RuntimeError):
                link.open_link()
        self.assertTrue(fake.close.called)

    def test_empty_read_raises(self):
        # readline() returns b"" on a real timeout with nothing received.
        fake = fake_serial(b"")
        with patch("link.Serial", return_value=fake):
            with self.assertRaises(RuntimeError):
                link.open_link()


class TestSendRates(unittest.TestCase):
    def test_formats_protocol_message(self):
        conn = MagicMock()
        link.send_rates(conn, [10, -5, 0])
        conn.write.assert_called_once_with(b"V 10 -5 0\n")

    def test_accepts_numpy_int_array(self):
        # wheel_rates() (kinematics.py) actually returns this type in real
        # use, not a plain list - confirm formatting still comes out right.
        conn = MagicMock()
        link.send_rates(conn, array([1, -2, 3]))
        conn.write.assert_called_once_with(b"V 1 -2 3\n")

    def test_all_zero_stop_command(self):
        # main.py's shutdown path sends exactly this.
        conn = MagicMock()
        link.send_rates(conn, [0, 0, 0])
        conn.write.assert_called_once_with(b"V 0 0 0\n")


if __name__ == "__main__":
    unittest.main()
