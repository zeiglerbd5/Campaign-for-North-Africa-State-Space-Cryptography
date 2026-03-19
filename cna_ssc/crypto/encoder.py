"""
cna_ssc/crypto/encoder.py

Encode a secret message into a Vassal .vsav file.

OVERVIEW
--------
1. Parse the template .vsav to discover which pieces it contains.
   These ~1,000-1,500 pieces form the RESTRICTED bijection key.
2. Frame the message with a 4-byte length prefix.
3. Embed the framed message into a state-space integer N using the
   restricted bijection (only template pieces participate).
4. Decode N → game state dict for the active pieces.
5. Apply those positions to the template .vsav (replacing pixel coords).
6. Write the output .vsav.

The restricted state space is still enormous: ~1,200 pieces × ~5,000
positions each ≈ 10^4,400 states — vastly exceeding any cryptographic
system and completely quantum-resistant.

SECURITY NOTES
--------------
- The template .vsav is part of the key (it defines which pieces participate).
- The canonical location ordering per piece is part of the key.
- Both parties need the same `piece_registry_data.json` AND the same template.
- Nonce randomisation provides semantic security (same message ≠ same .vsav).
"""

from __future__ import annotations

import os
import secrets
from typing import Dict, List, Optional

from cna_ssc.engine import bijection
from cna_ssc.engine import mixed_radix as mr
from cna_ssc.formats.vsav_codec import parse_vsav, write_vsav, VassalSave


# ──────────────────────────────────────────────────────────────────────────────
# Capacity helpers
# ──────────────────────────────────────────────────────────────────────────────

def active_piece_names(template_vsav: str) -> List[str]:
    """Return canonical ordered list of relocatable piece names from template.

    Only pieces on the main map (or in named holding-box zones) are included.
    Pieces in Org Chart panels have display-only coordinates that cannot be
    freely relocated to arbitrary hex positions.

    Pieces with duplicate names are excluded if their last-seen copy (which
    the vsav reader stores in _piece_map) is not on the main map — because
    the reader would return the OC location, not the map location.
    """
    save = parse_vsav(template_vsav)
    from cna_ssc.engine.piece_registry import pieces as all_pieces
    registry_names = {p.name for p in all_pieces()}

    # A piece is relocatable if:
    #   - it's in the registry
    #   - it has meaningful non-zero pixel coordinates in any command
    #   - its LAST occurrence (_piece_map entry) is on the main map or a
    #     named off-map zone (not in OC / at 1,1 with zero pixels)
    present = set()
    for p in save.pieces:
        if p.name not in registry_names:
            continue
        if p.pixel_x <= 0 and p.pixel_y <= 0:
            continue
        # Only include if the canonical (last-seen) location is sensible
        canonical = save._piece_map.get(p.name)
        if canonical and canonical.location_id != "1,1":
            present.add(p.name)
        elif canonical and canonical.location_id == "1,1" and canonical.pixel_x > 0:
            present.add(p.name)

    return [p.name for p in all_pieces() if p.name in present]


def capacity_bits(template_vsav: str) -> float:
    """Return steganographic capacity in bits for a given template."""
    names = active_piece_names(template_vsav)
    return bijection.restricted_capacity_bits(names)


def max_message_bytes(template_vsav: str) -> int:
    """Return the maximum message payload size in bytes for a given template."""
    bits = capacity_bits(template_vsav)
    return int(bits // 8) - mr.FRAME_HEADER_BYTES


# ──────────────────────────────────────────────────────────────────────────────
# Main encoder
# ──────────────────────────────────────────────────────────────────────────────

def encode(
    message: bytes,
    template_vsav: str,
    output_vsav: str,
    nonce: Optional[int] = None,
) -> int:
    """Encode a message into a .vsav file.

    Parameters
    ----------
    message : bytes
        The secret message to hide.
    template_vsav : str
        Path to a .vsav file to use as the structural template.
        The set of pieces in this file defines the bijection key.
    output_vsav : str
        Path to write the steganographic .vsav.
    nonce : int, optional
        High-order randomisation factor.  If None, a cryptographically
        random nonce is chosen.

    Returns
    -------
    int : The nonce used.
    """
    # Step 1: determine active (relocatable) pieces from template
    active_names = active_piece_names(template_vsav)
    save = parse_vsav(template_vsav)

    # Step 2: compute restricted radices and capacity
    radices = bijection.restricted_radices(active_names)
    capacity = mr.product(radices)

    # Step 3: frame message
    framed = mr.frame_message(message)
    framed_int = mr.bytes_to_int(framed)
    msg_modulus = 1 << (8 * len(framed))

    if framed_int >= capacity:
        max_bytes = int(mr.bits(radices) // 8) - mr.FRAME_HEADER_BYTES
        raise ValueError(
            f"Message too large: {len(message)} bytes payload. "
            f"Maximum for this template: {max_bytes} bytes."
        )

    # Step 4: choose nonce
    high_modulus = capacity // msg_modulus
    if nonce is None:
        nonce = secrets.randbelow(max(1, high_modulus))
    elif nonce >= high_modulus:
        raise ValueError(f"Nonce {nonce} >= high_modulus {high_modulus}")

    # Step 5: embed into state integer
    N = mr.embed_message(framed, radices, nonce=nonce)

    # Step 6: decode to game state (restricted to active pieces)
    game_state = bijection.decode_restricted(N, active_names)

    # Step 7: write .vsav
    write_vsav(save, output_vsav, location_overrides=game_state)

    return nonce


def encode_file(
    message_path: str,
    template_vsav: str,
    output_vsav: str,
    nonce: Optional[int] = None,
) -> int:
    """Encode the contents of a file into a .vsav."""
    with open(message_path, "rb") as f:
        message = f.read()
    return encode(message, template_vsav, output_vsav, nonce=nonce)


def encode_text(
    text: str,
    template_vsav: str,
    output_vsav: str,
    nonce: Optional[int] = None,
    encoding: str = "utf-8",
) -> int:
    """Encode a text string into a .vsav."""
    return encode(text.encode(encoding), template_vsav, output_vsav, nonce=nonce)


# ──────────────────────────────────────────────────────────────────────────────
# Diagnostic
# ──────────────────────────────────────────────────────────────────────────────

def print_capacity(template_vsav: Optional[str] = None) -> None:
    import math
    if template_vsav:
        bits = capacity_bits(template_vsav)
        names = active_piece_names(template_vsav)
        max_bytes = max_message_bytes(template_vsav)
        print(f"Template:              {os.path.basename(template_vsav)}")
        print(f"Active pieces:         {len(names)}")
    else:
        from cna_ssc.engine.piece_registry import radix_list
        radices = radix_list()
        bits = mr.bits(radices)
        max_bytes = int(bits // 8) - mr.FRAME_HEADER_BYTES
        print(f"Full registry (no template filter):")

    print(f"State space bits:      {bits:.0f}")
    print(f"State space size:      ~10^{bits * math.log10(2):.0f}")
    print(f"Maximum message:       {max_bytes:,} bytes ({max_bytes/1024:.1f} KB)")
    print(f"AES-256 key space:     256 bits")
    print(f"RSA-4096 key space:    4,096 bits")
    print(f"Grover-reduced:        {bits/2:.0f} bits remain after quantum search")


if __name__ == "__main__":
    print_capacity()

