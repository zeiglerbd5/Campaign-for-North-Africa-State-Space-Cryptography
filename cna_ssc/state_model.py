"""
cna_ssc/state_model.py
======================
Canonical in-memory representation of a CNA game state.

GameState  — full board state (all pieces + meta)
PieceState — state of one piece

These are the "lingua franca" between the vsav reader/writer and
the bijection encoder/decoder.  All three encoding approaches
(positional, hash-based, constraint-aware) use the same GameState.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import json

from .constants import (
    PIECES, PIECE_BY_NAME, ELIMINATED_INDEX, LOCATIONS,
    FACE_FULL, AIR_READY, nearest_location_index, zone_index,
    STATUS_FLAG_NAMES,
)


# ---------------------------------------------------------------------------
# PieceState
# ---------------------------------------------------------------------------

@dataclass
class PieceState:
    """
    State of a single piece.

    Attributes
    ----------
    name         : canonical piece name (must be in PIECE_BY_NAME)
    location_idx : index into LOCATIONS (0=hex[0,0], ..., ELIMINATED_INDEX=off)
    face         : 0=full strength, 1=reduced strength  (ground units only)
    status_byte  : 8 status flags packed as one byte     (ground units only)
    air_state    : aircraft readiness (0-4)              (aircraft only)
    air_sorties  : sorties flown this turn (0-3)         (aircraft only)
    vassal_id    : Vassal internal piece ID (int), used for .vsav round-trips
    stack_id     : Vassal stack ID, used for .vsav round-trips
    """
    name:         str
    location_idx: int  = ELIMINATED_INDEX
    face:         int  = FACE_FULL
    status_byte:  int  = 0
    air_state:    int  = AIR_READY
    air_sorties:  int  = 0
    vassal_id:    Optional[int] = None
    stack_id:     Optional[int] = None

    # ---- derived ----

    @property
    def piece_type(self) -> str:
        return PIECE_BY_NAME[self.name]["type"]

    @property
    def side(self) -> Optional[str]:
        return PIECE_BY_NAME[self.name].get("side")

    @property
    def location(self) -> dict:
        return LOCATIONS[self.location_idx]

    @property
    def pixel_xy(self) -> Optional[tuple[int, int]]:
        loc = self.location
        if "px" in loc:
            return loc["px"], loc["py"]
        return None

    @property
    def is_on_map(self) -> bool:
        return self.location.get("type") == "hex"

    @property
    def is_eliminated(self) -> bool:
        return self.location_idx == ELIMINATED_INDEX

    # ---- radix digit encoding ----

    def to_digits(self) -> list[int]:
        """Encode piece state as a list of mixed-radix digits."""
        ptype = self.piece_type
        if ptype == "ground_unit":
            return [self.location_idx, self.face, self.status_byte]
        elif ptype == "aircraft":
            return [self.location_idx, self.air_state, self.air_sorties]
        else:
            return [self.location_idx]

    @classmethod
    def from_digits(cls, name: str, digits: list[int]) -> "PieceState":
        """Reconstruct PieceState from mixed-radix digits."""
        p = cls(name=name)
        ptype = PIECE_BY_NAME[name]["type"]
        if ptype == "ground_unit" and len(digits) >= 3:
            p.location_idx = digits[0]
            p.face         = digits[1]
            p.status_byte  = digits[2]
        elif ptype == "aircraft" and len(digits) >= 3:
            p.location_idx = digits[0]
            p.air_state    = digits[1]
            p.air_sorties  = digits[2]
        elif digits:
            p.location_idx = digits[0]
        return p

    # ---- active status flags ----

    def active_status_markers(self) -> list[str]:
        """Return list of Vassal marker names for active status flags."""
        return [
            marker_name
            for flag, marker_name in STATUS_FLAG_NAMES.items()
            if self.status_byte & flag
        ]

    def set_status_flag(self, flag: int) -> None:
        self.status_byte |= flag

    def clear_status_flag(self, flag: int) -> None:
        self.status_byte &= ~flag

    # ---- serialization ----

    def to_dict(self) -> dict:
        return {
            "name":         self.name,
            "location_idx": self.location_idx,
            "face":         self.face,
            "status_byte":  self.status_byte,
            "air_state":    self.air_state,
            "air_sorties":  self.air_sorties,
            "vassal_id":    self.vassal_id,
            "stack_id":     self.stack_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PieceState":
        return cls(
            name         = d["name"],
            location_idx = d.get("location_idx", ELIMINATED_INDEX),
            face         = d.get("face", FACE_FULL),
            status_byte  = d.get("status_byte", 0),
            air_state    = d.get("air_state", AIR_READY),
            air_sorties  = d.get("air_sorties", 0),
            vassal_id    = d.get("vassal_id"),
            stack_id     = d.get("stack_id"),
        )

    def __repr__(self) -> str:
        loc = self.location
        loc_str = (
            f"hex({loc['col']},{loc['row']})" if loc["type"] == "hex"
            else loc.get("name", "eliminated")
        )
        return f"PieceState({self.name!r}, loc={loc_str})"


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

@dataclass
class GameState:
    """
    Complete game state: all 1307 canonical pieces + global meta.

    Pieces are stored in canonical order (matching PIECES in constants.py).
    Pieces not present in the .vsav are assigned ELIMINATED_INDEX.

    Meta state:
        turn      : 1-111  (game turn)
        op_stage  : 1-3    (operation stage within turn)
        weather   : 0-5    (weather condition index)
    """
    pieces:   dict[str, PieceState]   = field(default_factory=dict)
    turn:     int                     = 1
    op_stage: int                     = 1
    weather:  int                     = 0

    # ---- construction ----

    @classmethod
    def default(cls) -> "GameState":
        """Create a GameState with all pieces at ELIMINATED_INDEX."""
        gs = cls()
        for p in PIECES:
            gs.pieces[p["name"]] = PieceState(name=p["name"])
        return gs

    def get_piece(self, name: str) -> PieceState:
        """Get piece by name, creating an eliminated placeholder if absent."""
        if name not in self.pieces:
            self.pieces[name] = PieceState(name=name)
        return self.pieces[name]

    # ---- bulk digit encoding (for bijection) ----

    def to_digit_segments(self) -> tuple[list[list[int]], list[list[int]]]:
        """
        Return (segments, radices_per_segment) for all pieces + meta.

        Pieces are emitted in canonical PIECES order so that the ordinal
        is always computed identically regardless of insertion order.

        The meta state (turn, op_stage, weather) is appended last.
        """
        from .constants import PIECES, get_radices, RADICES_META

        segments:           list[list[int]] = []
        radices_per_segment: list[list[int]] = []

        for p_def in PIECES:
            name  = p_def["name"]
            piece = self.pieces.get(name) or PieceState(name=name)
            digits = piece.to_digits()
            rads   = get_radices(p_def["type"])
            segments.append(digits)
            radices_per_segment.append(list(rads))

        # Meta: turn (1-based -> 0-based), op_stage (1-based -> 0-based), weather
        segments.append([self.turn - 1, self.op_stage - 1, self.weather])
        radices_per_segment.append(RADICES_META)

        return segments, radices_per_segment

    @classmethod
    def from_digit_segments(
        cls,
        segments:            list[list[int]],
        radices_per_segment: list[list[int]],
    ) -> "GameState":
        """Reconstruct GameState from per-piece digit segments."""
        from .constants import PIECES

        gs = cls()
        for i, p_def in enumerate(PIECES):
            name   = p_def["name"]
            digits = segments[i] if i < len(segments) else []
            gs.pieces[name] = PieceState.from_digits(name, digits)

        # Meta is last segment
        if len(segments) > len(PIECES):
            meta = segments[len(PIECES)]
            gs.turn      = meta[0] + 1 if len(meta) > 0 else 1
            gs.op_stage  = meta[1] + 1 if len(meta) > 1 else 1
            gs.weather   = meta[2]     if len(meta) > 2 else 0

        return gs

    # ---- convenience queries ----

    def pieces_on_map(self) -> list[PieceState]:
        """Return all pieces with a valid on-map hex location."""
        return [ps for ps in self.pieces.values() if ps.is_on_map]

    def pieces_in_zone(self, zone_name: str) -> list[PieceState]:
        """Return all pieces in the named off-map zone."""
        idx = zone_index(zone_name)
        return [ps for ps in self.pieces.values() if ps.location_idx == idx]

    def pieces_by_side(self, side: str) -> list[PieceState]:
        """Return all pieces for 'allied' or 'axis'."""
        result = []
        for name, ps in self.pieces.items():
            if PIECE_BY_NAME.get(name, {}).get("side") == side:
                result.append(ps)
        return result

    # ---- serialization ----

    def to_dict(self) -> dict:
        return {
            "turn":     self.turn,
            "op_stage": self.op_stage,
            "weather":  self.weather,
            "pieces":   {name: ps.to_dict() for name, ps in self.pieces.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        gs = cls(
            turn     = d.get("turn", 1),
            op_stage = d.get("op_stage", 1),
            weather  = d.get("weather", 0),
        )
        for name, pdata in d.get("pieces", {}).items():
            gs.pieces[name] = PieceState.from_dict(pdata)
        return gs

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "GameState":
        return cls.from_dict(json.loads(s))

    # ---- summary ----

    def summary(self) -> str:
        on_map    = sum(1 for ps in self.pieces.values() if ps.is_on_map)
        in_zones  = sum(1 for ps in self.pieces.values() if ps.location.get("type") == "zone")
        elim      = sum(1 for ps in self.pieces.values() if ps.is_eliminated)
        return (
            f"GameState: turn={self.turn}, op={self.op_stage}, weather={self.weather} | "
            f"pieces: {on_map} on-map, {in_zones} in zones, {elim} eliminated"
        )

    def __repr__(self) -> str:
        return self.summary()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from .constants import PIECES

    print("=== GameState Self-Test ===\n")

    gs = GameState.default()
    print(f"Default state: {gs.summary()}")
    assert len(gs.pieces) == len(PIECES)

    # Place a piece
    gs.get_piece("GE I-8 - 15").location_idx = nearest_location_index(7964, 1173)
    gs.get_piece("GE I-8 - 15").face = FACE_FULL

    # Encode to digits
    segs, rads = gs.to_digit_segments()
    assert len(segs) == len(PIECES) + 1   # pieces + meta
    print(f"Digit segments: {len(segs)} (including meta) ✓")

    # Round-trip through digits
    gs2 = GameState.from_digit_segments(segs, rads)
    ps1 = gs.get_piece("GE I-8 - 15")
    ps2 = gs2.get_piece("GE I-8 - 15")
    assert ps1.location_idx == ps2.location_idx, f"{ps1.location_idx} != {ps2.location_idx}"
    print(f"Digit round-trip for GE I-8 - 15: loc_idx={ps2.location_idx} ✓")

    # JSON round-trip
    j = gs.to_json()
    gs3 = GameState.from_json(j)
    assert gs3.get_piece("GE I-8 - 15").location_idx == ps1.location_idx
    print(f"JSON round-trip ✓")

    print("\nAll tests passed.")
