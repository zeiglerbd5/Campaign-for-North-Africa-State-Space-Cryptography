"""
cna_ssc/image_steg.py
=====================
Image-based steganographic encoder/decoder for CNA.

Encodes a secret message into piece positions on a CNA board image.
Decodes by detecting pieces in the image via template matching.

Usage:
    from cna_ssc.image_steg import encode_message, decode_message

    # Encode
    png_bytes = encode_message(
        message="Meet at dawn near Tobruk",
        key="shared secret passphrase",
    )
    with open("game.png", "wb") as f:
        f.write(png_bytes)

    # Decode
    with open("game.png", "rb") as f:
        png_bytes = f.read()
    text = decode_message(png_bytes, key="shared secret passphrase")
"""

from __future__ import annotations

import hashlib
import hmac
import io
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
MAP_PDF = _PROJECT / "CNA-CB-Map-v1.4.pdf"
IMAGES_DIR = _PROJECT / "images"

# Cached rendered map (150 DPI)
_MAP_CACHE = _PROJECT / ".cache"
MAP_PNG_150 = _MAP_CACHE / "map_150dpi.png"
TEMPLATES_DIR = _MAP_CACHE / "templates"

# ---------------------------------------------------------------------------
# Hex grid parameters (calibrated from CNA-CB-Map-v1.4.pdf at 150 DPI)
# ---------------------------------------------------------------------------
# The PDF map rendered at 150 DPI is 9421 x 3138 pixels.
# Hex grid: flat-top hexes with odd-row stagger (odd map-rows shift right).
#
# Grid measured from visible hex labels on the map:
#   Column spacing (dx): 60.5 px
#   Row spacing (dy): 50.0 px
#   Odd-row stagger: +30.25 px (dx/2) horizontal offset
#
# Map rows increase northward (toward smaller y), so in pixel coordinates
# row numbers run "backwards": higher row numbers = smaller y values.
#
# Anchor point: hex D3524 (Section D, map-row 35, map-col 24)
# is at pixel (6880, 1385) at 150 DPI.

DX = 60.5          # pixels between column centers
DY = 50.0          # pixels between row centers
STAGGER = DX / 2   # odd-row horizontal offset

# Anchor: map-row 35, map-col 24, section D
# To convert (map_row, map_col) -> pixel:
#   px = ORIGIN_X + (abs_col) * DX + (map_row % 2) * STAGGER
#   py = ORIGIN_Y - (map_row) * DY
# where abs_col accounts for section offsets.
#
# We use a simpler approach: define a pixel-based grid where positions are
# identified by (grid_col, grid_row) starting from (0,0) at the top-left
# playable hex. This avoids needing to parse section letters entirely.

# We'll compute the origin from our anchor and the section/column mapping.
# Section local column widths (from hex label observations):
#   Section A: cols 01-33 (33 columns)
#   Section B: cols 01-33 (33 columns)
#   Section C: cols 01-33 (33 columns)
#   Section D: cols 01-33 (33 columns)
#   Section E: cols 01-33 (33 columns)
# Total: 165 columns (approximate, some sections may differ)

SECTION_OFFSETS = {
    'A': 0,
    'B': 33,
    'C': 66,
    'D': 99,
    'E': 132,
}

# Anchor: D3524 → absolute col = 99 + 24 = 123, map_row = 35
# At 150 DPI pixel: (6880, 1385)
_ANCHOR_ABS_COL = 99 + 24   # = 123
_ANCHOR_MAP_ROW = 35
_ANCHOR_PX = 6880
_ANCHOR_PY = 1385

# Solve for origin:
#   6880 = ORIGIN_X + 123 * 60.5 + (35 % 2) * 30.25
#   6880 = ORIGIN_X + 7441.5 + 30.25
#   ORIGIN_X = 6880 - 7471.75 = -591.75
#
#   1385 = ORIGIN_Y - 35 * 50
#   ORIGIN_Y = 1385 + 1750 = 3135

ORIGIN_X = _ANCHOR_PX - _ANCHOR_ABS_COL * DX - (_ANCHOR_MAP_ROW % 2) * STAGGER
ORIGIN_Y = _ANCHOR_PY + _ANCHOR_MAP_ROW * DY

# Playable hex bounds (approximate)
MIN_ROW = 20    # southernmost row (bottom of map)
MAX_ROW = 44    # northernmost row (near sea)
MIN_COL = 1     # westernmost column
MAX_COL = 165   # easternmost column

MAP_WIDTH = 9421
MAP_HEIGHT = 3138


def hex_to_pixel(abs_col: int, map_row: int) -> Tuple[float, float]:
    """Convert (absolute_column, map_row) to pixel coordinates at 150 DPI."""
    px = ORIGIN_X + abs_col * DX + (map_row % 2) * STAGGER
    py = ORIGIN_Y - map_row * DY
    return px, py


def pixel_to_hex(px: float, py: float) -> Tuple[int, int]:
    """Convert pixel coordinates to nearest (abs_col, map_row)."""
    # Estimate row first
    map_row = round((ORIGIN_Y - py) / DY)
    # Then column accounting for stagger
    stagger_offset = (map_row % 2) * STAGGER
    abs_col = round((px - ORIGIN_X - stagger_offset) / DX)
    return abs_col, map_row


def _build_playable_hexes() -> List[Tuple[int, int]]:
    """Build sorted list of all playable hex positions.

    Returns list of (abs_col, map_row) tuples for hexes that are on land
    (i.e., their pixel position falls within the map bounds and is not in the sea).
    """
    hexes = []
    for row in range(MIN_ROW, MAX_ROW + 1):
        for col in range(MIN_COL, MAX_COL + 1):
            px, py = hex_to_pixel(col, row)
            if 50 < px < MAP_WIDTH - 50 and 400 < py < MAP_HEIGHT - 50:
                hexes.append((col, row))
    return hexes


# Lazily computed
_PLAYABLE_HEXES: Optional[List[Tuple[int, int]]] = None
_HEX_INDEX: Optional[Dict[Tuple[int, int], int]] = None


def playable_hexes() -> List[Tuple[int, int]]:
    global _PLAYABLE_HEXES, _HEX_INDEX
    if _PLAYABLE_HEXES is None:
        _PLAYABLE_HEXES = _build_playable_hexes()
        _HEX_INDEX = {h: i for i, h in enumerate(_PLAYABLE_HEXES)}
    return _PLAYABLE_HEXES


def hex_index() -> Dict[Tuple[int, int], int]:
    playable_hexes()
    return _HEX_INDEX


def n_positions() -> int:
    return len(playable_hexes())


# ---------------------------------------------------------------------------
# Piece selection for encoding
# ---------------------------------------------------------------------------

@dataclass
class PieceInfo:
    """A game piece available for encoding."""
    name: str       # filename stem (e.g., "GE I-8 - 15")
    image: str      # full filename (e.g., "GE I-8 - 15.svg")
    path: str       # full path to image file
    nationality: str  # first 2 chars (GE, IT, BR, etc.)


_NATIONALITY_PREFIXES = (
    # Real combatant nationalities. Greek units are filed under 'AL'
    # (Allied), not 'GR' — the 'GR' prefix in this asset set flags
    # admin/marker tokens (Grey-01 blank, GR Gun Rep, GR Blank BG, etc.)
    # which have insufficient visual detail to be reliably template-matched.
    'GE', 'IT', 'BR', 'AU', 'NZ', 'SA', 'IN', 'AL', 'FR',
    'GD', 'BD', 'RD', 'NR', 'PO', 'OC',
)

# Minimum grayscale stddev for a template to be uniquely localizable.
# Solid-color counters score ~0; real unit counters score >> 30.
_MIN_TEMPLATE_STDDEV = 5.0


def _template_is_distinctive(piece: 'PieceInfo') -> bool:
    """Render the piece (or load from cache) and check it has enough visual
    variation to be uniquely template-matched. Caches the rendered template
    in TEMPLATES_DIR so _ensure_templates can reuse it later."""
    cache_path = TEMPLATES_DIR / f"{piece.name}.png"
    if cache_path.exists():
        img = cv2.imread(str(cache_path), cv2.IMREAD_UNCHANGED)
    else:
        try:
            pil_img = _render_counter(piece)
        except Exception:
            return False
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        pil_img.save(str(cache_path))
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGBA2BGRA)
    if img is None:
        return False
    gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    return float(gray.std()) > _MIN_TEMPLATE_STDDEV


def _discover_pieces() -> List[PieceInfo]:
    """Find all unit counter images in the images directory.

    Filters out admin/marker tokens (low-detail templates) which can't be
    reliably localized by template matching and would cause flaky decode.
    """
    pieces = []
    seen_names = set()
    for f in sorted(IMAGES_DIR.iterdir()):
        if not f.suffix.lower() in ('.svg', '.png'):
            continue
        stem = f.stem
        if len(stem) < 3 or stem[0:2].upper() not in _NATIONALITY_PREFIXES:
            continue
        if stem in seen_names:
            continue
        seen_names.add(stem)
        info = PieceInfo(
            name=stem,
            image=f.name,
            path=str(f),
            nationality=stem[:2].upper(),
        )
        if not _template_is_distinctive(info):
            continue
        pieces.append(info)
    return pieces


_ALL_PIECES: Optional[List[PieceInfo]] = None


def all_pieces() -> List[PieceInfo]:
    global _ALL_PIECES
    if _ALL_PIECES is None:
        _ALL_PIECES = _discover_pieces()
    return _ALL_PIECES


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand (RFC 5869) using SHA-256."""
    hash_fn = hashlib.sha256
    n_blocks = math.ceil(length / 32)
    okm = b""
    t = b""
    for i in range(1, n_blocks + 1):
        t = hmac.new(prk, t + info + bytes([i]), hash_fn).digest()
        okm += t
    return okm[:length]


def _hkdf(key: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt or b'\x00' * 32, key, hashlib.sha256).digest()
    return _hkdf_expand(prk, info, length)


def _key_bytes(key: str) -> bytes:
    """Convert string key to 32 bytes via SHA-256."""
    return hashlib.sha256(key.encode('utf-8')).digest()


# Hardcoded system key — identical on all installations.
# Security comes from the steganographic layer (nobody knows a CNA board
# image contains a message), not from keeping this key secret.
_SYSTEM_KEY = hashlib.sha256(
    b"CNA-SSC::Campaign-for-North-Africa::State-Space-Cryptography::v1"
).digest()


def _select_signal_pieces(
    key_bytes: bytes,
    n_needed: int,
    all_pcs: List[PieceInfo],
) -> List[PieceInfo]:
    """Deterministically select n_needed signal pieces using the key.

    Uses Fisher-Yates shuffle seeded by HKDF to permute all pieces,
    then takes the first n_needed. Ensures no two selected pieces share
    the same image (for unambiguous template matching).
    """
    n = len(all_pcs)
    rand_bytes = _hkdf(key_bytes, b"cna-signal-select", b"fisher-yates", n * 4)

    indices = list(range(n))
    for i in range(n - 1, 0, -1):
        j_bytes = rand_bytes[i * 4: i * 4 + 4]
        j = int.from_bytes(j_bytes, 'big') % (i + 1)
        indices[i], indices[j] = indices[j], indices[i]

    # Select pieces with unique images
    selected = []
    used_images = set()
    for idx in indices:
        p = all_pcs[idx]
        if p.image in used_images:
            continue
        used_images.add(p.image)
        selected.append(p)
        if len(selected) >= n_needed:
            break

    return selected


def _select_noise_pieces(
    key_bytes: bytes,
    signal_pieces: List[PieceInfo],
    all_pcs: List[PieceInfo],
    n_noise: int,
) -> List[PieceInfo]:
    """Select noise pieces (camouflage) that don't overlap with signal pieces."""
    signal_images = {p.image for p in signal_pieces}
    candidates = [p for p in all_pcs if p.image not in signal_images]

    rand_bytes = _hkdf(key_bytes, b"cna-noise-select", b"noise", len(candidates) * 4)
    indices = list(range(len(candidates)))
    for i in range(len(indices) - 1, 0, -1):
        j = int.from_bytes(rand_bytes[i * 4:i * 4 + 4], 'big') % (i + 1)
        indices[i], indices[j] = indices[j], indices[i]

    noise = []
    used_images = set(signal_images)
    for idx in indices:
        p = candidates[idx]
        if p.image in used_images:
            continue
        used_images.add(p.image)
        noise.append(p)
        if len(noise) >= n_noise:
            break
    return noise


def _assign_noise_positions(
    key_bytes: bytes,
    noise_pieces: List[PieceInfo],
    occupied: set,
) -> Dict[str, Tuple[int, int]]:
    """Assign noise pieces to plausible hex positions using the key."""
    hexes = playable_hexes()
    available = [h for h in hexes if h not in occupied]

    rand_bytes = _hkdf(key_bytes, b"cna-noise-pos", b"positions",
                        len(noise_pieces) * 4)
    positions = {}
    for i, piece in enumerate(noise_pieces):
        if not available:
            break
        j = int.from_bytes(rand_bytes[i * 4:i * 4 + 4], 'big') % len(available)
        pos = available[j]
        positions[piece.name] = pos
        occupied.add(pos)
        available[j] = available[-1]
        available.pop()
    return positions


# ---------------------------------------------------------------------------
# Mixed-radix encoding (raw bytes <-> piece positions)
# ---------------------------------------------------------------------------

def _piece_capacity(n_pieces: int) -> int:
    """Return the byte capacity for n_pieces using without-replacement encoding."""
    n_hex = n_positions()
    capacity_bits = sum(math.log2(n_hex - i) for i in range(n_pieces))
    return int(capacity_bits) // 8


def _bytes_to_positions(
    payload: bytes,
    key_bytes: bytes,
    pieces: List[PieceInfo],
    hkdf_label: bytes = b"cna-cipher",
    excluded_hexes: Optional[set] = None,
) -> Dict[str, Tuple[int, int]]:
    """Encode raw bytes into piece hex positions.

    Pads payload to fill full capacity, encrypts with HKDF keystream,
    then decomposes into per-piece positions using mixed-radix without
    replacement. Guaranteed unique positions for each piece.
    """
    hexes = playable_hexes()
    if excluded_hexes:
        hexes = [h for h in hexes if h not in excluded_hexes]
    n_hex = len(hexes)
    n_pieces = len(pieces)

    capacity_bits = sum(math.log2(n_hex - i) for i in range(n_pieces))
    capacity_bytes = int(capacity_bits) // 8

    if len(payload) > capacity_bytes:
        raise ValueError(
            f"Payload too large: {len(payload)} bytes, capacity {capacity_bytes}"
        )

    # Pad with HKDF-derived random bytes to fill full capacity
    pad_len = capacity_bytes - len(payload)
    padding = _hkdf(key_bytes, hkdf_label, b"padding", pad_len)
    padded = payload + padding

    # XOR with keystream for encryption
    keystream = _hkdf(key_bytes, hkdf_label, b"stream", len(padded))
    encrypted = bytes(a ^ b for a, b in zip(padded, keystream))

    # Convert to big integer
    N = int.from_bytes(encrypted, 'big')

    # Mixed-radix decomposition WITHOUT replacement
    available = list(range(n_hex))
    positions = {}
    remaining = N
    for piece in pieces:
        pool_size = len(available)
        idx = remaining % pool_size
        remaining //= pool_size
        hex_pos = hexes[available[idx]]
        positions[piece.name] = hex_pos
        available.pop(idx)

    return positions


def _positions_to_bytes(
    positions: Dict[str, Tuple[int, int]],
    key_bytes: bytes,
    pieces: List[PieceInfo],
    payload_len: int,
    hkdf_label: bytes = b"cna-cipher",
    excluded_hexes: Optional[set] = None,
) -> bytes:
    """Decode piece positions back to raw bytes.

    Reverses _bytes_to_positions. payload_len is the original unpadded
    byte count (the caller knows this, e.g. 2+msg_len or nonce size).
    """
    all_hexes = playable_hexes()
    if excluded_hexes:
        all_hexes = [h for h in all_hexes if h not in excluded_hexes]
    n_hex = len(all_hexes)
    n_pieces = len(pieces)

    hi = {h: i for i, h in enumerate(all_hexes)}

    # Recover per-piece indices
    available = list(range(n_hex))
    indices_and_pools = []
    for piece in pieces:
        pos = positions.get(piece.name)
        if pos is None:
            raise ValueError(f"Piece {piece.name!r} not found in detected positions")
        hex_idx = hi.get(pos)
        if hex_idx is None:
            raise ValueError(f"Position {pos} not in hex index")
        pool_idx = available.index(hex_idx)
        indices_and_pools.append((pool_idx, len(available)))
        available.pop(pool_idx)

    # Reconstruct N
    N = 0
    for pool_idx, pool_size in reversed(indices_and_pools):
        N = N * pool_size + pool_idx

    # Compute padded size (must match encoder)
    capacity_bits = sum(math.log2(n_hex - i) for i in range(n_pieces))
    capacity_bytes = int(capacity_bits) // 8

    encrypted = N.to_bytes(capacity_bytes, 'big')

    # Decrypt
    keystream = _hkdf(key_bytes, hkdf_label, b"stream", capacity_bytes)
    padded = bytes(a ^ b for a, b in zip(encrypted, keystream))

    return padded[:payload_len]


# Convenience wrappers for message encoding (with length prefix)

def _message_to_positions(
    message: str,
    key_bytes: bytes,
    signal_pieces: List[PieceInfo],
    excluded_hexes: Optional[set] = None,
) -> Dict[str, Tuple[int, int]]:
    """Encode a message string into signal piece hex positions."""
    msg_bytes = message.encode('utf-8')
    framed = len(msg_bytes).to_bytes(2, 'big') + msg_bytes
    return _bytes_to_positions(framed, key_bytes, signal_pieces,
                               hkdf_label=b"cna-msg", excluded_hexes=excluded_hexes)


def _positions_to_message(
    positions: Dict[str, Tuple[int, int]],
    key_bytes: bytes,
    signal_pieces: List[PieceInfo],
    excluded_hexes: Optional[set] = None,
) -> str:
    """Decode signal piece positions back to a message string."""
    # We need to recover the full padded payload first to read the length prefix.
    # Read the 2-byte length prefix by decoding with max capacity as payload_len.
    n_hex = n_positions()
    if excluded_hexes:
        n_hex = n_hex - len(excluded_hexes)
    n_pieces = len(signal_pieces)
    capacity_bits = sum(math.log2(n_hex - i) for i in range(n_pieces))
    capacity_bytes = int(capacity_bits) // 8

    full = _positions_to_bytes(positions, key_bytes, signal_pieces,
                                payload_len=capacity_bytes, hkdf_label=b"cna-msg",
                                excluded_hexes=excluded_hexes)
    msg_len = int.from_bytes(full[:2], 'big')
    return full[2:2 + msg_len].decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# Map and template rendering
# ---------------------------------------------------------------------------

COUNTER_SIZE = 44  # pixels at 150 DPI


def _ensure_map_rendered() -> str:
    """Ensure the base map PNG exists at 150 DPI. Returns path."""
    _MAP_CACHE.mkdir(exist_ok=True)
    if not MAP_PNG_150.exists():
        import subprocess
        subprocess.run([
            'pdftoppm', '-png', '-r', '150', '-singlefile',
            str(MAP_PDF), str(MAP_PNG_150).replace('.png', '')
        ], check=True)
    return str(MAP_PNG_150)


def _render_counter(piece: PieceInfo, size: int = COUNTER_SIZE) -> Image.Image:
    """Render a piece counter to a PIL Image at the given size."""
    path = piece.path
    if path.endswith('.svg'):
        import cairosvg
        png_data = cairosvg.svg2png(
            url=path,
            output_width=size,
            output_height=size,
        )
        return Image.open(io.BytesIO(png_data)).convert('RGBA')
    else:
        return Image.open(path).convert('RGBA').resize((size, size), Image.LANCZOS)


def _ensure_templates(pieces: List[PieceInfo]) -> Dict[str, np.ndarray]:
    """Render all piece templates to numpy arrays for template matching."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates = {}
    for p in pieces:
        cache_path = TEMPLATES_DIR / f"{p.name}.png"
        if cache_path.exists():
            img = cv2.imread(str(cache_path), cv2.IMREAD_UNCHANGED)
        else:
            pil_img = _render_counter(p)
            pil_img.save(str(cache_path))
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGBA2BGRA)
        templates[p.name] = img
    return templates


# ---------------------------------------------------------------------------
# Image composition (encoding)
# ---------------------------------------------------------------------------

def _compose_board(
    positions: Dict[str, Tuple[int, int]],
    piece_lookup: Dict[str, PieceInfo],
) -> Image.Image:
    """Compose the board image with pieces placed at their hex positions."""
    map_path = _ensure_map_rendered()
    board = Image.open(map_path).convert('RGBA')

    for name, (col, row) in positions.items():
        piece = piece_lookup.get(name)
        if piece is None:
            continue
        px, py = hex_to_pixel(col, row)
        counter = _render_counter(piece)

        # Center the counter on the hex
        cx = int(px - counter.width / 2)
        cy = int(py - counter.height / 2)

        # Clip to image bounds
        if 0 <= cx < board.width - counter.width and 0 <= cy < board.height - counter.height:
            board.paste(counter, (cx, cy), counter)  # alpha composite

    return board.convert('RGB')


# ---------------------------------------------------------------------------
# Template matching (decoding)
# ---------------------------------------------------------------------------

def _detect_pieces(
    board_img: np.ndarray,
    templates: Dict[str, np.ndarray],
    threshold: float = 0.75,
) -> Dict[str, Tuple[int, int]]:
    """Detect piece positions in the board image using template matching.

    Returns {piece_name: (abs_col, map_row)} for each detected piece.
    """
    gray_board = cv2.cvtColor(board_img, cv2.COLOR_BGR2GRAY)
    detected = {}

    for name, template in templates.items():
        # Convert template to grayscale (ignore alpha — masks cause NaN issues)
        if len(template.shape) == 3 and template.shape[2] >= 3:
            gray_tmpl = cv2.cvtColor(template[:, :, :3], cv2.COLOR_BGR2GRAY)
        else:
            gray_tmpl = template

        # Template matching (no mask — counter SVGs have solid backgrounds)
        result = cv2.matchTemplate(gray_board, gray_tmpl, cv2.TM_CCOEFF_NORMED)

        # Find best match
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        if max_val >= threshold:
            # max_loc is top-left corner of match
            cx = max_loc[0] + gray_tmpl.shape[1] // 2
            cy = max_loc[1] + gray_tmpl.shape[0] // 2

            # Snap to nearest hex
            col, row = pixel_to_hex(cx, cy)
            detected[name] = (col, row)

    return detected


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_capacity(key: str) -> dict:
    """Return encoding capacity information."""
    kb = _key_bytes(key)
    pcs = all_pieces()
    n_hex = n_positions()
    bits_per_piece = math.log2(n_hex)

    # For 50 chars (worst case ~200 bytes UTF-8 + 2 byte header):
    needed_bits = 202 * 8
    n_signal = math.ceil(needed_bits / bits_per_piece)

    return {
        'total_pieces': len(pcs),
        'playable_hexes': n_hex,
        'bits_per_piece': bits_per_piece,
        'signal_pieces_for_50_chars': n_signal,
        'max_message_bytes': int(len(pcs) * bits_per_piece / 8) - 2,
    }


NONCE_BYTES = 16    # 128-bit per-message nonce
MAX_MSG_BYTES = 52  # 50 chars + 2-byte length prefix


def _nonce_piece_count() -> int:
    """How many pieces needed to encode NONCE_BYTES."""
    n_hex = n_positions()
    bits_per_piece = math.log2(n_hex)
    return math.ceil((NONCE_BYTES + 2) * 8 / bits_per_piece)  # +2 for length prefix


def _msg_piece_count() -> int:
    """How many pieces needed to encode MAX_MSG_BYTES."""
    n_hex = n_positions()
    bits_per_piece = math.log2(n_hex)
    return math.ceil(MAX_MSG_BYTES * 8 / bits_per_piece)


def encode_message(
    message: str,
    key: str = "",
    n_noise: int = 80,
    output_path: Optional[str] = None,
) -> bytes:
    """Encode a message into a CNA board image.

    Two-layer encoding — no key management needed:
      Layer 1: A random session nonce is encoded into "key pieces"
               selected by the hardcoded system key.
      Layer 2: The message is encoded into "message pieces"
               selected by a working key derived from system key + nonce.

    Parameters
    ----------
    message : str
        Secret message (up to 50 characters).
    key : str
        Optional extra passphrase (adds a layer on top of the system key).
        If empty, the system key alone is used.
    n_noise : int
        Number of noise (camouflage) pieces to place.
    output_path : str, optional
        If given, save PNG to this path.

    Returns
    -------
    bytes : PNG image data.
    """
    pcs = all_pieces()

    msg_bytes = message.encode('utf-8')
    if len(msg_bytes) > MAX_MSG_BYTES - 2:
        raise ValueError(f"Message too long: {len(msg_bytes)} bytes, max {MAX_MSG_BYTES - 2}")

    # Combine system key with optional user passphrase
    base_key = _SYSTEM_KEY
    if key:
        base_key = _hkdf(_SYSTEM_KEY, b"cna-user-key", _key_bytes(key), 32)

    # --- Layer 1: Nonce ---
    # Generate random nonce, encode it via key pieces
    nonce = os.urandom(NONCE_BYTES)
    n_key_pieces = _nonce_piece_count()
    key_pieces = _select_signal_pieces(base_key, n_key_pieces, pcs)

    nonce_framed = len(nonce).to_bytes(2, 'big') + nonce
    key_positions = _bytes_to_positions(nonce_framed, base_key, key_pieces,
                                         hkdf_label=b"cna-nonce")

    # --- Layer 2: Message ---
    # Derive working key from base_key + nonce
    working_key = _hkdf(base_key, b"cna-session", nonce, 32)

    # Select message pieces from the remaining pool (exclude key pieces)
    key_piece_images = {p.image for p in key_pieces}
    remaining_pcs = [p for p in pcs if p.image not in key_piece_images]
    n_msg_pieces = _msg_piece_count()
    msg_pieces = _select_signal_pieces(working_key, n_msg_pieces, remaining_pcs)

    # Encode message, excluding hexes used by key pieces
    occupied_by_keys = set(key_positions.values())
    msg_positions = _message_to_positions(message, working_key, msg_pieces,
                                           excluded_hexes=occupied_by_keys)

    # --- Noise ---
    all_signal = key_pieces + msg_pieces
    occupied = occupied_by_keys | set(msg_positions.values())
    noise = _select_noise_pieces(base_key, all_signal, pcs, n_noise)
    noise_positions = _assign_noise_positions(base_key, noise, occupied)

    # Combine all positions
    all_positions = {}
    all_positions.update(key_positions)
    all_positions.update(msg_positions)
    all_positions.update(noise_positions)

    # Build piece lookup and compose
    piece_lookup = {p.name: p for p in pcs}
    board = _compose_board(all_positions, piece_lookup)

    buf = io.BytesIO()
    board.save(buf, format='PNG', optimize=True)
    png_bytes = buf.getvalue()

    if output_path:
        with open(output_path, 'wb') as f:
            f.write(png_bytes)

    return png_bytes


def decode_message(
    png_data: bytes,
    key: str = "",
    threshold: float = 0.75,
) -> str:
    """Decode a message from a CNA board image.

    Reverses two-layer encoding:
      1. Detect key pieces (system key) → recover session nonce
      2. Derive working key → detect message pieces → recover message

    Parameters
    ----------
    png_data : bytes
        PNG image data of the CNA board.
    key : str
        Optional extra passphrase (must match what was used to encode).
    threshold : float
        Template matching confidence threshold (0-1).

    Returns
    -------
    str : The decoded message.
    """
    pcs = all_pieces()

    base_key = _SYSTEM_KEY
    if key:
        base_key = _hkdf(_SYSTEM_KEY, b"cna-user-key", _key_bytes(key), 32)

    # Load board image once
    board_img = cv2.imdecode(
        np.frombuffer(png_data, np.uint8),
        cv2.IMREAD_COLOR,
    )

    # --- Layer 1: Recover nonce ---
    n_key_pieces = _nonce_piece_count()
    key_pieces = _select_signal_pieces(base_key, n_key_pieces, pcs)
    key_templates = _ensure_templates(key_pieces)
    key_detected = _detect_pieces(board_img, key_templates, threshold=threshold)

    # Decode nonce (read full padded payload, extract length-prefixed nonce)
    n_hex = n_positions()
    cap_bits = sum(math.log2(n_hex - i) for i in range(n_key_pieces))
    cap_bytes = int(cap_bits) // 8
    nonce_padded = _positions_to_bytes(key_detected, base_key, key_pieces,
                                        payload_len=cap_bytes, hkdf_label=b"cna-nonce")
    nonce_len = int.from_bytes(nonce_padded[:2], 'big')
    nonce = nonce_padded[2:2 + nonce_len]

    # --- Layer 2: Recover message ---
    working_key = _hkdf(base_key, b"cna-session", nonce, 32)

    key_piece_images = {p.image for p in key_pieces}
    remaining_pcs = [p for p in pcs if p.image not in key_piece_images]
    n_msg_pieces = _msg_piece_count()
    msg_pieces = _select_signal_pieces(working_key, n_msg_pieces, remaining_pcs)

    msg_templates = _ensure_templates(msg_pieces)
    msg_detected = _detect_pieces(board_img, msg_templates, threshold=threshold)

    # Decode message, using same hex exclusion as encoder
    occupied_by_keys = set(key_detected.values())
    return _positions_to_message(msg_detected, working_key, msg_pieces,
                                  excluded_hexes=occupied_by_keys)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Simple CLI for testing."""
    import argparse

    parser = argparse.ArgumentParser(description="CNA Image Steganography")
    sub = parser.add_subparsers(dest='cmd')

    enc = sub.add_parser('encode')
    enc.add_argument('--message', '-m', required=True)
    enc.add_argument('--key', '-k', required=True)
    enc.add_argument('--out', '-o', default='encoded_board.png')
    enc.add_argument('--noise', type=int, default=80)

    dec = sub.add_parser('decode')
    dec.add_argument('--image', '-i', required=True)
    dec.add_argument('--key', '-k', required=True)
    dec.add_argument('--threshold', type=float, default=0.75)

    info = sub.add_parser('info')
    info.add_argument('--key', '-k', default='test')

    args = parser.parse_args()

    if args.cmd == 'encode':
        print(f"Encoding: {args.message!r}")
        png = encode_message(args.message, args.key, n_noise=args.noise,
                           output_path=args.out)
        print(f"Written {len(png):,} bytes to {args.out}")

    elif args.cmd == 'decode':
        with open(args.image, 'rb') as f:
            png = f.read()
        text = decode_message(png, args.key, threshold=args.threshold)
        print(f"Decoded: {text!r}")

    elif args.cmd == 'info':
        cap = compute_capacity(args.key)
        print(f"Total pieces:        {cap['total_pieces']}")
        print(f"Playable hexes:      {cap['playable_hexes']}")
        print(f"Bits per piece:      {cap['bits_per_piece']:.1f}")
        print(f"Signal pieces (50c): {cap['signal_pieces_for_50_chars']}")
        print(f"Max message bytes:   {cap['max_message_bytes']}")

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
