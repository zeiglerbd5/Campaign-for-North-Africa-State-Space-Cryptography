# Experimental — Restricted Bijection (V2)

**Status: parked, not wired into the CLI.**

This directory holds an alternative encoder/decoder design that didn't get
finished. It's kept in-tree because the underlying idea is interesting and
may be worth picking up again later.

## What V1 (the active CLI) does

The shipping system in `cna_ssc/` produces a board state where **all 2,468
pieces** in the Vassal module appear at valid positions. The state is legal
under the rules of *Campaign for North Africa* but not necessarily a state
that would arise during real play — every counter is on the map at once. For
the project's stated purpose (a fun cryptography novelty) this is fine, and
it's what the README and the `cna-ssc img-encode` command produce today.

## What V2 was reaching for

V2 swaps the bijection's universe: instead of all 2,468 pieces, it operates
on the **subset of pieces present in a template `.vsav` file** that the user
supplies. The template becomes part of the key. The encoded output then looks
like a plausible mid-game continuation of that template rather than a
synthetic dump of every counter.

State space drops from ~10⁸⁵²⁸ to ~10⁴⁴⁰⁰ — still astronomically larger than
any cryptographic keyspace, just narrower in a way that buys steganographic
plausibility. The intended payoff is that an adversary who actually knows
CNA gameplay couldn't tell the cover object from a real saved game.

## Why it's parked

V1 already meets the project's goal of producing a *legal* game state. V2
addresses a stricter goal — producing a *plausible* one — that wasn't part
of the original plan. Wiring V2 into the CLI would require:

- A template-management UX (both parties need the same template).
- Reworking the key model to include the template hash.
- End-to-end testing of the encode → email → decode loop with restricted
  pieces.
- Rendering V2 states through `image_steg.py` (currently V1-only).

None of that is hard, but none of it is implemented either.

## What's here

```
experimental/
├── engine/
│   ├── bijection.py        # restricted-bijection math
│   ├── mixed_radix.py      # arbitrary-precision odometer
│   ├── hex_grid.py         # Vassal coordinate system, cleaner than V1
│   └── piece_registry.py   # piece data loaded from JSON (not hardcoded)
├── crypto/
│   ├── encoder.py          # template .vsav + message → output .vsav
│   └── decoder.py          # output .vsav → message
├── formats/
│   ├── vsav_codec.py       # combined .vsav reader/writer
│   └── buildfile_parser.py # regenerate piece_registry_data.json from .vmod
└── tests/
    └── test_v2.py          # 19 tests against the engine/formats/crypto modules
```

## Running the V2 tests

```
PYTHONPATH=. pytest experimental/tests/
```

These don't run by default — they're not wired into any `pytest.ini` or
`pyproject.toml` test config, and they aren't expected to pass against the
shipping CLI.
