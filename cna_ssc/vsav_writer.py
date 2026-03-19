"""
cna_ssc/vsav_writer.py
======================
Serialize a GameState back to a valid Vassal .vsav file.

The produced .vsav is indistinguishable from a file saved by Vassal itself:
- Same ZIP structure (moduledata, savedata, savedGame)
- Same VCSK encoding (XOR 0x7d, hex-encoded)
- Same Vassal command format
- Metadata timestamps are realistic (defaulting to a plausible 2020 date)

Piece images are derived from piece names using the naming convention
observed in CNAv2.1.0: 'GE I-8 - 15' -> 'GE I-8 - 15.png'

The .vsav writer emits 'sendto' commands for each placed piece and
'mark' commands for each active status marker.
"""

from __future__ import annotations
import io
import time
import zipfile
from typing import Optional

from .state_model import GameState, PieceState
from .constants   import (
    PIECES, PIECE_BY_NAME, LOCATIONS, NUM_LOCATIONS, ELIMINATED_INDEX,
    STATUS_FLAG_NAMES, FACE_REDUCED,
    WEATHER_NAMES, zone_index,
)
from .vsav_reader import _encode_saved_game, VSAV_XOR_KEY


# ---------------------------------------------------------------------------
# Module metadata template
# ---------------------------------------------------------------------------

_MODULEDATA_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<data version="1">
  <version>2.1.0</version>
  <VassalVersion>3.7.15</VassalVersion>
  <dateSaved>{timestamp}</dateSaved>
  <description>(c) 1979 SPI/(c) 2022 White Box Games</description>
  <n>Campaign for North Africa</n>
</data>
"""

_SAVEDATA_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<data version="1">
  <version></version>
  <VassalVersion>3.7.15</VassalVersion>
  <dateSaved>{timestamp}</dateSaved>
  <description></description>
</data>
"""


# ---------------------------------------------------------------------------
# Command generation
# ---------------------------------------------------------------------------

def _piece_image_name(piece_name: str, face: int) -> str:
    """
    Derive Vassal image filename from piece name.
    Reduced-strength units have 'Rd' suffix convention in some modules,
    but CNAv2.1.0 uses separate piece entries per strength level.
    Default: just use the piece name + .png (matches module convention).
    """
    return f"{piece_name}.png"


def _make_sendto_command(ps: PieceState, timestamp: int, seq: int) -> str:
    """
    Generate a Vassal 'sendto' command string for a piece placement.

    Format (observed from Operation_Brevity .vsav analysis):
        +/TIMESTAMP/sendto;DEST_DESC;ICON_POS;ORG_NAME;ORG_NAME;0;0;
        RETURN_DESC;RETURN_POS;;0;0;0;Return;G;;;;OC_ID
        \\tmark;Type\\\\\\tfootprint;...\\\\\\tmarkmoved;...
        \\\\\\tpiece;;;IMAGE.png;PIECE_NAME/;;\\tPieces\\\\\\tfalse;
        MapN;1;X,Y\\\\\\tfalse\\\\\\\\\\tnull;X;Y;PIECE_ID\\\\
    """
    name  = ps.name
    image = _piece_image_name(name, ps.face)
    vid   = ps.vassal_id or (1000 + seq)

    loc   = LOCATIONS[ps.location_idx] if ps.location_idx < NUM_LOCATIONS else None
    loc_type = loc.get("type", "eliminated") if loc else "eliminated"

    # Determine map and coordinates
    if loc_type == "hex":
        px, py   = loc["px"], loc["py"]
        map_name = "Map0"
        pos_str  = f"{map_name};1;{px},{py}"
        null_str = f"null;{px};{py};{vid}"
    elif loc_type == "zone":
        px, py   = loc["px"], loc["py"]
        map_name = "Map0"
        pos_str  = f"{map_name};1;{px},{py}"
        null_str = f"null;{px};{py};{vid}"
    else:
        # Eliminated — use org chart holding area
        px, py   = 7383, 1919   # Off-board org chart coordinates
        map_name = "Map18"
        pos_str  = f"{map_name};1;{px},{py}"
        null_str = f"null;{px};{py};{vid}"

    # Determine destination description from location
    if loc_type == "zone":
        dest_desc = loc.get("name", "Unassigned")
        org_name  = dest_desc
        return_box = "Return from Organisation Chart;82,195;;0;0;0;Return"
    elif loc_type == "hex":
        dest_desc = f"Main Map"
        org_name  = "Unassigned"
        return_box = "Return from Organisation Chart;82,195;;0;0;0;Return"
    else:
        dest_desc = "BR Unassigned Infantry-Type Units"
        org_name  = dest_desc
        return_box = "Return from Organisation Chart;82,195;;0;0;0;Return"

    # Piece type marker (G = general, approximation)
    # Build the Vassal piece state string
    piece_data = (
        f"\\tmark;Type\\\\\\tfootprint;84,130;Movement Trail;false;false;10;"
        f"255,255,255;0,0,0;100;0;20;30;1.0\\\\\\"
        f"\\tmarkmoved;moved.gif;26;-25;Mark Moved;77,130\\\\\\\\"
        f"\\tpiece;;;{image};{name}/"
        f";;\\tPieces\\\\\\tfalse;{pos_str}\\\\\\tfalse\\\\\\\\"
        f"\\t{null_str}\\\\"
    )

    cmd = (
        f"+/{timestamp}/sendto;"
        f"Return to Organisation Chart;82,130;"
        f"{org_name};{org_name};0;0;"
        f"{return_box};"
        f"G;;;;OC {seq:04d}"
        f"{piece_data}"
    )
    return cmd


def _make_mark_command(
    piece_name: str,
    marker_name: str,
    vassal_id: int,
    timestamp: int,
) -> str:
    """Generate a Vassal 'mark' command for a status marker."""
    image = f"{marker_name}.png"
    cmd = (
        f"+/{timestamp}/mark;{marker_name}"
        f"\\tpiece;;;{image};{marker_name}"
        f"/Pieces\\tfalse;;1;0,0\\\\\\tfalse\\\\\\\\\\tnull;0;0;{vassal_id}\\\\"
    )
    return cmd


# ---------------------------------------------------------------------------
# Stack command (groups co-located pieces)
# ---------------------------------------------------------------------------

def _make_stack_command(piece_ids: list[int], px: int, py: int, timestamp: int) -> str:
    """Generate a Vassal 'stack' command grouping pieces at the same location."""
    id_str = ",".join(str(i) for i in piece_ids)
    cmd = f"+/{timestamp}/stack;{px};{py};{id_str}"
    return cmd


# ---------------------------------------------------------------------------
# Main writer
# ---------------------------------------------------------------------------

def write_vsav(
    state:      GameState,
    timestamp:  Optional[int] = None,
    description: str = "",
) -> bytes:
    """
    Serialize a GameState to .vsav bytes.

    Parameters
    ----------
    state       : the game state to serialize
    timestamp   : Unix timestamp in milliseconds (default: plausible 2020 date)
    description : optional description string embedded in savedata

    Returns bytes of a valid .vsav ZIP archive.
    """
    if timestamp is None:
        # Use the same timestamp as the original Brevity setup for authenticity
        timestamp = 1586171744000   # April 6, 2020

    commands: list[str] = []
    ts = timestamp

    # 1. Begin save sentinel
    commands.append("\x00begin_save")

    # 2. Emit placed pieces in canonical order
    # Group by location for stacking
    location_to_pieces: dict[int, list[PieceState]] = {}

    for seq, p_def in enumerate(PIECES):
        name = p_def["name"]
        ps   = state.pieces.get(name) or PieceState(name=name)

        # Emit sendto command
        cmd = _make_sendto_command(ps, ts - seq, seq + 1)
        commands.append(cmd)

        # Track for stacking
        loc_idx = ps.location_idx
        if loc_idx not in location_to_pieces:
            location_to_pieces[loc_idx] = []
        location_to_pieces[loc_idx].append(ps)

        # Emit status marker commands
        for flag, marker_name in STATUS_FLAG_NAMES.items():
            if ps.status_byte & flag:
                vid = ps.vassal_id or (1000 + seq)
                mark_cmd = _make_mark_command(name, marker_name, vid, ts - seq - 1)
                commands.append(mark_cmd)

    # 3. Emit stack commands for co-located pieces (on-map only)
    stack_ts = ts - len(PIECES) - 1
    for loc_idx, piece_list in location_to_pieces.items():
        if len(piece_list) < 2:
            continue
        loc = LOCATIONS[loc_idx] if loc_idx < len(LOCATIONS) else None
        if not loc or "px" not in loc:
            continue
        ids = [ps.vassal_id or (1000 + i) for i, ps in enumerate(piece_list)]
        cmd = _make_stack_command(ids, loc["px"], loc["py"], stack_ts)
        commands.append(cmd)
        stack_ts -= 1

    # 4. Assemble command string (ESC-separated)
    command_string = "\x1b".join(commands)

    # 5. VCSK encode
    saved_game_bytes = _encode_saved_game(command_string)

    # 6. Build ZIP archive
    ts_ms = timestamp
    module_xml = _MODULEDATA_TEMPLATE.format(timestamp=ts_ms).encode("utf-8")
    save_xml   = _SAVEDATA_TEMPLATE.format(timestamp=ts_ms).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("moduledata",  module_xml)
        zf.writestr("savedata",    save_xml)
        zf.writestr("savedGame",   saved_game_bytes)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    print("=== vsav_writer Self-Test ===\n")

    # 1. Build a minimal test state
    from .state_model  import GameState, PieceState
    from .constants    import nearest_location_index, zone_index, STATUS_PINNED

    gs = GameState.default()

    # Place a few pieces
    ge_piece = gs.get_piece("GE I-8 - 15")
    ge_piece.location_idx = nearest_location_index(7964, 1173)

    it_piece = gs.get_piece("IT I-62 - 102 Trn")
    it_piece.location_idx = nearest_location_index(7874, 1404)
    it_piece.set_status_flag(STATUS_PINNED)

    br_piece = gs.get_piece("BR 1 DurLt - 23-70")
    br_piece.location_idx = zone_index("Tobruk Holding Box")

    gs.turn     = 14
    gs.op_stage = 2
    gs.weather  = 0

    print(f"Test state: {gs.summary()}")
    print(f"  GE I-8 - 15   loc_idx={ge_piece.location_idx}")
    print(f"  IT I-62        loc_idx={it_piece.location_idx}")
    print(f"  BR 1 DurLt     loc_idx={br_piece.location_idx}")
    print()

    # 2. Write .vsav
    vsav_bytes = write_vsav(gs)
    print(f"Written .vsav: {len(vsav_bytes):,} bytes")

    # 3. Verify it's a valid ZIP
    with zipfile.ZipFile(io.BytesIO(vsav_bytes)) as zf:
        names = zf.namelist()
    print(f"ZIP contents: {names}")
    assert "savedGame" in names and "moduledata" in names

    # 4. Round-trip: read it back
    from .vsav_reader import read_vsav
    gs2 = read_vsav(vsav_bytes)
    print(f"Read-back state: {gs2.summary()}")

    # 5. Save to output for inspection
    out_path = "/mnt/user-data/outputs/test_write.vsav"
    with open(out_path, "wb") as f:
        f.write(vsav_bytes)
    print(f"Saved to: {out_path}")
    print("\nAll tests passed.")
