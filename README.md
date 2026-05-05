# Campaign for North Africa State Space Cryptography

A steganographic messaging system that hides secret messages inside board game save files by exploiting the astronomical state space of *The Campaign for North Africa* (SPI, 1979).

The game's **~10^8,528 distinct board configurations** exceed the information-theoretic keyspace of any existing cryptographic system by thousands of orders of magnitude. This project encodes messages as game states that are indistinguishable from legitimate saved games to any observer who does not possess the key.

![CNA Board](cna_board.png)

## Why This Works

A CNA game has 1,307 pieces, each with thousands of valid positions on a 174x29 hex grid plus 19 off-map zones. The total number of possible board states is:

```
~10^8,528 configurations
~28,333 bits of information capacity per save file (~3.5 KB)
```

For comparison:
- **Chess**: ~10^44 legal positions
- **Go**: ~10^170 legal positions
- **AES-256 keyspace**: ~10^77
- **CNA**: ~10^8,528 — larger by **8,358 orders of magnitude** than Go

No computer — classical or quantum — can enumerate this space. The observable universe can perform roughly 10^140 operations in its entire lifetime. Even Grover's quantum search algorithm only reduces the space to ~10^4,264, which still exceeds physical possibility by over 4,000 orders of magnitude.

**Security comes from physical impossibility, not computational hardness.**

## How It Works

### The Bijection

Each piece in the game has a list of valid locations. The full game state is a tuple of position indices — one per piece — which maps to a single integer via mixed-radix encoding:

```
N = d[0] + r[0] * (d[1] + r[1] * (d[2] + r[2] * (...)))
```

where `r[k]` is the number of valid positions for piece `k` and `d[k]` is the chosen position index. This creates a **bijection** between integers in the range `[0, 10^8528)` and valid game states.

A message is converted to an integer, that integer selects a game state, and that state is written to a `.vsav` file (the Vassal game engine's save format). The recipient reverses the process: parse the save file, reconstruct the integer, recover the message.

### Encryption Layer

Messages are not embedded as raw integers. The system uses **HKDF-SHA256** (RFC 5869) key derivation to produce:
- A **cipher key** (XOR stream encryption)
- A **MAC key** (HMAC-SHA256 authentication)
- A **nonce** (semantic security — same message produces different output each time)

The encoded payload is: `[4-byte length | ciphertext | 16-byte HMAC tag]`

An observer without the key cannot distinguish the game state from a random (but valid) board configuration.

### The .vsav Format

Vassal `.vsav` files are ZIP archives containing:
1. `moduledata` — XML module metadata
2. `savedata` — XML save metadata
3. `savedGame` — command stream, obfuscated by XOR with `0x7D`

This project reads and writes valid `.vsav` files that open normally in Vassal 3.x.

### Three Encoding Approaches

| Approach | Description | Trade-off |
|---|---|---|
| **hash** (default) | HKDF-derived ordinal with HMAC authentication | Best security: semantic security + authentication |
| **positional** | Direct mixed-radix embedding, piece ordering is the key | Fastest, simplest |
| **constraint_aware** | Rejection sampling for rule-legal game states | Best cover authenticity, slowest |

### Image Mode

In addition to `.vsav` encoding, the system can hide messages directly in **PNG board images**. Counter images are rendered onto a map and their positions encode data using a two-layer key system:

1. **Layer 1**: A random session nonce is encoded into "key pieces" selected by a system key
2. **Layer 2**: The message is encoded into "message pieces" selected by a working key derived from the system key + nonce

Decoy pieces provide camouflage. The output looks like a screenshot of a game in progress.

## Installation

### Requirements
- Python 3.9+
- System library: `cairo` (for rendering SVG counter art)
- `pdftoppm` (Poppler) — needed by `img-encode` to render the map at 150 DPI
- A copy of `CNA-CB-Map-v1.4.pdf` placed at the repo root (gitignored —
  copyrighted SPI material, not redistributable). Without it, `img-encode`
  will fail; the `.vsav` commands work fine without it.

### Quick Install

```bash
git clone https://github.com/zeiglerbd5/Campaign-for-North-Africa-State-Space-Cryptography.git
cd Campaign-for-North-Africa-State-Space-Cryptography
./install.sh
```

Or install manually:

```bash
# macOS
brew install cairo

# Ubuntu/Debian
sudo apt-get install libcairo2-dev

# Python dependencies
pip3 install Pillow "opencv-python-headless>=4.8,<4.11" "numpy>=1.24,<2" cairosvg
```

## Usage

### Image Mode (PNG board images)

The simplest way to use the system — hide a message in a board image:

```bash
# Encode a message into a board image
python3 -m cna_ssc img-encode \
    --message "Rendezvous at dawn near Tobruk" \
    --key "shared secret passphrase" \
    --out cna_board.png

# Decode
python3 -m cna_ssc img-decode \
    --in cna_board.png \
    --key "shared secret passphrase"
```

Or use the shortcut scripts:

```bash
./encode --message "Rendezvous at dawn near Tobruk" --key "shared secret"
./decode --in cna_board.png --key "shared secret"
```

Image mode supports messages up to 50 characters.

### Vassal Save File Mode (.vsav)

For higher capacity (~3.5 KB per file):

```bash
# Encode a text message
python3 -m cna_ssc encode \
    --key "operation torch commences at dawn" \
    --message "Proceed to Tobruk immediately. Destroy supply depot." \
    --out message.vsav

# Decode
python3 -m cna_ssc decode \
    --key "operation torch commences at dawn" \
    --in message.vsav

# Encode a binary file
python3 -m cna_ssc encode \
    --key "my key" \
    --message "$(cat secret.txt)" \
    --out message.vsav
```

### Other Commands

```bash
# Inspect a .vsav file (no key needed)
python3 -m cna_ssc inspect --in game.vsav

# Export game state as a schematic PNG
python3 -m cna_ssc export --in game.vsav --out board.png

# Run a full encode/decode demo with statistics
python3 -m cna_ssc demo
```

### Interactive Mode

Run with no arguments for an interactive prompt:

```bash
python3 -m cna_ssc
```

### Key Formats

Keys can be provided in three formats:

```bash
--key "my secret passphrase"          # Passphrase (HKDF-stretched)
--key hex:0123456789abcdef...         # Raw hex bytes
--key file:/path/to/keyfile           # Read key material from a file
```

### Python API

```python
from cna_ssc import encode, decode

# Encode
vsav_bytes = encode(b"secret message", key=b"my_key_material_here")
with open("game.vsav", "wb") as f:
    f.write(vsav_bytes)

# Decode
with open("game.vsav", "rb") as f:
    vsav_bytes = f.read()
message = decode(vsav_bytes, key=b"my_key_material_here")
```

## Project Structure

```
cna_ssc/                  # Shipping package — used by the CLI
├── cli.py                # Command-line interface
├── encoder.py            # High-level encode: bytes → .vsav
├── decoder.py            # High-level decode: .vsav → bytes
├── bijection.py          # GameState ↔ state-space integer
├── mixed_radix.py        # Arbitrary-precision mixed-radix arithmetic
├── state_model.py        # GameState / PieceState dataclasses
├── constants.py          # Piece vocabulary, hex positions, off-map zones
├── vsav_reader.py        # Parse .vsav files (XOR 0x7D decode)
├── vsav_writer.py        # Generate valid .vsav files
├── image_steg.py         # Image-based steganography (PNG board images)
├── image_export.py       # Export game state as schematic PNG
└── data/                 # Playable hex positions

experimental/             # Parked V2 design (see experimental/README.md)
├── engine/               # Restricted bijection over a template's pieces
├── crypto/               # Template-keyed encoder/decoder
├── formats/              # Cleaner .vsav codec + buildFile.xml parser
└── tests/                # 19 tests against the V2 modules
```

The shipping CLI (`cna-ssc img-encode`, `cna-ssc img-decode`, etc.) uses the
top-level `cna_ssc/` package and produces a legal CNA game state — every
piece placed at a valid position, though not necessarily a state that would
arise during real play.

`experimental/` holds a second-pass design that operates on the subset of
pieces present in a template `.vsav` file, aiming for output that looks like
a plausible mid-game continuation. It is not wired into the CLI and is kept
in-tree for future work — see [`experimental/README.md`](experimental/README.md)
for details.

## State Space Breakdown

```
Component               Pieces    States per piece    Radices
───────────────────────────────────────────────────────────────
Ground units            2,176     4,979 locations     [4979, 2, 256]
                                  × 2 faces
                                  × 256 status flags
Aircraft                   13     4,979 locations     [4979, 5, 4]
                                  × 5 air states
                                  × 4 sortie states
Naval / airfields         222     4,979 locations     [4979]
Markers                    35     4,979 locations     [4979]
Admin trackers             22     varies              varies
Meta state (turn/stage)     —     111 × 3 × 6        [111, 3, 6]
───────────────────────────────────────────────────────────────
Total                   1,307 pieces across 4,979 + 19 locations
State space             ~10^8,528  (~28,333 bits)
```

## Security Properties

| Property | Guarantee |
|---|---|
| **Capacity** | ~28,333 bits per `.vsav` file (~3.5 KB of hidden data) |
| **Brute-force resistance** | 10^8,528 states; physically impossible to enumerate |
| **Quantum resistance** | Grover's algorithm reduces to 10^4,264; observable universe can do ~10^140 |
| **Cover authenticity** | Output is a valid Vassal save file / plausible board image |
| **Deniability** | Sharing `.vsav` files and board screenshots is routine in the wargaming community |
| **Semantic security** | Random nonce ensures same message produces different output each time |
| **Authentication** | HMAC-SHA256 tag detects wrong key or tampering |

## Running Tests

The V2 (experimental) test suite covers `experimental/engine/` and
`experimental/formats/`:

```bash
# Unit tests
PYTHONPATH=. pytest experimental/tests/ -v -k "not integration"

# Integration tests (requires .vsav files)
PYTHONPATH=. pytest experimental/tests/ -v -m integration
```

The shipping V1 path is exercised end-to-end via `cna-ssc demo`.

## Dependencies

- **Pillow** >= 10.0 — Image manipulation
- **opencv-python-headless** >= 4.8 — Template matching for image decode
- **numpy** >= 1.24 — Array operations
- **cairosvg** >= 2.7 — SVG counter rendering

## License

MIT License.

The Campaign for North Africa is (c) 1979 SPI / (c) 2022 White Box Games. Vassal is open-source software (LGPL). Counter images are sourced from the community Vassal module. This project uses the CNA Vassal module's data structures but is not affiliated with or endorsed by the rights holders.
