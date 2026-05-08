# Campaign for North Africa State Space Cryptography

A steganographic messaging system that hides secret messages inside PNG board images of *The Campaign for North Africa* (SPI, 1979) by exploiting the astronomical state space of the most complex board wargame ever published.

The output looks like a screenshot of a CNA game in progress. Without the key, an observer cannot distinguish it from a legitimate mid-game board state.

![CNA Board](cna_board.png)

## Why This Works

A CNA game has 1,307 pieces, each with thousands of valid positions on a 174×29 hex grid plus 19 off-map zones. The total number of possible board states is:

```
~10^8,528 configurations
```

For comparison:
- **Chess**: ~10^44 legal positions
- **Go**: ~10^170 legal positions
- **AES-256 keyspace**: ~10^77
- **CNA**: ~10^8,528 — larger by **8,358 orders of magnitude** than Go

No computer — classical or quantum — can enumerate this space. The observable universe can perform roughly 10^140 operations in its entire lifetime. Even Grover's quantum search algorithm only reduces the space to ~10^4,264, which still exceeds physical possibility by over 4,000 orders of magnitude.

**Security comes from physical impossibility, not computational hardness.**

## How It Works

Counter images are rendered onto a map and their positions encode data using a two-layer key system:

1. **Layer 1**: A random session nonce is encoded into "key pieces" selected by a system key
2. **Layer 2**: The message is encoded into "message pieces" selected by a working key derived from the system key + nonce

Decoy pieces provide camouflage. The output is a PNG that looks like a screenshot of a real CNA game in progress.

### Encryption Layer

Messages are not embedded as raw bytes. The system uses **HKDF-SHA256** (RFC 5869) key derivation to produce:
- A **cipher key** (XOR stream encryption)
- A **MAC key** (HMAC-SHA256 authentication)
- A **nonce** (semantic security — the same message produces different output each time)

An observer without the key cannot distinguish the encoded image from a random (but valid) board configuration.

## Installation

### Requirements
- Python 3.9+
- System libraries: `cairo` (renders SVG counter art) and `poppler` (provides `pdftoppm`, renders the map PDF)

The `install.sh` script installs both via Homebrew (macOS) or apt (Ubuntu/Debian).

### Quick Install

```bash
git clone https://github.com/zeiglerbd5/Campaign-for-North-Africa-State-Space-Cryptography.git
cd Campaign-for-North-Africa-State-Space-Cryptography
./install.sh
```

Or install manually:

```bash
# macOS
brew install cairo poppler

# Ubuntu/Debian
sudo apt-get install libcairo2-dev poppler-utils

# Python dependencies
pip3 install Pillow "opencv-python-headless>=4.8,<4.11" "numpy>=1.24,<2" cairosvg
```

## Usage

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

Run with no arguments for an interactive prompt:

```bash
python3 -m cna_ssc
```

Messages are limited to 50 characters per image.

## Project Structure

```
cna_ssc/
├── __init__.py
├── __main__.py
├── cli.py             # Command-line interface
├── image_steg.py      # PNG-based steganography (encode + decode)
└── README.md
```

## Security Properties

| Property | Guarantee |
|---|---|
| **Brute-force resistance** | 10^8,528 states; physically impossible to enumerate |
| **Quantum resistance** | Grover's algorithm reduces to 10^4,264; observable universe can do ~10^140 |
| **Cover authenticity** | Output is a plausible CNA board screenshot |
| **Deniability** | Sharing board screenshots is routine in the wargaming community |
| **Semantic security** | Random nonce ensures same message produces different output each time |
| **Authentication** | HMAC-SHA256 tag detects wrong key or tampering |

## Dependencies

- **Pillow** ≥ 10.0 — Image manipulation
- **opencv-python-headless** ≥ 4.8 — Template matching for image decode
- **numpy** ≥ 1.24 — Array operations
- **cairosvg** ≥ 2.7 — SVG counter rendering

## License

All rights reserved. This source is published for portfolio review and
evaluation only — no use, copying, modification, or redistribution is
permitted without written permission. See [LICENSE](LICENSE).

The Campaign for North Africa is © 1979 SPI / © 2022 White Box Games. Vassal is open-source software (LGPL). Counter images are sourced from the community Vassal module. This project uses CNA-themed assets but is not affiliated with or endorsed by the rights holders.
