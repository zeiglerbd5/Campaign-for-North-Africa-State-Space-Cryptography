# Campaign for North Africa — Steganography

A steganographic messaging system that hides short secret messages inside PNG images that look like the board of *The Campaign for North Africa* (SPI, 1979) — the most notoriously complex board wargame ever published.

The output is a PNG of a CNA map with counters placed on it. The cover medium is the *positions* of those counters: a working key turns a message into a deterministic placement, and a recipient with the same key can recover the message by detecting where each counter ended up.

![CNA Board](example_board.png)

## What this is, and what it isn't

This is a **steganography** project first. The interesting part is the cover medium — the enormous space of plausible-looking board configurations of a famously sprawling wargame — not the cryptographic primitives, which are conventional.

- **Cryptographic security** is conventional and modest: HKDF-SHA256 derives a per-message keystream that XOR-encrypts the payload before it is mapped to piece positions. Effective keyspace is the entropy of the user passphrase. There is no key stretching (PBKDF / scrypt / argon2), so do not treat this as a high-assurance cipher.
- **Steganographic security** rests on the cover-image looking like an in-progress CNA game to a casual observer, and on the recipient knowing that a particular image carries a message at all. It does not rest on the combinatorial size of the CNA state space, and the README no longer claims otherwise.

If you came here for an interesting cover medium and a clean implementation of "encode bytes as positions of distinguishable objects on a large grid," that is what this is. If you came here for a novel cipher, this is not that.

## Why CNA, specifically

CNA is unusual: ~1,300 unit counters, a long hex map (~165 columns × ~25 playable rows), per-unit positioning that varies enormously between games, and a community in which sharing screenshots of mid-game states is normal. That combination makes a *plausible* CNA board screenshot a high-entropy, low-suspicion cover medium — far higher than, say, a chessboard, where any position is one of ~10^44 and incongruities are obvious to a human reader.

The number of distinguishable board states the encoder can actually produce is still very large (well in excess of what a brute-force search over cover-images is meaningful for), but the load-bearing claim is *plausibility of the cover*, not enumeration-hardness of the space.

## How it works

Two layers, both keyed:

1. **Layer 1 — Session nonce.** A random 128-bit nonce is encoded into the positions of a deterministically-selected set of "key pieces." The selection is driven by either a hardcoded system constant or, if supplied, a user passphrase mixed into it via HKDF.
2. **Layer 2 — Message.** A working key is derived as `HKDF(base_key, info="cna-session", salt=nonce)`. A separate set of message pieces is selected with the working key, and the message is encoded into their positions using a mixed-radix, without-replacement scheme so each piece lands on a unique hex.

Additional **decoy pieces** are placed at HKDF-derived positions to make the image look more like an in-progress game rather than a sparse opening.

Decoding reverses both layers: detect each expected piece via template matching, snap its location to the nearest hex, and read out the bytes.

### Encryption layer

Payloads are XOR-encrypted with an HKDF-SHA256 keystream before being mapped to positions. The same plaintext encodes to a different image each time, because the session nonce changes the working key.

**Known limitation:** the current version does not verify a MAC tag on decode. A successful decode means template matching succeeded and the keystream produced valid UTF-8 — it does not prove the image was produced by someone with the key. Treat decoded output as unauthenticated until this is fixed.

## Threat model

What this resists:
- A casual observer who sees the PNG and does not suspect it contains anything.
- Recovery without the user passphrase, assuming the passphrase has non-trivial entropy.

What this does **not** resist:
- An attacker who knows the system exists and has the codebase, when no user passphrase is set. The "system key" baked into the code is not a secret.
- Cryptanalysis by a CNA-literate observer who notices the board state is implausible (too few units, unrealistic placement, missing setup conventions).
- Statistical analysis of position distributions across many encoded images.
- Tampering — see the MAC limitation above.

## Installation

### Requirements
- Python 3.9+
- System libraries: `cairo` (renders SVG counter art) and `poppler` (provides `pdftoppm`, renders the map PDF)

The `install.sh` script installs both via Homebrew (macOS) or apt (Ubuntu/Debian).

### Quick install

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

## Project structure

```
cna_ssc/
├── __init__.py
├── __main__.py
├── cli.py             # Command-line interface
└── image_steg.py      # PNG-based steganography (encode + decode)
```

## Properties

| Property | Status |
|---|---|
| **Cover plausibility** | Reasonable to a casual viewer; weak to a CNA-literate observer because piece density is below mid-game norms |
| **Semantic security** | Random per-message nonce — same plaintext produces a different image each time |
| **Confidentiality** | HKDF-SHA256 keystream; effective keyspace = user passphrase entropy |
| **Authentication** | Not yet — MAC verification on decode is a known TODO |
| **Deniability** | Sharing CNA board screenshots is unremarkable in the wargaming community |

## Dependencies

- **Pillow** ≥ 10.0 — Image manipulation
- **opencv-python-headless** ≥ 4.8 — Template matching for image decode
- **numpy** ≥ 1.24 — Array operations
- **cairosvg** ≥ 2.7 — SVG counter rendering

## License

Source code is released under the MIT License — see [LICENSE](LICENSE).

The repository also bundles third-party assets (the CNA map PDF and Vassal-derived counter art) which are **not** covered by the MIT license. See [NOTICE](NOTICE) for attribution and usage notes on those assets.

*The Campaign for North Africa* is © 1979 SPI / © 2022 White Box Games. Vassal is open-source software (LGPL). Counter images originate from the community Vassal module. This project uses CNA-themed assets but is not affiliated with or endorsed by the rights holders.
