"""
cna_ssc/decoder.py
==================
High-level decode API.

    message = decode(vsav_bytes, key)

Combines vsav_reader + bijection into one call.
Also provides decode_from_file() for convenience.
"""

from __future__ import annotations
from typing import Literal

from .vsav_reader import read_vsav
from .bijection   import state_to_message


def decode(
    vsav_bytes: bytes,
    key:        bytes,
    approach:   Literal["hash", "positional", "constraint_aware"] = "hash",
    length:     int = -1,
) -> bytes:
    """
    Decode a hidden message from a Vassal .vsav file.

    Parameters
    ----------
    vsav_bytes : raw bytes of the .vsav file
    key        : same 16-256 byte key used for encoding
    approach   : same encoding approach used for encoding
    length     : expected plaintext length in bytes (-1 = auto from length prefix)

    Returns
    -------
    bytes : the recovered plaintext message

    Notes
    -----
    If the key is wrong, returns garbled bytes (not an error).
    Use the 16-byte HMAC tag (embedded in hash approach) to detect wrong keys.
    """
    state = read_vsav(vsav_bytes)
    payload = state_to_message(state, key, approach=approach, length=length + 16 if length > 0 else -1)

    # Strip the 16-byte salt prepended by encoder.py
    if len(payload) >= 16:
        message = payload[16:]
        if length > 0:
            return message[:length]
        return message
    return payload


def decode_from_file(
    path:     str,
    key:      bytes,
    approach: Literal["hash", "positional", "constraint_aware"] = "hash",
    length:   int = -1,
) -> bytes:
    """
    Read a .vsav file from disk and decode the hidden message.

    Parameters
    ----------
    path     : path to the .vsav file
    key      : shared secret key
    approach : encoding approach
    length   : expected plaintext length (-1 = auto)

    Returns the recovered plaintext message.
    """
    with open(path, "rb") as f:
        vsav_bytes = f.read()
    return decode(vsav_bytes, key, approach=approach, length=length)
