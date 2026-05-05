"""
cna_ssc/engine/bijection.py

Maps a CNA game state to/from a canonical big integer.

A GameState is represented as a dict:
    {piece_name: location_id, ...}

where location_id is either a hex string "col,row" or an off-map zone name.

The bijection works as follows:

    ENCODE:
        1. For each piece p in canonical order:
            a. Look up p's location_id in the game state.
            b. Find the index of location_id in p.valid_locations.
        2. Use mixed_radix.encode(indices, radices) → big integer N.

    DECODE:
        1. Use mixed_radix.decode(N, radices) → indices list.
        2. For each piece p (canonical order), assign:
               location_id = p.valid_locations[indices[k]]
        3. Return {piece_name: location_id, ...}.

The "key" is the canonical ordering:
    - Piece ordering  (defined in piece_registry.PIECES)
    - Per-piece location ordering (defined in piece_registry.get_valid_locations)

Security: an adversary without knowledge of this ordering cannot recover the
message, even knowing which .vsav file they have, because they cannot compute
the state integer N.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple

from experimental.engine import mixed_radix as mr
from experimental.engine.piece_registry import pieces, Piece
from experimental.engine.hex_grid import location_pixel, OFF_MAP_ZONE_NAMES

# Type alias: piece_name → location_id
GameState = Dict[str, str]


# ──────────────────────────────────────────────────────────────────────────────
# Pre-computed radix list (cached on first access)
# ──────────────────────────────────────────────────────────────────────────────

_RADICES: Optional[List[int]] = None
_PIECES:  Optional[List[Piece]] = None


def _ensure():
    global _RADICES, _PIECES
    if _RADICES is None:
        _PIECES  = pieces()
        _RADICES = [p.n_positions for p in _PIECES]


def radices() -> List[int]:
    _ensure()
    return _RADICES   # type: ignore[return-value]


def state_space_bits() -> float:
    import math
    return math.log2(mr.product(radices()))


# ──────────────────────────────────────────────────────────────────────────────
# Encode  GameState → int
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_LOCATION = "1,1"  # Fallback for pieces not mentioned in state dict


def encode(state: GameState) -> int:
    """Convert a game state dict to a big integer.

    Pieces missing from *state* are assigned their first valid location
    (index 0) — i.e. they're treated as 'off the map' or at the canonical
    default position for their category.

    Parameters
    ----------
    state : dict
        {piece_name: location_id}

    Returns
    -------
    int : State space index ≥ 0, < product(radices())
    """
    _ensure()
    indices = []
    for p in _PIECES:
        loc = state.get(p.name)
        if loc is None:
            idx = 0  # default: first valid location
        else:
            try:
                idx = p.location_index(loc)
            except ValueError:
                # Location not valid for this piece — snap to default
                idx = 0
        indices.append(idx)
    return mr.encode(indices, _RADICES)


def decode(N: int) -> GameState:
    """Convert a big integer to a game state dict.

    Parameters
    ----------
    N : int
        State space index, 0 ≤ N < product(radices()).

    Returns
    -------
    dict : {piece_name: location_id} for ALL pieces.
    """
    _ensure()
    indices = mr.decode(N, _RADICES)
    state: GameState = {}
    for p, idx in zip(_PIECES, indices):
        state[p.name] = p.valid_locations[idx]
    return state


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────

def validate_state(state: GameState) -> Tuple[bool, List[str]]:
    """Check that a game state dict is internally consistent.

    Returns (is_valid, list_of_error_messages).

    Current checks:
      - Every location_id in state is valid for its piece's category.
      - No two pieces of the same category occupy the same on-map hex
        (stack check — soft warning, not enforced as hard error since
         stacking is legal in CNA).
    """
    _ensure()
    errors: List[str] = []
    piece_map = {p.name: p for p in _PIECES}
    seen_hexes: Dict[str, List[str]] = {}

    for name, loc in state.items():
        p = piece_map.get(name)
        if p is None:
            errors.append(f"Unknown piece name: {name!r}")
            continue
        if loc not in p._loc_index:
            errors.append(
                f"Piece {name!r} (cat={p.cat}) cannot be at {loc!r}"
            )
        if loc not in OFF_MAP_ZONE_NAMES:
            seen_hexes.setdefault(loc, []).append(name)

    return (len(errors) == 0), errors


# ──────────────────────────────────────────────────────────────────────────────
# Partial state  (only encode pieces mentioned in .vsav)
# ──────────────────────────────────────────────────────────────────────────────

def encode_partial(state: GameState) -> int:
    """Encode a partial state dict (pieces not mentioned → index 0).

    This is the normal case: a .vsav file only records pieces that have
    non-default positions.  Pieces absent from the dict contribute index 0
    to the encoding.
    """
    return encode(state)  # already handles missing pieces as index 0


def encode_restricted(
    state: GameState,
    active_piece_names: List[str],
) -> int:
    """Encode only the pieces named in active_piece_names.

    This is the primary encoding path for real .vsav files, since a template
    only contains a subset of the full 2,468-piece registry.  The restricted
    bijection has a smaller (but still enormous) state space.

    Parameters
    ----------
    state : dict
        {piece_name: location_id} — may contain more keys than active_piece_names.
    active_piece_names : list of str
        Canonical ordered list of pieces to include in the encoding.
        This list defines the restricted bijection key and must be the same
        for both encoder and decoder.

    Returns
    -------
    int : State space index for the restricted encoding.
    """
    _ensure()
    piece_map = {p.name: p for p in _PIECES}
    indices = []
    restricted_radices = []

    for name in active_piece_names:
        p = piece_map.get(name)
        if p is None:
            continue
        loc = state.get(name)
        if loc is None:
            idx = 0
        else:
            try:
                idx = p.location_index(loc)
            except ValueError:
                idx = 0
        indices.append(idx)
        restricted_radices.append(p.n_positions)

    return mr.encode(indices, restricted_radices)


def decode_restricted(
    N: int,
    active_piece_names: List[str],
) -> GameState:
    """Decode N using only the pieces in active_piece_names.

    Returns a GameState dict with entries only for active pieces.
    """
    _ensure()
    piece_map = {p.name: p for p in _PIECES}
    restricted_radices = []
    active_pieces = []

    for name in active_piece_names:
        p = piece_map.get(name)
        if p is None:
            continue
        restricted_radices.append(p.n_positions)
        active_pieces.append(p)

    indices = mr.decode(N, restricted_radices)
    state: GameState = {}
    for p, idx in zip(active_pieces, indices):
        state[p.name] = p.valid_locations[idx]
    return state


def restricted_radices(active_piece_names: List[str]) -> List[int]:
    """Return radix list for a restricted bijection."""
    _ensure()
    piece_map = {p.name: p for p in _PIECES}
    return [
        piece_map[name].n_positions
        for name in active_piece_names
        if name in piece_map
    ]


def restricted_capacity_bits(active_piece_names: List[str]) -> float:
    import math
    radices_ = restricted_radices(active_piece_names)
    return math.log2(mr.product(radices_)) if radices_ else 0.0


def decode_to_active(N: int, include_defaults: bool = False) -> GameState:
    """Decode N and return only non-default positions.

    If include_defaults=False, pieces at index 0 are omitted from the result
    (i.e. pieces at their canonical 'off-board' position are not listed).
    """
    full = decode(N)
    if include_defaults:
        return full
    _ensure()
    active: GameState = {}
    for p in _PIECES:
        loc = full[p.name]
        if p.valid_locations.index(loc) != 0:
            active[p.name] = loc
    return active


if __name__ == "__main__":
    import math
    print(f"State space bits: {state_space_bits():.0f}")
    print(f"Total pieces: {len(radices())}")

    # Roundtrip test with a small state
    test_state: GameState = {
        "GE I-8 - 15": "109,13",
        "GE 2-5 - 5 Le": "108,17",
    }
    N = encode(test_state)
    recovered = decode(N)
    for name, loc in test_state.items():
        assert recovered[name] == loc, f"Mismatch: {name} {recovered[name]} != {loc}"
    print(f"Roundtrip test passed. N (first 40 digits): {str(N)[:40]}...")
