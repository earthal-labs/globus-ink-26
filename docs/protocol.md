# tsup/ink serial protocol

**Version: 0** (draft, in progress)

115200 baud, newline-terminated ASCII.

| Message | Direction | Meaning |
|---|---|---|
| `ink p0` | ink → tsup | hello on boot, and in response to `P` |
| `P` | tsup → ink | query protocol version |
| `V s1 s2 s3` | tsup → ink | wheel speeds, steps/sec, signed |
| `D mode` | tsup → ink | select coil map: `nat_full`, `nat_half`, `swap_full`, `swap_half` |
| `ink d mode` | ink → tsup | ack of `D` / `T` drive mode |
| `T m mode` | tsup → ink | self-held bench: motor `m` (0–2) at 40 steps/s for 4 s |
| `C m` | tsup → ink | self-held slow crawl: 500 ms/phase, NATURAL+FULLSTEP, 2 cycles |
| `ink c …` | ink → tsup | crawl phase/bits lines (`bits=1100` etc.) |
| `I m j` | tsup → ink | light only motor `m` input `j` (1–4) for 2 s |
| `ink i …` | ink → tsup | ack of single-IN probe |

tsup queries with `P` rather than relying solely on the boot-time hello:
native-USB boards (e.g. the Nano R4) drop the whole USB connection on
reset - unlike classic AVR boards with a separate bridge chip that stays
connected through one - so a freshly-opened connection has no reliable way
to catch a broadcast tied to reset timing it may not even have caused.

`D` / `T` are additive bench messages: production `tsup` never sends them;
old firmware that only accepts `P`/`V` ignores them. Default drive mode is
`nat_full` (natural IN1–IN4 pin order, two-coil full-step). Use
`tsup/force_spin.py` to try each mode and lock the winner in `ink.ino`.

## Rules

- tsup MUST query with `P`, read the response, and refuse to operate on a
  version mismatch.
- Any breaking change (message removed, field meaning changed) bumps the version.
- Additions that old firmware safely ignores do not bump it.
- The version lives in three places that change together: this file,
  `PROTOCOL_VERSION` in `ink.ino`, and the expected version in `tsup/link.py`.