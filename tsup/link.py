"""
Globus Link.

Serial connection to ink: opens the port, queries and verifies the protocol
version, and sends wheel-rate commands. See docs/protocol.md and
docs/globus-logic.md section 8.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import time

from serial import Serial

from config import SERIAL_PORT, SERIAL_BAUD, SERIAL_BOOT_WAIT_S

PROTOCOL_VERSION = 0
HELLO_SCAN_LINES = 20  # max non-hello lines to skip while hunting the P response


def open_link(port=None, baud=None):
    """Open the serial port and verify ink's protocol version.

    Actively queries with "P" rather than relying on ink's one-shot boot
    hello: native-USB boards (e.g. the Nano R4) drop the whole USB
    connection on reset, so a freshly-opened connection has no reliable way
    to catch a broadcast tied to reset timing it may not even have caused.
    """
    conn = Serial(port or SERIAL_PORT, baud or SERIAL_BAUD, timeout=5)
    time.sleep(SERIAL_BOOT_WAIT_S)  # let the connection settle before writing
    conn.reset_input_buffer()  # discard any stray hello already sitting in the buffer
    conn.write(b"P\n")

    # Skip any in-flight non-hello lines (e.g. debug output printed between
    # the buffer reset and ink processing the query) rather than failing on
    # the first one. An empty read is a real timeout - nothing is coming.
    hello = ""
    for _ in range(HELLO_SCAN_LINES):
        hello = conn.readline().decode(errors="replace").strip()
        if not hello or hello.startswith("ink p"):
            break

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
