"""
cna_ssc/encoder.py
==================
High-level encode API.

    vsav_bytes = encode(message, key)

Combines bijection + vsav_writer into one call.
Also provides encode_to_file() for convenience.
"""

from __future__ import annotations
import os
from typing import Literal, Optional

from .bijection    import message_to_state
from .vsav_writer  import write_vsav


def encode(
    message:  bytes,
    key:      bytes,
    approach: Literal["hash", "positional", "constraint_aware"] = "hash",
    salt:     Optional[bytes] = None,
    timestamp: Optional[int] = None,
) -> bytes:
    """
    Encode a secret message into a Vassal .vsav file.

    Parameters
    ----------
    message   : plaintext bytes to conceal (any length up to ~3,000 bytes)
    key       : 16-256 byte shared secret key
    approach  : "hash" (default), "positional", or "constraint_aware"
    salt      : optional random nonce (16 bytes recommended; generated if None)
    timestamp : Unix ms timestamp for .vsav metadata (default: April 6, 2020)

    Returns
    -------
    bytes : a valid .vsav file containing the hidden message

    Example
    -------
        key     = os.urandom(32)
        vsav    = encode(b"Meet at dawn.", key)
        # Transmit vsav as a "wargame save file"
        message = decode(vsav, key)
        assert message == b"Meet at dawn."
    """
    if salt is None:
        salt = os.urandom(16)

    # Prepend salt to message for nonce-based encryption
    payload = salt + message

    state = message_to_state(payload, key, approach=approach, salt=salt)
    return write_vsav(state, timestamp=timestamp)


def encode_to_file(
    message:   bytes,
    key:       bytes,
    path:      str,
    approach:  Literal["hash", "positional", "constraint_aware"] = "hash",
    salt:      Optional[bytes] = None,
    timestamp: Optional[int] = None,
) -> None:
    """
    Encode a message and write the .vsav to a file path.

    Parameters
    ----------
    message  : plaintext bytes
    key      : shared secret key
    path     : output file path (should end in .vsav)
    approach : encoding approach
    salt     : optional nonce
    timestamp: optional Unix ms timestamp
    """
    vsav_bytes = encode(message, key, approach=approach, salt=salt, timestamp=timestamp)
    with open(path, "wb") as f:
        f.write(vsav_bytes)
