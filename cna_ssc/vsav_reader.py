"""
cna_ssc/vsav_reader.py
======================
Parse a Vassal .vsav file into a GameState.

.vsav format (CNAv2.1.0, Vassal 3.7.15):
    ZIP archive containing three files:
        moduledata   — XML metadata (module name, version)
        savedata     — XML metadata (timestamp)
        savedGame    — VCSK-encoded game state

    savedGame format:
        "!VCSK" + hex_string
        where hex_string decodes to bytes XOR'd with 0x7d.
        The XOR'd bytes are UTF-8/latin-1 Vassal command strings,
        separated by ESC (0x1b) characters.

    Command format:
        +/TIMESTAMP/COMMAND_TYPE;arg1;arg2;...
        (see Vassal source: GameState.java, AddPiece.execute(), etc.)

Parsed commands:
    sendto    — piece placement (position + full piece state)
    stack     — piece grouping
    mark      — piece status marker
    piece     — standalone piece definition
"""

from __future__ import annotations
import io
import re
import zipfile
from typing import Optional

from .state_model import GameState, PieceState
from .constants   import (
    PIECE_BY_NAME, nearest_location_index, zone_index,
    ELIMINATED_INDEX, MARKER_TO_FLAG,
    FACE_FULL, FACE_REDUCED,
    AIR_READY, NUM_AIR_STATES,
    WEATHER_NAMES,
)

# ---------------------------------------------------------------------------
# XOR decoding
# ---------------------------------------------------------------------------

VSAV_XOR_KEY = 0x7D   # empirically determined from buildFile analysis


def _decode_saved_game(raw: bytes) -> str:
    """
    Decode a Vassal savedGame file.
    Strips the '!VCSK' header, hex-decodes, then XOR's each byte with 0x7D.
    Returns the decoded command string.
    """
    text = raw.decode("ascii", errors="replace").strip()
    if text.startswith("!VCSK"):
        hex_str = text[5:]
        data    = bytes.fromhex(hex_str)
        decoded = bytes(b ^ VSAV_XOR_KEY for b in data)
        return decoded.decode("latin-1", errors="replace")
    # Fallback: try interpreting as plain text
    return text


def _encode_saved_game(command_string: str) -> bytes:
    """
    Re-encode a Vassal command string into savedGame format.
    Inverse of _decode_saved_game.
    """
    encoded = bytes(ord(c) ^ VSAV_XOR_KEY for c in command_string)
    return b"!VCSK" + encoded.hex().encode("ascii")


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

_CMD_RE = re.compile(r"^\+/(\d+)/(\w+);(.*)", re.DOTALL)


def _parse_commands(decoded: str) -> list[dict]:
    """
    Split decoded game state into individual command records.
    Each command is separated by ESC (\\x1b).
    """
    commands = []
    parts    = decoded.split("\x1b")

    for part in parts:
        part = part.strip("\x00\r\n")
        if not part:
            continue

        if part == "\x00begin_save" or part.startswith("\x00begin_save"):
            commands.append({"type": "begin_save", "raw": part})
            continue

        m = _CMD_RE.match(part)
        if m:
            timestamp, cmd_type, args_str = m.groups()
            commands.append({
                "type":      cmd_type,
                "timestamp": int(timestamp),
                "args":      args_str,
                "raw":       part,
            })
        elif part.startswith("+/"):
            # Partial parse
            commands.append({"type": "unknown", "raw": part})

    return commands


# ---------------------------------------------------------------------------
# Piece-state extraction from sendto / mark commands
# ---------------------------------------------------------------------------

def _extract_piece_name(args: str) -> Optional[str]:
    """
    Extract the canonical piece name from a Vassal command args string.
    Looks for 'piece;;;IMAGE_NAME.png;PIECE_NAME/' pattern.
    """
    # Pattern: piece;;;IMAGE;NAME/...
    m = re.search(r"piece;;;[^;]*;([^/\t]+)/", args)
    if m:
        raw_name = m.group(1).strip()
        # Try exact match first
        if raw_name in PIECE_BY_NAME:
            return raw_name
        # Try trimming trailing path components
        for candidate in PIECE_BY_NAME:
            if candidate in raw_name or raw_name.startswith(candidate):
                return candidate
        # Try partial match
        parts = raw_name.split(" - ")
        for candidate in PIECE_BY_NAME:
            if all(part in candidate for part in parts[:2]):
                return candidate
        return raw_name  # Return unknown name anyway
    return None


def _extract_position(args: str) -> Optional[tuple[int, int, str]]:
    """
    Extract pixel position and map name from a Vassal command.
    Returns (px, py, map_name) or None.

    Position patterns:
        Map0;1;7964,1173  -> main map
        Main Map;7964;1173
        null;7964;1173
        MapN;1;x,y
    """
    # Pattern 1: MapN;1;x,y (position in a comma-separated field)
    m = re.search(r"(Main Map|Map\d+);(\d+);(\d+),(\d+)", args)
    if m:
        map_name = m.group(1)
        x, y = int(m.group(3)), int(m.group(4))
        return x, y, map_name

    # Pattern 2: MapName;x;y (three separate fields)
    m = re.search(r"(Main Map|Map\d+);(\d+);(\d+)", args)
    if m:
        x, y = int(m.group(2)), int(m.group(3))
        return x, y, m.group(1)

    # Pattern 3: null;x;y
    m = re.search(r"null;(\d+);(\d+)", args)
    if m:
        x, y = int(m.group(1)), int(m.group(2))
        return x, y, "Main Map"

    return None


def _extract_zone_name(args: str) -> Optional[str]:
    """
    Extract holding box / zone name from sendto command.
    e.g. sendto;Return to Organisation Chart;82,130;BR 70th Infantry Division;...
    """
    # Zone is typically in the sendto destination description
    from .constants import OFFMAP_ZONES
    zone_names = {z[0] for z in OFFMAP_ZONES}

    for zone_name in zone_names:
        if zone_name in args:
            return zone_name
    return None


def _extract_vassal_id(args: str) -> Optional[int]:
    """Extract Vassal internal piece ID (gpid-like integer from piece state)."""
    # Pattern: null;x;y;ID  or  MapName;x;y;ID
    m = re.search(r";\d+;\d+;(\d+)", args)
    if m:
        return int(m.group(1))
    return None


def _extract_face(args: str) -> int:
    """
    Extract unit face (full=0 / reduced=1) from piece image name.
    Image names encode strength: 'BR 1 Buffs - none.png' vs 'BR 1 Buffs - xx.png'
    """
    # Reduced strength units typically have strength value in name
    # e.g. 'GE I-8 - 15.png' = full, 'GE I-8Rd - 15.png' = reduced
    if "Rd" in args or "reduced" in args.lower():
        return FACE_REDUCED
    return FACE_FULL


def _parse_sendto_command(cmd: dict) -> Optional[dict]:
    """
    Parse a 'sendto' command into a piece placement record.
    Returns dict with keys: name, px, py, map_name, zone_name, face, vassal_id
    """
    args = cmd.get("args", "")

    piece_name = _extract_piece_name(args)
    if not piece_name:
        return None

    pos       = _extract_position(args)
    zone_name = _extract_zone_name(args)
    vassal_id = _extract_vassal_id(args)
    face      = _extract_face(args)

    result = {
        "name":      piece_name,
        "zone_name": zone_name,
        "face":      face,
        "vassal_id": vassal_id,
    }
    if pos:
        result["px"], result["py"], result["map_name"] = pos
    return result


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def read_vsav(vsav_bytes: bytes) -> GameState:
    """
    Parse a .vsav file (as bytes) into a GameState.

    Parameters
    ----------
    vsav_bytes : raw bytes of the .vsav file (ZIP archive)

    Returns a GameState with all pieces placed at their Vassal positions.
    """
    # 1. Unzip
    with zipfile.ZipFile(io.BytesIO(vsav_bytes)) as zf:
        saved_game_raw = zf.read("savedGame")

    # 2. Decode VCSK -> command string
    command_str = _decode_saved_game(saved_game_raw)

    # 3. Parse commands
    commands = _parse_commands(command_str)

    # 4. Build GameState
    gs = GameState.default()

    # Track all piece placements keyed by Vassal ID for stack resolution
    placement_by_id: dict[int, dict] = {}

    for cmd in commands:
        cmd_type = cmd.get("type", "")

        if cmd_type == "sendto":
            record = _parse_sendto_command(cmd)
            if record:
                _apply_placement(gs, record)
                if record.get("vassal_id"):
                    placement_by_id[record["vassal_id"]] = record

        elif cmd_type == "mark":
            _apply_mark_command(gs, cmd, placement_by_id)

        elif cmd_type in ("piece", "clone"):
            record = _parse_sendto_command(cmd)
            if record:
                _apply_placement(gs, record)

    # 5. Extract game-turn marker position -> turn number
    _extract_game_meta(gs, command_str)

    return gs


def _apply_placement(gs: GameState, record: dict) -> None:
    """Apply a piece placement record to the GameState."""
    name = record["name"]

    # Only track pieces we know about
    if name not in PIECE_BY_NAME:
        return

    ps = gs.get_piece(name)
    ps.face      = record.get("face", FACE_FULL)
    ps.vassal_id = record.get("vassal_id")

    # Determine location
    if record.get("zone_name"):
        ps.location_idx = zone_index(record["zone_name"])
    elif "px" in record and "py" in record:
        map_name = record.get("map_name", "")
        if "Main Map" in map_name or map_name.startswith("Map0"):
            ps.location_idx = nearest_location_index(record["px"], record["py"])
        else:
            # Other maps (control sheets) -> eliminated sentinel
            ps.location_idx = ELIMINATED_INDEX
    else:
        ps.location_idx = ELIMINATED_INDEX


def _apply_mark_command(
    gs:               GameState,
    cmd:              dict,
    placement_by_id:  dict,
) -> None:
    """Apply a 'mark' command (status marker) to a piece's status_byte."""
    args = cmd.get("args", "")

    # Find the marker type from the args
    for marker_name, flag in MARKER_TO_FLAG.items():
        if marker_name in args:
            # Find which piece this marker is on
            # Markers often reference the piece they're stacked with via vassal_id
            vassal_id = _extract_vassal_id(args)
            if vassal_id and vassal_id in placement_by_id:
                piece_name = placement_by_id[vassal_id].get("name")
                if piece_name and piece_name in gs.pieces:
                    gs.pieces[piece_name].set_status_flag(flag)
            break


def _extract_game_meta(gs: GameState, command_str: str) -> None:
    """
    Extract game turn, operation stage, and weather from game state string.
    Looks for mGame-Turn and mOpStage marker positions.
    """
    # mGame-Turn marker position encodes the current turn on the game track
    # This is a simplified extraction — full implementation would use
    # the Game Track zone geometry from buildFile.xml
    pass   # GameState defaults to turn=1, op_stage=1, weather=0


# ---------------------------------------------------------------------------
# Raw command string access (for debugging)
# ---------------------------------------------------------------------------

def parse_vsav_raw(vsav_bytes: bytes) -> tuple[list[dict], str]:
    """
    Return (commands, decoded_string) for inspection.
    """
    with zipfile.ZipFile(io.BytesIO(vsav_bytes)) as zf:
        saved_game_raw = zf.read("savedGame")
    command_str = _decode_saved_game(saved_game_raw)
    commands    = _parse_commands(command_str)
    return commands, command_str


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    test_vsav = "/mnt/user-data/uploads/Operation_Brevity_set-upv0_93.vsav"
    if os.path.exists(test_vsav):
        print("=== vsav_reader Self-Test ===\n")
        with open(test_vsav, "rb") as f:
            vsav_bytes = f.read()

        commands, raw = parse_vsav_raw(vsav_bytes)
        print(f"Commands parsed: {len(commands)}")
        from collections import Counter
        types = Counter(c["type"] for c in commands)
        for t, n in types.most_common():
            print(f"  {t}: {n}")
        print()

        gs = read_vsav(vsav_bytes)
        print(gs.summary())
        print()

        on_map = gs.pieces_on_map()
        print(f"Pieces on main map ({len(on_map)}):")
        for ps in on_map[:15]:
            loc = ps.location
            print(f"  {ps.name:45s} hex({loc['col']:3d},{loc['row']:2d})")
        print("...")
    else:
        print(f"Test file not found: {test_vsav}")
        print("Run from project root with test .vsav available.")
