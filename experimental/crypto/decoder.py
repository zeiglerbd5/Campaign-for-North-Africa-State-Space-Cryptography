"""
cna_ssc/crypto/decoder.py

Decode a secret message from a steganographic Vassal .vsav file.

OVERVIEW
--------
1. Parse the .vsav to extract {piece_name: location_id} for all pieces.
2. Reconstruct the canonical ordered active piece list from the registry
   (same filtering as the encoder: pieces present in this .vsav).
3. Encode the game state through the restricted bijection → integer N.
4. Extract the 4-byte length header from the low-order bits of N.
5. Extract the full payload (header + message body).
6. Unframe to recover the original message bytes.

SECURITY NOTE
-------------
The decoder requires the same piece_registry_data.json as the encoder.
The .vsav itself implicitly defines which pieces participate (matching
the template selection made during encoding).
"""

from __future__ import annotations

import os
from typing import List, Optional

from experimental.engine import bijection
from experimental.engine import mixed_radix as mr
from experimental.formats.vsav_codec import parse_vsav, vsav_to_locations


# ──────────────────────────────────────────────────────────────────────────────
# Decoder
# ──────────────────────────────────────────────────────────────────────────────

def _active_names_from_vsav(vsav_path: str) -> List[str]:
    """Get canonical ordered active piece list — must match encoder exactly."""
    from experimental.crypto.encoder import active_piece_names
    return active_piece_names(vsav_path)


def decode(vsav_path: str, template_vsav: Optional[str] = None) -> bytes:
    """Decode a secret message from a .vsav file.

    Parameters
    ----------
    vsav_path : str
        Path to the steganographic .vsav file.
    template_vsav : str, optional
        Path to the template .vsav used during encoding.  The template
        defines which pieces participate in the bijection (the key).
        If None, the encoded vsav itself is used — only works when all
        encoded pieces remain at non-default positions.

    Returns
    -------
    bytes : The original message bytes.
    """
    key_vsav = template_vsav or vsav_path

    # Step 1: Parse game state from the steganographic vsav
    locations = vsav_to_locations(vsav_path)

    # Step 2: Reconstruct active piece list using the TEMPLATE (shared key)
    active_names = _active_names_from_vsav(key_vsav)

    # Step 3: Encode to restricted state integer
    N = bijection.encode_restricted(locations, active_names)

    # Step 4: Extract length trailer (4 bytes) from low-order bits of N
    # frame_message puts length at the END → low-order bits of the integer
    trailer = mr.extract_message(N, mr.FRAME_HEADER_BYTES)
    message_length = int.from_bytes(trailer, byteorder="big")

    if message_length < 0:
        raise ValueError(f"Corrupt frame header: decoded length {message_length} < 0")

    # Sanity check against restricted capacity
    radices = bijection.restricted_radices(active_names)
    max_possible = int(mr.bits(radices) // 8) - mr.FRAME_HEADER_BYTES
    if message_length > max_possible:
        raise ValueError(
            f"Decoded length {message_length} bytes exceeds maximum "
            f"possible {max_possible} bytes. "
            f"Check that you are using the correct piece registry (key)."
        )

    # Step 5: Extract full framed payload (message + 4-byte trailer)
    total_length = message_length + mr.FRAME_HEADER_BYTES
    framed = mr.extract_message(N, total_length)

    # Step 6: Unframe
    return mr.unframe_message(framed)


def decode_to_file(vsav_path: str, output_path: str) -> int:
    """Decode a message from .vsav and write to a file."""
    message = decode(vsav_path)
    with open(output_path, "wb") as f:
        f.write(message)
    return len(message)


def decode_to_text(vsav_path: str, encoding: str = "utf-8") -> str:
    """Decode a message and interpret as text."""
    return decode(vsav_path).decode(encoding)


# ──────────────────────────────────────────────────────────────────────────────
# Diagnostic
# ──────────────────────────────────────────────────────────────────────────────

def inspect(vsav_path: str) -> dict:
    """Return diagnostic information about a .vsav file."""
    import math
    locations = vsav_to_locations(vsav_path)
    active_names = _active_names_from_vsav(vsav_path)
    radices = bijection.restricted_radices(active_names)
    N = bijection.encode_restricted(locations, active_names)

    header = mr.extract_message(N, mr.FRAME_HEADER_BYTES)
    decoded_length = int.from_bytes(header, byteorder="big")

    return {
        "n_pieces":              len(locations),
        "n_active_pieces":       len(active_names),
        "restricted_bits":       mr.bits(radices),
        "state_int":             str(N),
        "state_bits":            math.log2(N + 1) if N > 0 else 0,
        "header_peek":           header.hex(),
        "decoded_length_claim":  decoded_length,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m experimental.crypto.decoder <file.vsav>")
        sys.exit(1)
    vsav_path = sys.argv[1]
    info = inspect(vsav_path)
    print(f"Pieces found:          {info['n_pieces']}")
    print(f"Active pieces (key):   {info['n_active_pieces']}")
    print(f"Restricted bits:       {info['restricted_bits']:.0f}")
    print(f"State integer bits:    {info['state_bits']:.1f}")
    print(f"Header (hex):          {info['header_peek']}")
    print(f"Claimed message len:   {info['decoded_length_claim']} bytes")
    print(f"State integer (first 60 digits): {info['state_int'][:60]}...")

