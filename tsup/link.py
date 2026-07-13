"""
Globus Link.

Serial connection to ink: opens the port, verifies the protocol hello, and
sends wheel-rate commands. See docs/protocol.md and docs/globus-logic.md
section 8.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import time

from serial import Serial

from config import SERIAL_PORT, SERIAL_BAUD, SERIAL_BOOT_WAIT_S

PROTOCOL_VERSION = 0


def open_link(port=None, baud=None):
    """Open the serial port and verify ink's boot hello."""
    conn = Serial(port or SERIAL_PORT, baud or SERIAL_BAUD, timeout=5)
    time.sleep(SERIAL_BOOT_WAIT_S)  # port-open resets ink

    hello = conn.readline().decode(errors="replace").strip()
    try:
        version = int(hello.removeprefix("ink p"))
    except ValueError:
        conn.close()
        raise RuntimeError(f"unexpected boot line from ink: {hello!r}")

    if version != PROTOCOL_VERSION:
        conn.close()
        raise RuntimeError(
            f"protocol mismatch: tsup expects v{PROTOCOL_VERSION}, ink is v{version}"
        )

    return conn


def send_rates(conn, rates):
    """Send a signed wheel-rate command: "V s1 s2 s3\\n"."""
    conn.write(f"V {rates[0]} {rates[1]} {rates[2]}\n".encode())
