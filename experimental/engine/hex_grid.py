"""
cna_ssc/engine/hex_grid.py

Vassal ↔ CNA hex coordinate system for CNAv2.1.0.vmod.

The main map uses a sideways (flat-top) hex grid with parameters extracted
from buildFile.xml:
    dx=72.95  (horizontal spacing between column centres)
    dy=85.25  (vertical spacing between row centres)
    x0=-15.0  (x offset of column-0 centre)
    y0=4.0    (y offset of row-0 centre)
    sideways=True  → odd columns are offset DOWN by dy/2

Valid play area: cols 1–174, rows 1–29 (empirically determined from zone paths).

Off-map zones are named regions that pieces can occupy in addition to the hex
grid.  Their canonical Vassal pixel centre is derived from the zone path
bounding boxes.

Hex IDs used internally are strings like "109,13" (Vassal col, row).
Off-map zone IDs are the zone names as strings.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict

# ──────────────────────────────────────────────────────────────────────────────
# Grid constants  (from buildFile.xml HexGrid element)
# ──────────────────────────────────────────────────────────────────────────────
DX: float = 72.95
DY: float = 85.25
X0: float = -15.0
Y0: float = 4.0

# Playable column / row bounds
COL_MIN, COL_MAX = 1, 174
ROW_MIN, ROW_MAX = 1, 29

# ──────────────────────────────────────────────────────────────────────────────
# Named off-map zones  (extracted from buildFile.xml Zone elements)
# Order is canonical and must never change — it is part of the bijection key.
# ──────────────────────────────────────────────────────────────────────────────
OFF_MAP_ZONES: List[Dict] = [
    # name, representative pixel (centre of zone path bounding box from buildFile.xml)
    # IMPORTANT: zone centres must be >200px apart for reliable detection
    {"name": "Malta",                            "px": 10740, "py":  340},
    {"name": "Sicily Holding Box",               "px": 10150, "py":  340},
    {"name": "Italy Holding Box",                "px":  9560, "py":  340},
    {"name": "Crete Holding Box",                "px": 11330, "py":  340},
    {"name": "Benghazi Holding Box",             "px":  5200, "py": 2200},
    {"name": "Derna Holding Box",                "px":  6500, "py": 2200},
    {"name": "Bardia Holding Box",               "px":  8100, "py": 2200},
    {"name": "Tobruk Holding Box",               "px":  7200, "py": 2200},
    {"name": "Alexandria Holding Box Hex 3613",  "px": 12072, "py": 1863},
    {"name": "Alexandria Holding Box Hex 3714",  "px": 12270, "py": 1734},
    {"name": "Tunis",                            "px":  1000, "py": 1600},
    {"name": "Gabes",                            "px":  1750, "py": 1600},
    {"name": "Tripoli",                          "px":   165, "py": 1895},
    {"name": "Tripolitania",                     "px":   165, "py": 2193},
    {"name": "Gabes-Tripoli",                    "px":   347, "py": 1747},
    {"name": "Tripoli-Tripolitania",             "px":   347, "py": 2042},
    {"name": "Tripolitania-Nofilia",             "px":   347, "py": 2327},
    {"name": "German Shipping",                  "px":  9800, "py":  600},
    {"name": "Game Track",                       "px": 12200, "py": 1500},
    {"name": "OC",                               "px": 11000, "py": 1900},
    {"name": "Map A",                            "px":  1500, "py":  200},
    {"name": "Map B",                            "px":  4000, "py":  200},
    {"name": "Map C",                            "px":  6500, "py":  200},
    {"name": "Map D",                            "px":  9000, "py":  200},
    {"name": "Map E",                            "px": 11500, "py":  200},
    {"name": "Map D Operation Stage",            "px":  9000, "py": 1912},
    {"name": "Map E Operation Stage",            "px": 11670, "py": 1892},
    {"name": "Weather Track",                    "px": 12000, "py":  200},
    {"name": "Axis Control",                     "px":  1000, "py": 3000},
    {"name": "Comm Control",                     "px":  5000, "py": 3000},
]

OFF_MAP_ZONE_NAMES: List[str] = [z["name"] for z in OFF_MAP_ZONES]
OFF_MAP_ZONE_BY_NAME: Dict[str, Dict] = {z["name"]: z for z in OFF_MAP_ZONES}

# ──────────────────────────────────────────────────────────────────────────────
# Coordinate conversion
# ──────────────────────────────────────────────────────────────────────────────

def pixel_to_hex(px: float, py: float) -> Tuple[int, int]:
    """Convert Vassal pixel coordinates to (col, row) hex indices.

    Uses the flat-top (sideways) hex formula:
        col = round((px - x0) / dx)
        row = round((py - y0 - (col%2)*(dy/2)) / dy)
    """
    col = round((px - X0) / DX)
    row = round((py - Y0 - (col % 2) * (DY / 2)) / DY)
    return col, row


def hex_to_pixel(col: int, row: int) -> Tuple[float, float]:
    """Convert (col, row) hex indices to Vassal pixel centre coordinates."""
    px = X0 + col * DX
    py = Y0 + row * DY + (col % 2) * (DY / 2)
    return px, py


def hex_id(col: int, row: int) -> str:
    """Canonical string ID for a hex: 'col,row'."""
    return f"{col},{row}"


def parse_hex_id(hid: str) -> Tuple[int, int]:
    """Parse canonical hex ID back to (col, row)."""
    col, row = hid.split(",")
    return int(col), int(row)


def nearest_hex(px: float, py: float) -> Tuple[int, int]:
    """Snap a pixel coordinate to the nearest valid hex centre."""
    col = round((px - X0) / DX)
    row = round((py - Y0 - (col % 2) * (DY / 2)) / DY)
    col = max(COL_MIN, min(COL_MAX, col))
    row = max(ROW_MIN, min(ROW_MAX, row))
    return col, row


def pixel_distance(px1: float, py1: float, px2: float, py2: float) -> float:
    return math.sqrt((px2 - px1) ** 2 + (py2 - py1) ** 2)


def location_id_from_pixel(px: float, py: float, zone_hint: str = "") -> str:
    """Determine the canonical location ID for a piece at (px, py).

    If zone_hint is a known zone name, return it directly.
    Otherwise snap to the nearest valid hex.
    """
    if zone_hint and zone_hint in OFF_MAP_ZONE_BY_NAME:
        return zone_hint
    col, row = nearest_hex(px, py)
    return hex_id(col, row)


# ──────────────────────────────────────────────────────────────────────────────
# All valid hex positions (pre-computed, canonical order)
# ──────────────────────────────────────────────────────────────────────────────

def _build_hex_positions() -> List[str]:
    """Load the canonical set of playable CNA map hexes.

    These are derived from the Map A–E polygon zones in buildFile.xml via
    point-in-polygon testing against the Vassal snap grid.  Only positions
    that fall inside a real printed CNA map section are included — sea,
    off-map whitespace, and non-playable corners are excluded.

    The list is pre-computed and stored in data/playable_hexes.json.
    Falls back to the pixel bounding-box approach if the file is absent.
    """
    import json, os
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'playable_hexes.json')
    data_path = os.path.normpath(data_path)
    if os.path.exists(data_path):
        with open(data_path) as f:
            return json.load(f)
    # Fallback: coarse pixel bounding box (4,959 positions, includes non-map area)
    positions = []
    for col in range(COL_MIN, COL_MAX + 1):
        for row in range(ROW_MIN, ROW_MAX + 1):
            px, py = hex_to_pixel(col, row)
            if 0 <= px <= 13000 and 50 <= py <= 2500:
                positions.append(hex_id(col, row))
    return positions


HEX_POSITIONS: List[str] = _build_hex_positions()
HEX_POSITION_INDEX: Dict[str, int] = {h: i for i, h in enumerate(HEX_POSITIONS)}

# All locations = hexes + off-map zones
ALL_LOCATIONS: List[str] = HEX_POSITIONS + OFF_MAP_ZONE_NAMES
LOCATION_INDEX: Dict[str, int] = {loc: i for i, loc in enumerate(ALL_LOCATIONS)}

N_HEX_POSITIONS: int = len(HEX_POSITIONS)
N_OFF_MAP_ZONES: int = len(OFF_MAP_ZONE_NAMES)
N_ALL_LOCATIONS: int = len(ALL_LOCATIONS)


def location_pixel(loc_id: str) -> Tuple[float, float]:
    """Return the Vassal pixel centre for a location ID."""
    if loc_id in OFF_MAP_ZONE_BY_NAME:
        z = OFF_MAP_ZONE_BY_NAME[loc_id]
        return float(z["px"]), float(z["py"])
    col, row = parse_hex_id(loc_id)
    return hex_to_pixel(col, row)


if __name__ == "__main__":
    print(f"Valid hex positions:  {N_HEX_POSITIONS}")
    print(f"Off-map zones:       {N_OFF_MAP_ZONES}")
    print(f"Total locations:     {N_ALL_LOCATIONS}")
    print(f"Sample hex IDs:      {HEX_POSITIONS[:5]} ... {HEX_POSITIONS[-3:]}")
    print()
    # Verify calibration points
    t_col, t_row = pixel_to_hex(256, 1895)
    a_col, a_row = pixel_to_hex(12073, 1863)
    print(f"Tripoli   (256,1895)   → hex {t_col},{t_row}  (zone 'Tripoli')")
    print(f"Alexandria(12073,1863) → hex {a_col},{a_row}  (zone 'Alexandria Holding Box Hex 3613')")
