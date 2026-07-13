"""
Globus Link.

Serial connection to ink: opens the port, verifies the protocol hello, and
sends wheel-rate commands. See docs/protocol.md and docs/globus-logic.md
section 8.

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

PROTOCOL_VERSION = 0


def open_link(port="/dev/ttyACM0", baud=115200):
    """
    Open the serial port and verify ink's boot hello.

    TODO: pyserial Serial(port, baud); the port opening resets ink, so wait
    ~2s before reading; readline() the boot line ("ink p{N}\\n"); parse N
    and assert it equals PROTOCOL_VERSION - refuse to proceed on a
    mismatch (docs/protocol.md's rule, not optional). Return the open
    connection.
    """
    raise NotImplementedError


def send_rates(conn, rates):
    """
    Send a signed wheel-rate command: "V s1 s2 s3\\n".

    TODO: format `rates` (three ints, steps/s) into that message and
    write() it to `conn`.
    """
    raise NotImplementedError
