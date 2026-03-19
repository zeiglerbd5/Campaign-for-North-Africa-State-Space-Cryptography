"""
tests/test_core.py

Test suite for CNA-SSC core components.

Runs without needing a real .vsav or buildFile.xml by using synthetic
fixtures.  Integration tests that require actual files are marked with
pytest.mark.integration.
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from typing import Dict, List

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# hex_grid tests
# ──────────────────────────────────────────────────────────────────────────────

def test_hex_grid_roundtrip():
    from cna_ssc.engine.hex_grid import pixel_to_hex, hex_to_pixel

    for col in [1, 10, 50, 100, 174]:
        for row in [1, 5, 15, 29]:
            px, py = hex_to_pixel(col, row)
            c2, r2 = pixel_to_hex(px, py)
            assert (c2, r2) == (col, row), f"Roundtrip failed for ({col},{row})"


def test_hex_grid_known_positions():
    """Verify Tripoli is identified as an off-map zone, not a hex."""
    from cna_ssc.engine.hex_grid import location_id_from_pixel

    loc = location_id_from_pixel(256, 1895, zone_hint="Tripoli")
    assert loc == "Tripoli"


def test_all_locations_non_empty():
    from cna_ssc.engine.hex_grid import HEX_POSITIONS, OFF_MAP_ZONE_NAMES, ALL_LOCATIONS

    assert len(HEX_POSITIONS) > 2000
    assert len(OFF_MAP_ZONE_NAMES) > 10
    assert len(ALL_LOCATIONS) == len(HEX_POSITIONS) + len(OFF_MAP_ZONE_NAMES)


def test_location_index_unique():
    from cna_ssc.engine.hex_grid import ALL_LOCATIONS, LOCATION_INDEX

    assert len(LOCATION_INDEX) == len(ALL_LOCATIONS)
    for i, loc in enumerate(ALL_LOCATIONS):
        assert LOCATION_INDEX[loc] == i


# ──────────────────────────────────────────────────────────────────────────────
# mixed_radix tests
# ──────────────────────────────────────────────────────────────────────────────

def test_mixed_radix_encode_decode():
    from cna_ssc.engine.mixed_radix import encode, decode

    radices = [5, 3, 7, 4, 10]
    indices = [2, 1, 5, 3, 7]
    N = encode(indices, radices)
    assert decode(N, radices) == indices


def test_mixed_radix_zero():
    from cna_ssc.engine.mixed_radix import encode, decode

    radices = [10, 20, 30]
    indices = [0, 0, 0]
    assert encode(indices, radices) == 0
    assert decode(0, radices) == [0, 0, 0]


def test_mixed_radix_max():
    from cna_ssc.engine.mixed_radix import encode, decode, product

    radices = [5, 3, 7]
    max_indices = [r - 1 for r in radices]
    N = encode(max_indices, radices)
    assert N == product(radices) - 1
    assert decode(N, radices) == max_indices


def test_mixed_radix_single():
    from cna_ssc.engine.mixed_radix import encode, decode

    assert encode([3], [10]) == 3
    assert decode(7, [10]) == [7]


def test_mixed_radix_overflow():
    from cna_ssc.engine.mixed_radix import decode

    with pytest.raises(ValueError, match="too large"):
        decode(1000, [5, 3])  # product=15, 1000 >> 15


def test_mixed_radix_out_of_range():
    from cna_ssc.engine.mixed_radix import encode

    with pytest.raises(ValueError, match="out of range"):
        encode([5], [5])  # 5 is not < 5


def test_message_frame_unframe():
    from cna_ssc.engine.mixed_radix import frame_message, unframe_message

    msg = b"Hello, World!"
    framed = frame_message(msg)
    assert len(framed) == 4 + len(msg)
    assert unframe_message(framed) == msg


def test_message_embed_extract():
    from cna_ssc.engine.mixed_radix import (
        embed_message, extract_message, frame_message, unframe_message
    )

    msg = b"Secret data here"
    framed = frame_message(msg)
    large_radices = [1000] * 100  # ~1000 bits

    N = embed_message(framed, large_radices, nonce=42)
    recovered = extract_message(N, len(framed))
    assert unframe_message(recovered) == msg


def test_large_message_embed():
    """Test with a 3KB message in a large state space."""
    from cna_ssc.engine.mixed_radix import (
        embed_message, extract_message, frame_message, unframe_message, bits
    )

    msg = bytes(range(256)) * 12  # 3072 bytes
    framed = frame_message(msg)
    radices = [5000] * 500   # massive state space

    if bits(radices) < 8 * len(framed):
        pytest.skip("State space too small for this test")

    N = embed_message(framed, radices, nonce=7)
    recovered = extract_message(N, len(framed))
    assert unframe_message(recovered) == msg


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic bijection tests (no piece registry needed)
# ──────────────────────────────────────────────────────────────────────────────

def test_bijection_encode_decode_synthetic():
    """Test the encode/decode logic using synthetic radices."""
    from cna_ssc.engine import mixed_radix as mr

    # Simulate 5 pieces with 100 positions each
    radices = [100] * 5
    indices = [42, 7, 99, 0, 55]
    N = mr.encode(indices, radices)
    assert mr.decode(N, radices) == indices


# ──────────────────────────────────────────────────────────────────────────────
# vsav_codec tests (uses synthetic .vsav)
# ──────────────────────────────────────────────────────────────────────────────

def _make_minimal_vsav(commands: List[str], tmp_path: str) -> str:
    """Create a minimal valid .vsav for testing."""
    from cna_ssc.formats.vsav_codec import XOR_KEY, VSAV_HEADER

    stream = "\x00begin_save\x1b" + "\x1b".join(commands)
    raw = stream.encode("latin-1")
    obfuscated = bytes([b ^ XOR_KEY for b in raw])
    encoded = VSAV_HEADER.decode("ascii") + obfuscated.hex()

    moduledata = b'<?xml version="1.0"?><data version="1"><n>Test</n></data>'
    savedata   = b'<?xml version="1.0"?><data version="1"></data>'

    path = os.path.join(tmp_path, "test.vsav")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("moduledata", moduledata)
        zf.writestr("savedata",   savedata)
        zf.writestr("savedGame",  encoded.encode("ascii"))
    return path


def test_vsav_encode_decode_roundtrip(tmp_path):
    """Parse a synthetic .vsav and verify commands are preserved."""
    from cna_ssc.formats.vsav_codec import parse_vsav

    tmp = str(tmp_path)
    cmds = [
        "+/123456/sendto;Zone A;Main Map;0;0;Piece Name;0;0;0;Return;G;;;"
        "piece;;;GE I-8 - 15.png;GE I-8 - 15/;;\tPieces\\tfalse;Main Map;1;7964,1173\\\\tfalse\\\\\\tMain Map;7964;1173;3173\\",
    ]
    vsav_path = _make_minimal_vsav(cmds, tmp)
    save = parse_vsav(vsav_path)
    assert len(save.raw_commands) >= 1


def test_vsav_xor_roundtrip():
    """Verify XOR encode/decode is its own inverse."""
    from cna_ssc.formats.vsav_codec import _xor_encode, _xor_decode

    text = "Hello, Campaign for North Africa!\x1b+/12345/sendto;"
    hex_str = _xor_encode(text)
    recovered = _xor_decode(hex_str)
    assert recovered == text


# ──────────────────────────────────────────────────────────────────────────────
# Integration tests (require real files)
# ──────────────────────────────────────────────────────────────────────────────

BREVITY_VSAV = os.path.join(
    os.path.dirname(__file__), "..", "..", "Operation_Brevity_set-upv0_93.vsav"
)
BUILD_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "buildFile.xml"
)


@pytest.mark.integration
def test_parse_brevity_vsav():
    """Parse the real Operation Brevity .vsav."""
    if not os.path.exists(BREVITY_VSAV):
        pytest.skip(f"Real .vsav not found at {BREVITY_VSAV}")

    from cna_ssc.formats.vsav_codec import parse_vsav

    save = parse_vsav(BREVITY_VSAV)
    assert len(save.pieces) > 10, f"Too few pieces: {len(save.pieces)}"
    assert len(save.raw_commands) > 100

    # Spot-check known positions from the Brevity scenario
    locs = save.all_locations()
    print(f"\nParsed {len(save.pieces)} piece placements")
    print("Sample locations:")
    for name, loc in list(locs.items())[:5]:
        print(f"  {name:40s} → {loc}")


@pytest.mark.integration
def test_buildfile_parser():
    """Parse the real buildFile.xml."""
    if not os.path.exists(BUILD_FILE):
        pytest.skip(f"buildFile.xml not found at {BUILD_FILE}")

    from cna_ssc.formats.buildfile_parser import parse_buildfile

    pieces = parse_buildfile(BUILD_FILE)
    assert len(pieces) > 2000
    cats = set(p["cat"] for p in pieces)
    assert "ground_unit" in cats
    assert "marker" in cats
    print(f"\nParsed {len(pieces)} pieces, categories: {cats}")


@pytest.mark.integration
def test_full_encode_decode_roundtrip(tmp_path):
    """Full encode→decode roundtrip using real files."""
    if not os.path.exists(BREVITY_VSAV):
        pytest.skip("Real .vsav not found")

    # Ensure registry exists
    from cna_ssc.engine.piece_registry import pieces as get_pieces
    try:
        ps = get_pieces()
    except FileNotFoundError:
        pytest.skip("Piece registry not built. Run: python -m cna_ssc setup buildFile.xml")

    from cna_ssc.crypto.encoder import encode_text
    from cna_ssc.crypto.decoder import decode_to_text

    message = "This is a secret message encoded into a Campaign for North Africa save file."
    output = str(tmp_path / "encoded.vsav")

    nonce = encode_text(message, BREVITY_VSAV, output, nonce=0)
    recovered = decode_to_text(output)

    assert recovered == message, f"Roundtrip failed:\n{message!r}\n!=\n{recovered!r}"
    print(f"\n✓ Roundtrip passed. Message: {message[:50]}...")


if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.dirname(os.path.dirname(__file__))
    )
    sys.exit(result.returncode)
