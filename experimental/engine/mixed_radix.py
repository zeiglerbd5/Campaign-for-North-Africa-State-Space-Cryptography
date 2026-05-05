"""
cna_ssc/engine/mixed_radix.py

Arbitrary-precision mixed-radix encoder / decoder.

The CNA game state is a tuple of position indices (i₁, i₂, …, iₙ) where
each iₖ ∈ [0, rₖ-1] and rₖ is the number of valid positions for piece k.

The bijection maps this tuple to a single non-negative integer:

    N = i₁ + r₁*(i₂ + r₂*(i₃ + r₃*(…)))        (little-endian convention)

and back:

    iₖ = (N // ∏rⱼ for j<k) mod rₖ

This is analogous to a mixed-radix odometer where digit k has base rₖ.

Python's arbitrary-precision integers handle the ~8,528-decimal-digit numbers
natively, so no special library is required.

All functions operate on plain Python ints and lists.
"""

from __future__ import annotations
from typing import List, Tuple


def encode(indices: List[int], radices: List[int]) -> int:
    """Convert position-index tuple → big integer (little-endian mixed radix).

    Parameters
    ----------
    indices : list of int
        Position index for each piece, len == len(radices).
        Each indices[k] must satisfy 0 <= indices[k] < radices[k].
    radices : list of int
        Number of valid positions for each piece, in canonical order.

    Returns
    -------
    int : non-negative integer in range [0, product(radices))
    """
    if len(indices) != len(radices):
        raise ValueError(
            f"len(indices)={len(indices)} != len(radices)={len(radices)}"
        )
    N = 0
    base = 1
    for idx, r in zip(indices, radices):
        if not (0 <= idx < r):
            raise ValueError(f"Index {idx} out of range [0, {r})")
        N += idx * base
        base *= r
    return N


def decode(N: int, radices: List[int]) -> List[int]:
    """Convert big integer → position-index tuple (little-endian mixed radix).

    Parameters
    ----------
    N : int
        Non-negative integer, must be < product(radices).
    radices : list of int
        Number of valid positions for each piece, in canonical order.

    Returns
    -------
    list of int : position indices, same length as radices.
    """
    if N < 0:
        raise ValueError(f"N must be non-negative, got {N}")
    indices = []
    remaining = N
    for r in radices:
        indices.append(remaining % r)
        remaining //= r
    if remaining != 0:
        raise ValueError(
            f"N={N} is too large for the given radices "
            f"(overflowed by factor {remaining})"
        )
    return indices


def product(radices: List[int]) -> int:
    """Return ∏ radices — the total state space size."""
    result = 1
    for r in radices:
        result *= r
    return result


def bits(radices: List[int]) -> float:
    """Return log₂(product(radices)) — capacity in bits."""
    import math
    return math.log2(product(radices)) if radices else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Message ↔ state-index embedding
# ──────────────────────────────────────────────────────────────────────────────

def bytes_to_int(data: bytes) -> int:
    """Convert message bytes to a big integer (big-endian)."""
    return int.from_bytes(data, byteorder="big")


def int_to_bytes(n: int, length: int) -> bytes:
    """Convert a big integer to exactly `length` bytes (big-endian)."""
    return n.to_bytes(length, byteorder="big")


def embed_message(message: bytes, radices: List[int], nonce: int = 0) -> int:
    """Embed message bytes into a state-space integer.

    The state integer N satisfies:
        N mod 2^(8*len(message)) == message_int
        N is derived from nonce for the remaining high-order bits

    This ensures the message is recoverable from N without knowing the
    nonce, while the nonce randomises the high-order portion of the state
    (selecting which specific game state carries the message).

    Parameters
    ----------
    message : bytes
        Plaintext to embed.
    radices : list of int
        State space radices.
    nonce : int
        Non-negative integer selecting the high-order state bits.
        Must be < product(radices) // 2^(8*len(message)).

    Returns
    -------
    int : State-space integer carrying the message.
    """
    capacity = product(radices)
    msg_int = bytes_to_int(message)
    msg_modulus = 1 << (8 * len(message))

    if msg_int >= capacity:
        raise ValueError(
            f"Message ({len(message)} bytes = {msg_int.bit_length()} bits) "
            f"exceeds state space capacity ({capacity.bit_length()} bits)"
        )

    # High-order bits come from nonce; low-order bits carry the message
    high_modulus = capacity // msg_modulus
    if nonce >= high_modulus:
        raise ValueError(
            f"Nonce {nonce} >= high_modulus {high_modulus}"
        )

    N = nonce * msg_modulus + (msg_int % msg_modulus)
    assert N < capacity, "Embedding arithmetic error"
    return N


def extract_message(N: int, message_length: int) -> bytes:
    """Recover message bytes from a state-space integer.

    Parameters
    ----------
    N : int
        State-space integer (from decode/bijection).
    message_length : int
        Length of the original message in bytes.

    Returns
    -------
    bytes : Recovered message.
    """
    msg_modulus = 1 << (8 * message_length)
    msg_int = N % msg_modulus
    return int_to_bytes(msg_int, message_length)


# ──────────────────────────────────────────────────────────────────────────────
# Framing: prepend length so receiver knows how many bytes to extract
# ──────────────────────────────────────────────────────────────────────────────

FRAME_HEADER_BYTES = 4  # 4-byte big-endian length prefix


def frame_message(message: bytes) -> bytes:
    """Append a 4-byte length trailer to the message.

    Length is placed at the END so it occupies the low-order bits of the
    embedded integer — exactly where extract_message reads from.
    Layout: [message bytes ...] [4-byte big-endian length]
    """
    length_trailer = len(message).to_bytes(FRAME_HEADER_BYTES, byteorder="big")
    return message + length_trailer


def unframe_message(framed: bytes) -> bytes:
    """Strip the 4-byte length trailer and return the original message."""
    if len(framed) < FRAME_HEADER_BYTES:
        raise ValueError("Framed message too short to contain trailer")
    length = int.from_bytes(framed[-FRAME_HEADER_BYTES:], byteorder="big")
    payload = framed[:-FRAME_HEADER_BYTES]
    if len(payload) < length:
        raise ValueError(
            f"Declared length {length} but only {len(payload)} bytes available"
        )
    return payload[:length]


if __name__ == "__main__":
    # Quick self-test
    radices = [5, 3, 7, 4, 10]
    indices = [2, 1, 5, 3, 7]
    N = encode(indices, radices)
    recovered = decode(N, radices)
    assert recovered == indices, f"{recovered} != {indices}"
    print(f"encode({indices}, {radices}) = {N}")
    print(f"decode({N}, {radices})       = {recovered}")
    print(f"State space bits: {bits(radices):.2f}")

    # Message embedding
    msg = b"Hello, CNA-SSC!"
    framed = frame_message(msg)
    large_radices = [1000] * 100
    N2 = embed_message(framed, large_radices, nonce=42)
    recovered2 = extract_message(N2, len(framed))
    assert unframe_message(recovered2) == msg
    print(f"Message round-trip OK: {msg}")
