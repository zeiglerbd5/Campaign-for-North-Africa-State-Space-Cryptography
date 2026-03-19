# CNA-SSC: Campaign for North Africa State Space Cryptography

A steganographic encoding scheme using the game state space of *The Campaign
for North Africa* (SPI, 1979) as a covert channel.

## Background

CNA's state space — approximately **10^8,528** distinct board configurations —
exceeds the information-theoretic keyspace of any existing cryptographic
system by thousands of orders of magnitude.  This project exploits that
enormous state space to hide messages inside Vassal `.vsav` save files that
are indistinguishable from legitimate game states to any observer who does
not possess the canonical piece/location ordering (the "key").

## Security Properties

| Property | Guarantee |
|---|---|
| **Capacity** | ~28,333 bits per `.vsav` file (~3.5 KB) |
| **Brute-force resistance** | Requires enumeration of 10^8,528 states — not computationally hard but physically impossible |
| **Quantum resistance** | Grover's algorithm reduces to ~10^4,264 states; observable universe can achieve ~10^140 operations |
| **Cover authenticity** | Output is a valid Vassal save file that opens normally in Vassal 3.x |
| **Deniability** | Sharing `.vsav` files is routine in the wargaming community |

## Architecture

```
cna_ssc/
├── cli.py                   # Command-line interface
├── encoder.py               # bytes → .vsav (HKDF key derivation)
├── decoder.py               # .vsav → bytes
├── bijection.py             # GameState ↔ state-space integer (mixed-radix)
├── mixed_radix.py           # Arbitrary-precision mixed-radix arithmetic
├── state_model.py           # GameState / PieceState dataclasses
├── constants.py             # Piece vocabulary, hex positions, off-map zones
├── vsav_reader.py           # Parse .vsav files (XOR 0x7D decode)
├── vsav_writer.py           # Generate valid .vsav files
├── image_steg.py            # Image-based steganography (PNG board images)
├── image_export.py          # Export game state as schematic PNG
├── engine/                  # Alternative restricted-bijection implementation
│   ├── hex_grid.py          #   Vassal coordinate system
│   ├── piece_registry.py    #   Canonical ordered piece list (2,468 pieces)
│   ├── mixed_radix.py       #   Mixed-radix with message framing
│   └── bijection.py         #   Template-based restricted bijection
├── formats/
│   ├── vsav_codec.py        #   .vsav ZIP codec (dataclass-based)
│   └── buildfile_parser.py  #   Parse buildFile.xml → piece registry JSON
├── crypto/                  # Template-based encode/decode (experimental)
│   ├── encoder.py           #   Template .vsav → stego .vsav
│   └── decoder.py           #   Stego .vsav → plaintext
├── data/
│   └── playable_hexes.json  #   Valid hex positions
└── tests/
    └── test_core.py         # Unit + integration tests
```

## Quick Start

### 1. Setup (one time)

```bash
# Parse buildFile.xml from the CNAv2.1.0.vmod and generate piece registry
python -m cna_ssc setup /path/to/buildFile.xml
```

This writes `cna_ssc/engine/piece_registry_data.json` — the canonical piece
ordering that constitutes half of the steganographic key.

### 2. Encode a message

```bash
python -m cna_ssc encode \
    --message "Rendezvous at 0200. Use the southern route." \
    --template Operation_Brevity_set-upv0.93.vsav \
    --output stego_game.vsav
```

Or encode a binary file:

```bash
python -m cna_ssc encode \
    --file secret.pdf \
    --template template.vsav \
    --output stego_game.vsav
```

### 3. Decode

```bash
python -m cna_ssc decode stego_game.vsav
# → Rendezvous at 0200. Use the southern route.
```

### 4. Inspect a file

```bash
python -m cna_ssc inspect some_game.vsav
```

### 5. Test roundtrip

```bash
python -m cna_ssc roundtrip \
    --template Operation_Brevity_set-upv0.93.vsav \
    --message "Test message"
```

## How It Works

### The .vsav Format

Vassal `.vsav` files are ZIP archives containing three files:
- `moduledata` — XML module metadata
- `savedata` — XML save metadata  
- `savedGame` — Obfuscated command stream

The command stream encoding:
1. Starts with `!VCSK` (5 bytes)
2. Followed by a hex-encoded byte string
3. Each byte XOR'd with `0x7D` gives the plaintext
4. Plaintext is `\x1b`-delimited Vassal command strings

### The Bijection

Each piece in the canonical registry has a list of valid locations (hex
positions + off-map zones).  The full game state is encoded as a tuple of
position indices, then converted to a single integer via mixed-radix
encoding:

```
N = i₁ + r₁×(i₂ + r₂×(i₃ + r₃×(…)))
```

where `rₖ` is the number of valid positions for piece `k`.

### The Key

The steganographic key consists of:
1. **Canonical piece ordering** — the order of entries in `piece_registry_data.json`
2. **Canonical location ordering** — the `valid_locations` list for each piece (defined in `piece_registry.py`)
3. **XOR constant** — `0x7D` (needed to decode the `.vsav` format)

An observer without items 1 and 2 cannot compute `N` and therefore cannot
extract the message, even knowing the XOR constant.

### Message Framing

```
framed = len(message) [4 bytes] + message
N = nonce × 2^(8×len(framed)) + int(framed)
```

The 4-byte length prefix allows the decoder to determine `len(message)`
without out-of-band communication.  The nonce randomises the game state
(semantic security: same message → different `.vsav` each time).

## The State Space

```
Component               Count    States per piece   Notes
─────────────────────────────────────────────────────────
Ground units           2,176    ~5,000 locations   Hexes + off-map zones
Aircraft                  13    ~5,000 locations   Hexes + air zones  
Naval / airfields        222    ~5,000 locations   Hexes + naval zones
Markers                   35    ~5,029 locations   All hexes + zones
Admin trackers, etc.      22    small              Fixed zones

Total pieces           2,468
State space bits      ~28,333
State space size      ~10^8,528
```

For comparison:
- Go (board game): 10^170 states
- CNA: 10^8,528 states — **8,358 orders of magnitude larger**

## Development

```bash
# Run unit tests
pytest cna_ssc/tests/ -v -k "not integration"

# Run integration tests (requires real .vsav and buildFile.xml)
pytest cna_ssc/tests/ -v -m integration

# Check capacity
python -m cna_ssc capacity
```

## Files Required

| File | Source | Purpose |
|---|---|---|
| `CNAv2.1.0.vmod` | NJHarman (BGG) | Contains `buildFile.xml` |
| `buildFile.xml` | Extracted from `.vmod` | Piece registry generation |
| Any `.vsav` | Generated by Vassal | Template for encoding |

The `.vmod` and `.vsav` files are not distributed with this project.

## Citation

If you use this work in research, please cite:

> Zeigler, B. (2026). *CNA-SSC: Steganographic Encoding in an Astronomically
> Large Board Game State Space.* Campaign for North Africa Python Engine Project.

## License

MIT License. The steganographic scheme and code are original work.

The Campaign for North Africa is © 1979 SPI / © 2022 White Box Games.
Vassal is open-source software (LGPL). This project uses the CNA Vassal
module's data structures but does not redistribute copyrighted game materials.
