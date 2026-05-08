"""
CNA-SSC: Campaign for North Africa State Space Cryptography
===========================================================
Steganographic encoding system that hides messages in PNG board
images by exploiting the astronomical state space of the
Campaign for North Africa board game.

Usage:
    python -m cna_ssc img-encode --message "secret" --key "passphrase"
    python -m cna_ssc img-decode --in cna_board.png --key "passphrase"

The output PNG looks like a screenshot of a CNA game in progress.
Without the key, an observer cannot distinguish it from a legitimate
mid-game board state.

Security properties:
    - State space is ~10^8,528, dwarfing AES-256 (~10^77) and Go
      (~10^170). Even Grover's quantum search on this space exceeds
      the operations the observable universe can perform.
    - Cover object is a board screenshot — sharing screenshots is
      routine in the wargaming community, providing deniability.
    - HMAC-SHA256 authentication detects tampering or wrong keys.

Version: 2.0.0
"""

__version__ = "2.0.0"
__author__  = "Brett Zeigler"
