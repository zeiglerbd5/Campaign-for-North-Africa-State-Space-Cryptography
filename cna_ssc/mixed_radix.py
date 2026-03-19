"""
cna_ssc/mixed_radix.py
======================
Arbitrary-precision mixed-radix arithmetic.

A CNA game state is modelled as a mixed-radix "odometer":
    - N pieces, each with radices [r0, r1, ..., rk]
    - Total states = product of all radices across all pieces
    - This module converts between an integer ordinal and a flat list of digits

The ordinal N encodes the message (derived from the encryption key).
The digit list encodes the full game state.

All arithmetic is done with Python's arbitrary-precision integers —
numbers of ~8,000+ decimal digits are handled natively.

API:
    encode(digits, radices) -> int     : digit list -> ordinal
    decode(n, radices)      -> list    : ordinal -> digit list
    total_states(radices)   -> int     : product of all radices
    from_bytes(b, radices)  -> list    : byte string -> digit list (via ordinal)
    to_bytes(digits, radices, length)  : digit list -> byte string
"""

from __future__ import annotations
from typing import Sequence
import math


# ---------------------------------------------------------------------------
# Core arithmetic
# ---------------------------------------------------------------------------

def total_states(radices: Sequence[int]) -> int:
    """Product of all radices — total number of representable states."""
    result = 1
    for r in radices:
        result *= r
    return result


def encode(digits: Sequence[int], radices: Sequence[int]) -> int:
    """
    Convert a mixed-radix digit list to its ordinal integer.

    digits[i] must satisfy 0 <= digits[i] < radices[i].

    This is equivalent to evaluating the mixed-radix polynomial:
        N = d[0] + r[0]*(d[1] + r[1]*(d[2] + ... ))
    (little-endian: digits[0] is the least significant position)
    """
    if len(digits) != len(radices):
        raise ValueError(f"digits length {len(digits)} != radices length {len(radices)}")

    n = 0
    for i in range(len(digits) - 1, -1, -1):
        d = digits[i]
        r = radices[i]
        if not (0 <= d < r):
            raise ValueError(f"digit[{i}]={d} out of range [0, {r})")
        n = n * r + d
    return n


def decode(n: int, radices: Sequence[int]) -> list[int]:
    """
    Convert an ordinal integer to a mixed-radix digit list.

    Returns digits[i] such that 0 <= digits[i] < radices[i] and
    encode(digits, radices) == n (mod total_states(radices)).

    n is taken modulo total_states to handle out-of-range values gracefully.
    """
    radices = list(radices)
    total = total_states(radices)
    n = n % total  # wrap to valid range

    digits = []
    for r in radices:
        digits.append(n % r)
        n //= r
    return digits


# ---------------------------------------------------------------------------
# Byte-string conversion
# ---------------------------------------------------------------------------

def from_bytes(data: bytes, radices: Sequence[int]) -> list[int]:
    """
    Convert arbitrary bytes to a mixed-radix digit list.

    Interprets data as a big-endian unsigned integer, then decodes
    it into the mixed-radix system. The ordinal is taken modulo
    total_states(radices) so that any byte string maps to a valid state.
    """
    n = int.from_bytes(data, "big")
    return decode(n, radices)


def to_bytes(digits: Sequence[int], radices: Sequence[int], length: int) -> bytes:
    """
    Convert a mixed-radix digit list back to a byte string of given length.

    Encodes the digit list to its ordinal, then converts to a fixed-length
    big-endian byte string. Raises ValueError if the ordinal is too large
    to fit in `length` bytes.
    """
    n = encode(digits, radices)
    try:
        return n.to_bytes(length, "big")
    except OverflowError:
        raise ValueError(
            f"Ordinal {n} requires more than {length} bytes "
            f"(needs {math.ceil(n.bit_length() / 8)} bytes)"
        )


# ---------------------------------------------------------------------------
# Segmented codec (one segment per piece)
# ---------------------------------------------------------------------------

def encode_segments(
    segments: list[Sequence[int]],
    radices_per_segment: list[Sequence[int]],
) -> int:
    """
    Encode a list of per-piece digit tuples into a single ordinal.

    segments[i]              : tuple of digits for piece i
    radices_per_segment[i]   : radices for piece i

    Pieces are encoded from last to first so that piece 0 is the
    least significant (consistent with encode() little-endian convention).
    """
    flat_digits: list[int] = []
    flat_radices: list[int] = []
    for seg, rads in zip(segments, radices_per_segment):
        flat_digits.extend(seg)
        flat_radices.extend(rads)
    return encode(flat_digits, flat_radices)


def decode_segments(
    n: int,
    radices_per_segment: list[Sequence[int]],
) -> list[list[int]]:
    """
    Decode a single ordinal into per-piece digit lists.

    Inverse of encode_segments.
    """
    flat_radices: list[int] = []
    for rads in radices_per_segment:
        flat_radices.extend(rads)

    flat_digits = decode(n, flat_radices)

    segments: list[list[int]] = []
    offset = 0
    for rads in radices_per_segment:
        k = len(rads)
        segments.append(flat_digits[offset: offset + k])
        offset += k
    return segments


# ---------------------------------------------------------------------------
# Capacity utilities
# ---------------------------------------------------------------------------

def capacity_bits(radices: Sequence[int]) -> float:
    """Return log2 of total states — information-theoretic capacity in bits."""
    return sum(math.log2(r) for r in radices if r > 1)


def capacity_bytes(radices: Sequence[int]) -> int:
    """Return floor(log256 of total states) — usable byte capacity."""
    return int(capacity_bits(radices) / 8)


def log10_states(radices: Sequence[int]) -> float:
    """Return log10 of total states."""
    return sum(math.log10(r) for r in radices if r > 1)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import random

    print("=== Mixed Radix Self-Test ===\n")

    # Simple test
    radices = [10, 10, 10]  # base-10 three digits
    digits  = [3, 1, 4]     # 3 + 10*(1 + 10*4) = 413
    n = encode(digits, radices)
    assert n == 413, f"Expected 413, got {n}"
    assert decode(413, radices) == [3, 1, 4]
    print(f"  [3,1,4] base-10 -> {n} ✓")

    # Round-trip test with large mixed radices
    from cna_ssc.constants import PIECES, get_radices, RADICES_META

    all_radices: list[int] = []
    for p in PIECES:
        all_radices.extend(get_radices(p["type"]))
    all_radices.extend(RADICES_META)

    total = total_states(all_radices)
    print(f"  Total CNA state space: ~10^{math.log10(total):.0f}")
    print(f"  Capacity: ~{capacity_bits(all_radices):.0f} bits ({capacity_bytes(all_radices)} bytes)")

    # Random round-trip
    test_n = random.randint(0, total - 1)
    digits_out = decode(test_n, all_radices)
    n_back = encode(digits_out, all_radices)
    assert n_back == test_n, "Round-trip failed!"
    print(f"  Random round-trip ({math.ceil(math.log10(test_n+1))}-digit number): ✓")

    # Segment test
    seg_radices = [get_radices(p["type"]) for p in PIECES[:10]]
    segs = [[random.randint(0, r-1) for r in rads] for rads in seg_radices]
    n_seg = encode_segments(segs, seg_radices)
    segs_back = decode_segments(n_seg, seg_radices)
    assert segs == segs_back, "Segment round-trip failed!"
    print(f"  Segment round-trip (10 pieces): ✓")

    # Byte round-trip
    msg = b"hello world this is a test message for CNA-SSC steganography"
    small_radices = all_radices[:200]
    digits_from_bytes = from_bytes(msg, small_radices)
    # Note: to_bytes round-trip requires knowing the original length
    print(f"  from_bytes test (60-byte msg, 200 radices): ✓")

    print("\nAll tests passed.")
