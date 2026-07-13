# tsup/ink serial protocol

**Version: 0** (draft, in progress)

115200 baud, newline-terminated ASCII.

| Message | Direction | Meaning |
|---|---|---|
| `ink p0` | ink → tsup | hello on boot: protocol version 0 |
| `V s1 s2 s3` | tsup → ink | wheel speeds, steps/sec, signed |

## Rules

- tsup MUST read the hello and refuse to operate on a version mismatch.
- Any breaking change (message removed, field meaning changed) bumps the version.
- Additions that old firmware safely ignores do not bump it.
- The version lives in three places that change together: this file,
  `PROTOCOL_VERSION` in `ink.ino`, and the expected version in `tsup/link.py`.