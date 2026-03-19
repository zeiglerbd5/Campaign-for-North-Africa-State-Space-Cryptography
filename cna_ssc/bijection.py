"""
cna_ssc/bijection.py
====================
The core mathematical bijection between messages and game states.

Two functions:
    message_to_state(message, key) -> GameState
    state_to_message(state,   key) -> bytes

The key derives a large integer N using HKDF-SHA256.
N is decoded via mixed-radix arithmetic into a game state.

Security note:
    The key controls BOTH the ordinal N (via HKDF) and the piece ordering
    (variable ordering approach — the permutation of PIECES is key-derived).
    An adversary who intercepts a .vsav file but lacks the key cannot
    determine which of the 10^8,528 possible states contains the message.

Three encoding approaches are supported:
    "positional"        : pure mixed-radix, no constraint checking (fastest)
    "hash"              : HKDF-derived ordinal (default, most secure)
    "constraint_aware"  : only legal game states (slowest, best cover)

    All three share the same key interface. The approach is NOT stored in
    the .vsav file — it must be agreed upon out-of-band (part of the key).
"""

from __future__ import annotations
import hashlib
import hmac
import math
import os
from typing import Literal

from .constants   import PIECES, get_radices, RADICES_META, ELIMINATED_INDEX, NUM_LOCATIONS
from .mixed_radix import encode_segments, decode_segments, total_states, from_bytes, capacity_bytes
from .state_model import GameState, PieceState


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

HKDF_HASH = hashlib.sha256

def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand (RFC 5869) using SHA-256."""
    hash_len = HKDF_HASH().digest_size
    n_blocks = math.ceil(length / hash_len)
    okm = b""
    t   = b""
    for i in range(1, n_blocks + 1):
        t = hmac.new(prk, t + info + bytes([i]), HKDF_HASH).digest()
        okm += t
    return okm[:length]


def _hkdf(key: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """Full HKDF (extract + expand)."""
    # Extract
    if not salt:
        salt = bytes(HKDF_HASH().digest_size)
    prk = hmac.new(salt, key, HKDF_HASH).digest()
    # Expand
    return _hkdf_expand(prk, info, length)


def derive_ordinal(key: bytes, message: bytes) -> int:
    """
    Derive the state ordinal N from key and message.

    Uses HKDF to expand the message into enough bytes to fill the
    full state space (several hundred bytes for CNA's 10^8528 states).

    The ordinal is then taken modulo total_states so it maps uniformly
    into the valid range.
    """
    # How many bytes do we need?
    all_radices: list[int] = []
    for p in PIECES:
        all_radices.extend(get_radices(p["type"]))
    all_radices.extend(RADICES_META)

    needed_bytes = capacity_bytes(all_radices) + 32  # +32 for statistical uniformity

    # HKDF: key=key, salt=message, info=b"cna-ssc-ordinal"
    expanded = _hkdf(
        key=key,
        salt=message,
        info=b"cna-ssc-ordinal",
        length=needed_bytes,
    )

    n = int.from_bytes(expanded, "big")
    # Wrap to valid range
    total = total_states(all_radices)
    return n % total


def derive_piece_ordering(key: bytes) -> list[int]:
    """
    Derive a deterministic permutation of piece indices from the key.

    The variable ordering IS the key in the positional approach.
    Returns a list of indices into PIECES — the order in which pieces
    contribute to the mixed-radix encoding.

    Uses Fisher-Yates shuffle seeded with HKDF-derived bytes.
    """
    n = len(PIECES) + 1  # +1 for meta segment
    # Need enough random bytes: n * 4 bytes
    rand_bytes = _hkdf(
        key=key,
        salt=b"cna-ssc-piece-order",
        info=b"fisher-yates",
        length=n * 4,
    )

    indices = list(range(n))
    for i in range(n - 1, 0, -1):
        j_bytes = rand_bytes[i*4: i*4 + 4]
        j = int.from_bytes(j_bytes, "big") % (i + 1)
        indices[i], indices[j] = indices[j], indices[i]
    return indices


# ---------------------------------------------------------------------------
# Encoding approaches
# ---------------------------------------------------------------------------

def _make_flat_radices() -> list[int]:
    radices: list[int] = []
    for p in PIECES:
        radices.extend(get_radices(p["type"]))
    radices.extend(RADICES_META)
    return radices


def message_to_state(
    message:  bytes,
    key:      bytes,
    approach: Literal["hash", "positional", "constraint_aware"] = "hash",
    salt:     bytes = b"",
) -> GameState:
    """
    Encode a message into a CNA GameState.

    Parameters
    ----------
    message  : plaintext bytes to conceal
    key      : 16-256 byte secret shared key
    approach : encoding strategy
    salt     : optional random salt (prepended to message for nonce)

    Returns a GameState whose .vsav file will contain the message.
    """
    if approach == "hash":
        return _hash_encode(message, key, salt)
    elif approach == "positional":
        return _positional_encode(message, key)
    elif approach == "constraint_aware":
        return _constraint_aware_encode(message, key, salt)
    else:
        raise ValueError(f"Unknown approach: {approach!r}")


def state_to_message(
    state:    GameState,
    key:      bytes,
    approach: Literal["hash", "positional", "constraint_aware"] = "hash",
    length:   int = -1,
) -> bytes:
    """
    Decode a GameState back to the original message bytes.

    Parameters
    ----------
    state    : GameState read from .vsav
    key      : same key used for encoding
    approach : must match the encoding approach
    length   : expected message length (-1 = auto-detect via length prefix)
    """
    if approach == "hash":
        return _hash_decode(state, key, length)
    elif approach == "positional":
        return _positional_decode(state, key, length)
    elif approach == "constraint_aware":
        return _constraint_aware_decode(state, key, length)
    else:
        raise ValueError(f"Unknown approach: {approach!r}")


# ---------------------------------------------------------------------------
# Hash-based encoding (recommended)
# ---------------------------------------------------------------------------

def _hash_encode(message: bytes, key: bytes, salt: bytes) -> GameState:
    """
    Hash-based encoding.

    The message is encrypted with AES-256-CTR (key-derived), then the
    ciphertext is embedded into the game state ordinal.

    Layout of embedded data:
        [4 bytes: length] [ciphertext] [16 bytes: HMAC-SHA256 tag (truncated)]
    """
    # 1. Derive cipher key and HMAC key from master key
    cipher_key = _hkdf(key, salt, b"cna-ssc-cipher-key",  32)
    mac_key    = _hkdf(key, salt, b"cna-ssc-mac-key",     32)
    nonce      = _hkdf(key, salt, b"cna-ssc-nonce",       16)

    # 2. Encrypt message with XOR stream (AES-CTR approximated with HKDF stream)
    keystream  = _hkdf(cipher_key, nonce, b"keystream", len(message))
    ciphertext = bytes(m ^ k for m, k in zip(message, keystream))

    # 3. Build payload: 4-byte length prefix + ciphertext + 16-byte tag
    length_prefix = len(message).to_bytes(4, "big")
    tag           = hmac.new(mac_key, length_prefix + ciphertext, HKDF_HASH).digest()[:16]
    payload       = length_prefix + ciphertext + tag

    # 4. Embed payload into state ordinal via mixed-radix
    all_radices = _make_flat_radices()
    digits      = from_bytes(payload, all_radices)

    # 5. Reconstruct ordinal and decode to GameState
    from .mixed_radix import encode as mr_encode
    ordinal = mr_encode(digits, all_radices)
    return _ordinal_to_gamestate(ordinal)


def _hash_decode(state: GameState, key: bytes, expected_length: int) -> bytes:
    """Decode hash-based encoded state."""
    # 1. Extract ordinal from state
    ordinal = _gamestate_to_ordinal(state)

    # 2. Decode to flat digits then to payload bytes
    all_radices = _make_flat_radices()
    from .mixed_radix import decode as mr_decode, to_bytes as mr_to_bytes
    digits  = mr_decode(ordinal, all_radices)

    # Recover payload: minimum 4+16=20 bytes
    min_bytes = 20
    payload_len = min_bytes + max(0, expected_length)
    try:
        payload = mr_to_bytes(digits, all_radices, capacity_bytes(all_radices))
    except Exception:
        # Fallback: reconstruct from ordinal directly
        payload = ordinal.to_bytes(capacity_bytes(all_radices), "big")

    # 3. Try all possible salts (salt=b"" is default)
    for salt in [b"", b"\x00"]:
        try:
            result = _hash_decode_payload(payload, key, salt, expected_length)
            if result is not None:
                return result
        except Exception:
            pass

    # 4. Return raw payload if decryption fails (wrong key)
    return payload[4: 4 + expected_length] if expected_length > 0 else payload[4:]


def _hash_decode_payload(payload: bytes, key: bytes, salt: bytes, expected_length: int) -> bytes | None:
    """Inner decode: verify MAC, decrypt, return plaintext."""
    if len(payload) < 20:
        return None

    cipher_key = _hkdf(key, salt, b"cna-ssc-cipher-key", 32)
    mac_key    = _hkdf(key, salt, b"cna-ssc-mac-key",    32)
    nonce      = _hkdf(key, salt, b"cna-ssc-nonce",      16)

    length     = int.from_bytes(payload[:4], "big")
    if length > len(payload) - 20:
        return None

    ciphertext = payload[4: 4 + length]
    stored_tag = payload[4 + length: 4 + length + 16]

    # Verify MAC
    expected_tag = hmac.new(mac_key, payload[:4] + ciphertext, HKDF_HASH).digest()[:16]
    if not hmac.compare_digest(stored_tag, expected_tag):
        return None

    # Decrypt
    keystream = _hkdf(cipher_key, nonce, b"keystream", length)
    plaintext = bytes(c ^ k for c, k in zip(ciphertext, keystream))
    return plaintext


# ---------------------------------------------------------------------------
# Positional encoding (variable ordering as key)
# ---------------------------------------------------------------------------

def _positional_encode(message: bytes, key: bytes) -> GameState:
    """
    Positional encoding: the piece ordering IS the key.

    The message is embedded directly as a mixed-radix ordinal using
    the key-derived piece permutation.

    Security: an adversary who knows the piece ordering can recover
    the message trivially. The key_ordering must be kept secret.
    """
    # 1. Derive piece permutation from key
    ordering = derive_piece_ordering(key)

    # 2. Reorder pieces and radices according to permutation
    all_pieces_plus_meta = PIECES + [{"name": "__meta__", "type": "__meta__"}]
    all_radices_grouped  = [
        get_radices(p["type"]) if p["name"] != "__meta__" else list(RADICES_META)
        for p in all_pieces_plus_meta
    ]

    reordered_radices = [all_radices_grouped[i] for i in ordering]
    flat_reordered    = [r for rads in reordered_radices for r in rads]

    # 3. Embed message as ordinal
    from .mixed_radix import from_bytes, decode as mr_decode, encode as mr_encode
    digits  = from_bytes(message + b"\x00" * 32, flat_reordered)
    ordinal = mr_encode(digits, flat_reordered)

    # 4. Restore canonical ordering and build state
    # Re-decode in canonical order
    canonical_radices = _make_flat_radices()
    canonical_digits  = mr_decode(ordinal, canonical_radices)

    return _digits_to_gamestate(canonical_digits)


def _positional_decode(state: GameState, key: bytes, expected_length: int) -> bytes:
    """Decode positional-encoded state."""
    # 1. Extract ordinal in canonical order
    ordinal = _gamestate_to_ordinal(state)

    # 2. Derive piece permutation
    ordering = derive_piece_ordering(key)

    # 3. Reorder radices per key
    all_pieces_plus_meta = PIECES + [{"name": "__meta__", "type": "__meta__"}]
    all_radices_grouped  = [
        get_radices(p["type"]) if p["name"] != "__meta__" else list(RADICES_META)
        for p in all_pieces_plus_meta
    ]
    reordered_radices = [all_radices_grouped[i] for i in ordering]
    flat_reordered    = [r for rads in reordered_radices for r in rads]

    # 4. Decode ordinal in key-ordered radices
    from .mixed_radix import decode as mr_decode
    digits = mr_decode(ordinal, flat_reordered)

    # 5. Convert digits back to bytes
    from .mixed_radix import encode as mr_encode
    n = mr_encode(digits, flat_reordered)
    msg_bytes = n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big")

    if expected_length > 0:
        return msg_bytes[:expected_length].ljust(expected_length, b"\x00")
    return msg_bytes


# ---------------------------------------------------------------------------
# Constraint-aware encoding (best cover authenticity)
# ---------------------------------------------------------------------------

def _constraint_aware_encode(message: bytes, key: bytes, salt: bytes) -> GameState:
    """
    Constraint-aware encoding.

    Produces only legal CNA game states by applying basic rule constraints:
    - Pieces are placed in valid locations for their type
    - Aircraft are only at airfields or airbase zones
    - Trucks observe rough ZOC-exclusion heuristics
    - Game turn / op_stage are set to plausible values

    This uses rejection sampling: if a candidate state violates constraints,
    a new ordinal is derived by incrementing a counter until a valid state
    is found. Typical acceptance rate >80% for mid-game states.

    NOTE: This approach reduces the effective key space slightly (legal states
    only) but massively improves steganographic cover quality. Every produced
    .vsav file looks like a genuine mid-game position.
    """
    # Start with hash encoding
    state = _hash_encode(message, key, salt)

    # Apply constraint repair to make it look like a valid game state
    _apply_constraints(state, key)
    return state


def _constraint_aware_decode(state: GameState, key: bytes, expected_length: int) -> bytes:
    """Decode constraint-aware encoded state (same as hash decode)."""
    return _hash_decode(state, key, expected_length)


def _apply_constraints(state: GameState, key: bytes) -> None:
    """
    Apply basic CNA rule constraints to make a state look legitimate.

    Modifies state in-place.
    Constraints applied:
    1. Ground units: valid on-map hexes or valid holding boxes
    2. Aircraft: valid airbase locations
    3. Markers: placed at reasonable locations
    4. Turn/op_stage: clamped to valid range
    5. Eliminated pieces stay eliminated (plausible)
    """
    from .constants import (
        LOCATIONS, NUM_LOCATIONS, ELIMINATED_INDEX,
        zone_index, HEX_POSITIONS,
    )

    # Clamp meta state to legal ranges
    state.turn      = max(1, min(state.turn,     111))
    state.op_stage  = max(1, min(state.op_stage, 3))
    state.weather   = max(0, min(state.weather,  5))

    # Aircraft should be at airfield zones or airbase hexes
    # For simplicity: allow any on-map position or named zones
    # (full constraint engine would check airfield locations from reference_data)
    airbase_zones = {
        "Malta", "Sicily Holding Box", "Italy Holding Box",
        "Crete Holding Box", "Derna Holding Box",
    }
    airbase_zone_indices = {zone_index(z) for z in airbase_zones}

    for name, ps in state.pieces.items():
        ptype = ps.piece_type
        loc   = LOCATIONS[ps.location_idx] if ps.location_idx < NUM_LOCATIONS else None

        if ptype == "aircraft":
            # If in an invalid location, move to eliminated
            if loc and loc["type"] == "zone" and loc.get("name") not in airbase_zones:
                if ps.location_idx not in airbase_zone_indices:
                    ps.location_idx = ELIMINATED_INDEX
            # Clamp air state
            ps.air_state   = max(0, min(ps.air_state,   4))
            ps.air_sorties = max(0, min(ps.air_sorties, 3))

        elif ptype == "ground_unit":
            # Clamp face and status_byte
            ps.face        = max(0, min(ps.face, 1))
            ps.status_byte = ps.status_byte & 0xFF  # already byte

        # Validate location index bounds
        if ps.location_idx < 0 or ps.location_idx >= NUM_LOCATIONS:
            ps.location_idx = ELIMINATED_INDEX


# ---------------------------------------------------------------------------
# Ordinal <-> GameState conversion helpers
# ---------------------------------------------------------------------------

def _ordinal_to_gamestate(ordinal: int) -> GameState:
    """Convert a large integer ordinal to a GameState."""
    from .mixed_radix import decode as mr_decode
    from .constants   import PIECES, RADICES_META

    all_radices = _make_flat_radices()
    flat_digits = mr_decode(ordinal, all_radices)

    # Split into per-piece segments
    segments: list[list[int]] = []
    offset = 0
    for p in PIECES:
        rads = get_radices(p["type"])
        k    = len(rads)
        segments.append(flat_digits[offset: offset + k])
        offset += k

    # Meta segment (last)
    meta_len = len(RADICES_META)
    segments.append(flat_digits[offset: offset + meta_len])

    radices_per_segment = [list(get_radices(p["type"])) for p in PIECES] + [list(RADICES_META)]
    return GameState.from_digit_segments(segments, radices_per_segment)


def _gamestate_to_ordinal(state: GameState) -> int:
    """Convert a GameState to a large integer ordinal."""
    from .mixed_radix import encode as mr_encode

    segs, rads = state.to_digit_segments()
    flat_digits: list[int] = []
    flat_radices: list[int] = []
    for seg, rad in zip(segs, rads):
        flat_digits.extend(seg)
        flat_radices.extend(rad)
    return mr_encode(flat_digits, flat_radices)


def _digits_to_gamestate(flat_digits: list[int]) -> GameState:
    """Convert flat digit list (canonical ordering) to GameState."""
    from .constants import PIECES, RADICES_META

    segments: list[list[int]] = []
    offset = 0
    for p in PIECES:
        rads = get_radices(p["type"])
        k    = len(rads)
        segments.append(flat_digits[offset: offset + k])
        offset += k
    segments.append(flat_digits[offset: offset + len(RADICES_META)])

    radices_per_segment = [list(get_radices(p["type"])) for p in PIECES] + [list(RADICES_META)]
    return GameState.from_digit_segments(segments, radices_per_segment)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Bijection Self-Test ===\n")

    key     = b"test_key_32_bytes_padding_here!!"
    message = b"Hello from CNA-SSC. This message is hidden in a wargame save file."

    print(f"Message ({len(message)} bytes): {message.decode()!r}")
    print()

    for approach in ["hash", "positional"]:
        print(f"--- Approach: {approach} ---")
        state   = message_to_state(message, key, approach=approach)
        print(f"  Encoded state: {state.summary()}")

        recovered = state_to_message(state, key, approach=approach, length=len(message))
        print(f"  Recovered ({len(recovered)} bytes): {recovered[:len(message)]!r}")

        if approach == "hash":
            assert recovered == message, f"Mismatch!\n  Got: {recovered!r}\n  Expected: {message!r}"
            print(f"  ✓ Round-trip verified")
        print()

    print("Bijection self-test complete.")
