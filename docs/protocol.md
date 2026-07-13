# tsup/ink serial protocol

**Version: 0** (draft, in progress)

115200 baud, newline-terminated ASCII.

| Message | Direction | Meaning |
|---|---|---|
| `ink p0` | ink → tsup | hello on boot, and in response to `P` |
| `P` | tsup → ink | query protocol version |
| `V s1 s2 s3` | tsup → ink | wheel speeds, steps/sec, signed |

tsup queries with `P` rather than relying solely on the boot-time hello:
native-USB boards (e.g. the Nano R4) drop the whole USB connection on
reset - unlike classic AVR boards with a separate bridge chip that stays
connected through one - so a freshly-opened connection has no reliable way
to catch a broadcast tied to reset timing it may not even have caused.

## Rules

- tsup MUST query with `P`, read the response, and refuse to operate on a
  version mismatch.
- Any breaking change (message removed, field meaning changed) bumps the version.
- Additions that old firmware safely ignores do not bump it.
- The version lives in three places that change together: this file,
  `PROTOCOL_VERSION` in `ink.ino`, and the expected version in `tsup/link.py`.