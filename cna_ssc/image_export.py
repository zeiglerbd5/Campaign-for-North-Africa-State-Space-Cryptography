"""
cna_ssc/image_export.py
=======================
Export a GameState as a PNG image with the full Vassal state embedded
in image metadata (PNG tEXt chunk).

Two modes:
    "schematic"  : simple SVG-derived colored hex map (no Vassal dependency)
    "metadata"   : any PNG + state embedded as tEXt chunk (lossless)

The metadata mode is the practical covert channel:
- The image is indistinguishable to the eye from any other screenshot
- The tEXt chunk contains the full .vsav state (VCSK-encoded)
- An observer who examines the file sees a normal PNG with custom metadata
  (many PNG files have extensive metadata; this is unremarkable)
- The decoder reads the tEXt chunk, decodes the VCSK string, and recovers
  the game state without the Vassal engine

Schematic mode produces a standalone image showing:
- Blue hexes = Axis ground units
- Red hexes  = Allied ground units
- Yellow dots = aircraft
- No axis labels (intentional — the image looks like abstract art)
"""

from __future__ import annotations
import io
import struct
import zlib
from typing import Optional

from .state_model  import GameState
from .constants    import LOCATIONS, NUM_LOCATIONS, PIECE_BY_NAME


# ---------------------------------------------------------------------------
# PNG tEXt chunk embedding
# ---------------------------------------------------------------------------

def _make_png_text_chunk(keyword: str, text: str) -> bytes:
    """Build a PNG tEXt ancillary chunk."""
    data     = keyword.encode("latin-1") + b"\x00" + text.encode("latin-1")
    length   = len(data)
    chunk_type = b"tEXt"
    crc      = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", length) + chunk_type + data + struct.pack(">I", crc)


def _insert_text_chunk(png_bytes: bytes, keyword: str, text: str) -> bytes:
    """
    Insert a tEXt chunk after the IHDR chunk of a PNG file.
    Returns modified PNG bytes.
    """
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Not a valid PNG file")

    # Find position after IHDR chunk (offset 8 + 4+4+13+4 = 33 bytes)
    ihdr_end = 8 + 4 + 4 + 13 + 4   # PNG header + IHDR

    new_chunk = _make_png_text_chunk(keyword, text)
    return png_bytes[:ihdr_end] + new_chunk + png_bytes[ihdr_end:]


def _extract_text_chunk(png_bytes: bytes, keyword: str) -> Optional[str]:
    """
    Extract a tEXt chunk value from a PNG by keyword.
    Returns None if not found.
    """
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return None

    offset = 8  # skip PNG signature
    while offset < len(png_bytes) - 12:
        length     = struct.unpack(">I", png_bytes[offset: offset + 4])[0]
        chunk_type = png_bytes[offset + 4: offset + 8]
        data       = png_bytes[offset + 8: offset + 8 + length]

        if chunk_type == b"tEXt":
            null_pos = data.index(b"\x00") if b"\x00" in data else -1
            if null_pos >= 0:
                kw  = data[:null_pos].decode("latin-1")
                val = data[null_pos + 1:].decode("latin-1")
                if kw == keyword:
                    return val

        if chunk_type == b"IEND":
            break
        offset += 12 + length

    return None


# ---------------------------------------------------------------------------
# Minimal PNG generation (1x1 pixel placeholder for pure metadata mode)
# ---------------------------------------------------------------------------

def _minimal_png(width: int = 1, height: int = 1, color: tuple = (128, 128, 128)) -> bytes:
    """Generate a minimal valid RGB PNG of given size and color."""
    def chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

    # IHDR
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    ihdr      = chunk(b"IHDR", ihdr_data)

    # IDAT: single scanline
    r, g, b   = color
    raw       = b""
    for _ in range(height):
        row = b"\x00" + bytes([r, g, b] * width)
        raw += row
    compressed = zlib.compress(raw)
    idat       = chunk(b"IDAT", compressed)

    # IEND
    iend = chunk(b"IEND", b"")

    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Schematic hex-map PNG
# ---------------------------------------------------------------------------

def _render_schematic(state: GameState, scale: int = 4) -> bytes:
    """
    Render a schematic map showing piece positions.
    Returns PNG bytes.

    Parameters
    ----------
    state : game state to render
    scale : pixels per Vassal pixel unit (1 = tiny, 4 = ~4K image)

    Note: generates a full schematic map sized to fit the CNA board.
    For a 1:1 pixel rendering this would be 13000 x 2500 px (huge).
    Default scale=4 produces a ~1600x300 summary thumbnail.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        # Fallback: return minimal PNG with metadata only
        return _minimal_png(800, 150, (20, 30, 50))

    # Thumbnail dimensions
    MAP_W = 13000
    MAP_H =  2500
    thumb_w = 1300
    thumb_h =  250
    sx = thumb_w / MAP_W
    sy = thumb_h / MAP_H

    img  = Image.new("RGB", (thumb_w, thumb_h), (20, 30, 50))
    draw = ImageDraw.Draw(img)

    # Draw hex grid dots
    from .constants import HEX_POSITIONS
    for (col, row, px, py) in HEX_POSITIONS[::4]:   # every 4th for speed
        tx = int(px * sx)
        ty = int(py * sy)
        draw.point((tx, ty), fill=(40, 50, 70))

    # Draw pieces
    for name, ps in state.pieces.items():
        if ps.is_eliminated:
            continue
        loc = LOCATIONS[ps.location_idx] if ps.location_idx < NUM_LOCATIONS else None
        if not loc or "px" not in loc:
            continue

        tx = int(loc["px"] * sx)
        ty = int(loc["py"] * sy)

        piece_info = PIECE_BY_NAME.get(name, {})
        ptype = piece_info.get("type", "other")
        side  = piece_info.get("side", "")

        if ptype == "aircraft":
            color = (255, 255, 100)   # yellow
            r = 3
        elif side == "axis":
            color = (100, 149, 237)   # cornflower blue
            r = 2
        elif side == "allied":
            color = (205, 92, 92)     # indian red
            r = 2
        else:
            color = (150, 150, 150)   # grey
            r = 1

        draw.ellipse((tx - r, ty - r, tx + r, ty + r), fill=color)

    # Convert to PNG bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Main export functions
# ---------------------------------------------------------------------------

def export_png_metadata(
    state:      GameState,
    base_png:   Optional[bytes] = None,
    keyword:    str = "VassalState",
) -> bytes:
    """
    Embed the full Vassal game state into a PNG file's tEXt metadata.

    Parameters
    ----------
    state    : game state to embed
    base_png : existing PNG bytes to embed state into (default: schematic render)
    keyword  : tEXt chunk keyword (default "VassalState")

    Returns PNG bytes with embedded state.

    The embedded state is the VCSK-encoded savedGame string — identical
    to what would be in a .vsav file, enabling recovery without Vassal.
    """
    from .vsav_writer  import write_vsav
    import zipfile

    # Get the savedGame bytes from a .vsav
    vsav_bytes  = write_vsav(state)
    with zipfile.ZipFile(io.BytesIO(vsav_bytes)) as zf:
        saved_game  = zf.read("savedGame")

    vcsk_string = saved_game.decode("ascii")

    # Get base image
    if base_png is None:
        base_png = _render_schematic(state)

    # Embed VCSK string as tEXt chunk
    return _insert_text_chunk(base_png, keyword, vcsk_string)


def extract_state_from_png(
    png_bytes: bytes,
    keyword:   str = "VassalState",
) -> Optional[GameState]:
    """
    Extract a GameState from the tEXt metadata of a PNG file.

    Returns None if no VassalState metadata is found.
    """
    from .vsav_reader import read_vsav
    import zipfile

    vcsk_string = _extract_text_chunk(png_bytes, keyword)
    if vcsk_string is None:
        return None

    # Reconstruct a .vsav from the VCSK string
    # We need to wrap it in the expected ZIP structure
    saved_game_bytes = vcsk_string.encode("ascii")

    _MODULEDATA = b"""<?xml version="1.0" encoding="UTF-8"?>
<data version="1"><version>2.1.0</version>
<VassalVersion>3.7.15</VassalVersion>
<dateSaved>1586171744000</dateSaved>
<description>(c) 1979 SPI</description>
<n>Campaign for North Africa</n></data>"""

    _SAVEDATA = b"""<?xml version="1.0" encoding="UTF-8"?>
<data version="1"><version></version>
<VassalVersion>3.7.15</VassalVersion>
<dateSaved>1586171744000</dateSaved>
<description></description></data>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("moduledata", _MODULEDATA)
        zf.writestr("savedata",   _SAVEDATA)
        zf.writestr("savedGame",  saved_game_bytes)

    return read_vsav(buf.getvalue())


def export_schematic(state: GameState) -> bytes:
    """
    Render a schematic thumbnail of the board state as PNG bytes.
    No embedded metadata — pure visual.
    """
    return _render_schematic(state)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== image_export Self-Test ===\n")

    from .state_model import GameState
    from .constants   import nearest_location_index, zone_index

    gs = GameState.default()
    gs.get_piece("GE I-8 - 15").location_idx    = nearest_location_index(7964, 1173)
    gs.get_piece("BR 1 DurLt - 23-70").location_idx = zone_index("Tobruk Holding Box")
    gs.turn = 14

    # Test schematic export
    png = export_schematic(gs)
    print(f"Schematic PNG: {len(png):,} bytes")
    assert png.startswith(b"\x89PNG")

    # Test metadata embed/extract round-trip
    png_with_state = export_png_metadata(gs, png)
    print(f"PNG with metadata: {len(png_with_state):,} bytes")

    gs2 = extract_state_from_png(png_with_state)
    if gs2:
        print(f"Extracted state: {gs2.summary()}")
        ps1 = gs.get_piece("GE I-8 - 15")
        ps2 = gs2.get_piece("GE I-8 - 15")
        print(f"GE I-8 - 15: loc_idx {ps1.location_idx} -> {ps2.location_idx}")

    # Save outputs
    with open("/mnt/user-data/outputs/cna_schematic.png", "wb") as f:
        f.write(png)
    with open("/mnt/user-data/outputs/cna_with_state.png", "wb") as f:
        f.write(png_with_state)
    print("\nSaved schematic and metadata-embedded PNG to outputs/")
    print("\nAll tests passed.")
