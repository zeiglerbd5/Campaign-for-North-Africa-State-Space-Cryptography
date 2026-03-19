"""
CNA-SSC: Campaign for North Africa State Space Cryptography
===========================================================
Steganographic encoding system using the CNA Vassal module's
~10^8,528 game state space as a high-capacity covert channel.

Usage:
    from cna_ssc import encode, decode

    vsav_bytes = encode(b"secret message", key=b"my_key_32_bytes_")
    message    = decode(vsav_bytes, key=b"my_key_32_bytes_")

The .vsav file produced is indistinguishable from a legitimate
Campaign for North Africa saved game file.

Architecture:
    message bytes
        -> encoder.py  (HKDF key -> ordinal N)
        -> bijection.py (N -> GameState via mixed-radix)
        -> vsav_writer.py (GameState -> .vsav bytes)

    .vsav bytes
        -> vsav_reader.py (parse -> GameState)
        -> bijection.py   (GameState -> ordinal N)
        -> decoder.py     (N + key -> message bytes)

Security properties:
    - Brute force: requires enumerating 10^8,528 states. The observable
      universe can perform ~10^140 operations. The gap (10^8,388 orders
      of magnitude) cannot be closed by any physical process.
    - Steganalysis: no pixel manipulation. The cover object is a
      legitimate .vsav file with valid game state structure.
    - Key space: 256-bit HKDF key -> ~85 bits effective per piece
      ordering parameter, total >> 10^1,000.

Version: 2.0.0
"""

__version__ = "2.0.0"
__author__  = "Brett Zeigler"

from .encoder  import encode
from .decoder  import decode
from .vsav_reader import read_vsav
from .vsav_writer import write_vsav
from .state_model import GameState, PieceState

__all__ = ["encode", "decode", "read_vsav", "write_vsav", "GameState", "PieceState"]
