"""
cna_ssc/formats/vsav_codec.py

Read and write Vassal .vsav files for CNAv2.1.0.vmod.

FILE FORMAT
-----------
A .vsav is a ZIP archive containing three files:
    moduledata  – XML metadata (module name, version, timestamp)
    savedata    – XML metadata (save-level metadata)
    savedGame   – Obfuscated command stream (the actual game state)

The savedGame encoding:
    1. Content starts with the literal bytes b"!VCSK"
    2. The remainder is a hex-encoded byte string
    3. Each byte b in the hex-decoded data is XOR'd with 0x7D to recover
       the plaintext command stream
    4. The plaintext is a sequence of Vassal command strings delimited by
       the ESC character (0x1B = '\\x1b')
    5. Each command has the form:
           +/timestamp/type;param1;param2;...
       The full piece definition is embedded as tab-separated traits within
       sendto and mark commands.

READER
------
parse_vsav(path) → VassalSave

    Decodes the .vsav and returns a structured object with:
        - pieces: list of PiecePlacement (name, location, pixel coords, etc.)
        - raw_commands: original command strings (for round-trip fidelity)
        - metadata

WRITER
------
write_vsav(save: VassalSave, path, template_path=None)

    Encodes and writes a .vsav file.  If template_path is provided, piece
    definitions (the full trait strings) are taken from the template for
    pieces not explicitly defined in the save object.

    The writer modifies the pixel coordinates within existing sendto commands
    to move pieces to new positions.  This preserves all Vassal trait data
    that we don't model (layer states, label text, etc.).
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from experimental.engine.hex_grid import (
    location_pixel,
    location_id_from_pixel,
    OFF_MAP_ZONE_BY_NAME,
)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

XOR_KEY: int = 0x7D
VSAV_HEADER: bytes = b"!VCSK"
CMD_SEP: str = "\x1b"  # ESC character between commands

MODULE_NAME: str = "SPI Campaign for North Africa"
MODULE_VERSION: str = "2.1.0"
VASSAL_VERSION: str = "3.7.15"

# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PiecePlacement:
    """A single piece placed on the board (or off-map)."""
    name: str               # Vassal entryName / piece name
    gpid: str               # Vassal global piece ID
    location_id: str        # canonical location: "col,row" or zone name
    pixel_x: float          # Vassal pixel x
    pixel_y: float          # Vassal pixel y
    map_name: str           # "Main Map", zone name, or ""
    moved: bool = False     # whether the piece has been marked as moved
    cmd_index: int = -1     # index into raw_commands list (for in-place edit)
    raw_cmd: str = ""       # the original command string


@dataclass
class VassalSave:
    """Parsed representation of a .vsav file."""
    pieces: List[PiecePlacement] = field(default_factory=list)
    raw_commands: List[str] = field(default_factory=list)
    module_name: str = MODULE_NAME
    module_version: str = MODULE_VERSION
    vassal_version: str = VASSAL_VERSION
    date_saved: int = 0  # epoch milliseconds

    # Index: piece name → PiecePlacement (last occurrence wins for stacks)
    _piece_map: Dict[str, PiecePlacement] = field(
        default_factory=dict, repr=False
    )

    def get_location(self, piece_name: str) -> Optional[str]:
        p = self._piece_map.get(piece_name)
        return p.location_id if p else None

    def all_locations(self) -> Dict[str, str]:
        """Return {piece_name: location_id} for all pieces."""
        return {p.name: p.location_id for p in self.pieces}


# ──────────────────────────────────────────────────────────────────────────────
# Low-level codec helpers
# ──────────────────────────────────────────────────────────────────────────────

def _xor_decode(hex_str: str) -> str:
    """Hex-decode and XOR with 0x7D → plaintext command stream."""
    raw = bytes.fromhex(hex_str)
    decoded = bytes([b ^ XOR_KEY for b in raw])
    return decoded.decode("latin-1")


def _xor_encode(text: str) -> str:
    """XOR with 0x7D and hex-encode → savedGame content (without !VCSK)."""
    raw = text.encode("latin-1")
    obfuscated = bytes([b ^ XOR_KEY for b in raw])
    return obfuscated.hex()


def _read_saved_game_from_zip(vsav_path: str) -> Tuple[str, str, str]:
    """Return (moduledata_xml, savedata_xml, command_stream_plaintext)."""
    with zipfile.ZipFile(vsav_path, "r") as zf:
        moduledata = zf.read("moduledata").decode("utf-8")
        savedata   = zf.read("savedata").decode("utf-8")
        sg_raw     = zf.read("savedGame").decode("ascii")

    if not sg_raw.startswith("!VCSK"):
        raise ValueError("savedGame does not begin with !VCSK header")

    plaintext = _xor_decode(sg_raw[5:])
    return moduledata, savedata, plaintext


def _write_saved_game_to_zip(
    vsav_path: str,
    commands: List[str],
    module_name: str,
    module_version: str,
    vassal_version: str,
) -> None:
    """Encode commands and write to .vsav ZIP archive."""
    now_ms = int(time.time() * 1000)

    # Build command stream: null byte + begin_save + ESC-joined commands
    stream = "\x00begin_save" + CMD_SEP + CMD_SEP.join(commands)
    encoded = VSAV_HEADER.decode("ascii") + _xor_encode(stream)

    moduledata = (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        '<data version="1">\r\n'
        f'  <version>{module_version}</version>\r\n'
        f'  <VassalVersion>{vassal_version}</VassalVersion>\r\n'
        f'  <dateSaved>{now_ms}</dateSaved>\r\n'
        f'  <description>(c) 1979 SPI</description>\r\n'
        f'  <n>{module_name}</n>\r\n'
        '</data>\r\n'
    )
    savedata = (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        '<data version="1">\r\n'
        f'  <version></version>\r\n'
        f'  <VassalVersion>{vassal_version}</VassalVersion>\r\n'
        f'  <dateSaved>{now_ms}</dateSaved>\r\n'
        '  <description></description>\r\n'
        '</data>\r\n'
    )

    with zipfile.ZipFile(vsav_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("moduledata", moduledata.encode("utf-8"))
        zf.writestr("savedata",   savedata.encode("utf-8"))
        zf.writestr("savedGame",  encoded.encode("ascii"))


# ──────────────────────────────────────────────────────────────────────────────
# Parser: command stream → VassalSave
# ──────────────────────────────────────────────────────────────────────────────

# Patterns for extracting piece data from commands
_RE_PIECE_NAME = re.compile(r'piece;;;([^;]+\.(?:svg|png));([^/\t]+)', re.IGNORECASE)
_RE_MAP_COORD  = re.compile(r'(Main Map|[A-Z][a-z][^;]*?);(\d+);(\d+);(\d+)\\')
_RE_TIMESTAMP  = re.compile(r'^\+/(\d+)/(\w+)')
_RE_GPID_END   = re.compile(r';(\d+)\\\\$')
_RE_MOVED      = re.compile(r'markmoved')


def _parse_command(cmd: str, cmd_index: int) -> Optional[PiecePlacement]:
    """Attempt to extract a PiecePlacement from a command string.

    Returns None if the command does not represent a piece placement.
    """
    if not cmd.startswith("+/"):
        return None

    m_ts = _RE_TIMESTAMP.match(cmd)
    if not m_ts:
        return None
    cmd_type = m_ts.group(2)  # 'sendto', 'mark', 'stack', etc.

    # We only parse commands that place pieces
    if cmd_type not in ("sendto", "mark"):
        return None

    # Extract piece name from image reference
    m_piece = _RE_PIECE_NAME.search(cmd)
    if not m_piece:
        return None
    image_file = m_piece.group(1)
    # Piece name is in the path after '/'
    raw_path   = m_piece.group(2)
    # entryName is before '//' in the path (e.g. "GE I-8 - 15/")
    piece_name = raw_path.rstrip("/").split("/")[0].strip()
    if not piece_name:
        piece_name = image_file.rsplit(".", 1)[0]

    # Extract map name and pixel coordinates
    # Pattern: MapName;pixel_x;pixel_y;gpid\
    map_name = ""
    px, py = 0.0, 0.0
    gpid = ""

    # Try to find "Main Map;x;y;id" or "ZoneName;x;y;id"
    m_coord = re.search(r'(Main Map);(\d+);(\d+);(\d+)', cmd)
    if m_coord:
        map_name = m_coord.group(1)
        px = float(m_coord.group(2))
        py = float(m_coord.group(3))
        gpid = m_coord.group(4)
    else:
        # Try null;x;y;id (off-map pieces)
        m_null = re.search(r'null;(\d+);(\d+);(\d+)', cmd)
        if m_null:
            px = float(m_null.group(1))
            py = float(m_null.group(2))
            gpid = m_null.group(3)

    # Determine location ID
    # Check if the sendto destination is a named zone
    zone_dest = ""
    if cmd_type == "sendto":
        # sendto;dest_name;... — extract the destination
        parts = cmd.split(";", 5)
        if len(parts) > 3:
            zone_dest = parts[3].strip()

    location_id = location_id_from_pixel(px, py, zone_hint=zone_dest)

    moved = bool(_RE_MOVED.search(cmd))

    return PiecePlacement(
        name=piece_name,
        gpid=gpid,
        location_id=location_id,
        pixel_x=px,
        pixel_y=py,
        map_name=map_name if map_name else zone_dest,
        moved=moved,
        cmd_index=cmd_index,
        raw_cmd=cmd,
    )


def parse_vsav(vsav_path: str) -> VassalSave:
    """Read a .vsav file and return a VassalSave object."""
    moduledata, savedata, plaintext = _read_saved_game_from_zip(vsav_path)

    # Split on ESC
    raw_commands = plaintext.split(CMD_SEP)

    save = VassalSave(
        raw_commands=raw_commands,
        module_name=MODULE_NAME,
    )

    # Parse each command
    for i, cmd in enumerate(raw_commands):
        placement = _parse_command(cmd, i)
        if placement is not None:
            save.pieces.append(placement)
            save._piece_map[placement.name] = placement

    return save


# ──────────────────────────────────────────────────────────────────────────────
# Writer: VassalSave → .vsav
# ──────────────────────────────────────────────────────────────────────────────

_RE_MAIN_MAP_COORD = re.compile(
    r'(Main Map);(\d+);(\d+);(\d+)'
)
_RE_NULL_COORD = re.compile(
    r'null;(\d+);(\d+);(\d+)'
)


def _update_coords_in_cmd(cmd: str, new_px: int, new_py: int, map_name: str) -> str:
    """Replace pixel coordinates in a command string with new values.

    Tries both Main Map and null coordinate patterns; applies whichever
    matches.  The null;x;y pattern is used for pieces in off-map zones.
    """
    # Try Main Map first
    if "Main Map;" in cmd:
        def replace_main(m):
            return f"Main Map;{new_px};{new_py};{m.group(4)}"
        updated = _RE_MAIN_MAP_COORD.sub(replace_main, cmd)
        if updated != cmd:
            return updated

    # Fall back to null;x;y (off-map / zone pieces)
    if "null;" in cmd:
        def replace_null(m):
            return f"null;{new_px};{new_py};{m.group(3)}"
        return _RE_NULL_COORD.sub(replace_null, cmd)

    return cmd  # nothing to update


def write_vsav(
    save: VassalSave,
    output_path: str,
    location_overrides: Optional[Dict[str, str]] = None,
) -> None:
    """Write a .vsav file from a VassalSave object.

    Parameters
    ----------
    save : VassalSave
        The save state (containing original raw_commands for round-trip).
    output_path : str
        Where to write the new .vsav.
    location_overrides : dict, optional
        {piece_name: new_location_id} — pieces to move to new positions.
        If None, the save is written as-is (pure round-trip).

    Notes
    -----
    Some piece names appear in multiple commands (e.g., a piece on the main map
    AND a copy in an Org Chart panel).  ALL matching commands are updated so
    every copy of the piece reflects the new location.
    """
    overrides = location_overrides or {}
    commands  = list(save.raw_commands)  # copy

    # Pre-compute pixel coords for each overridden location
    coord_cache: Dict[str, tuple] = {}
    for piece_name, new_loc in overrides.items():
        px_f, py_f = location_pixel(new_loc)
        coord_cache[piece_name] = (round(px_f), round(py_f))

    # Update ALL piece commands (handles duplicate piece names)
    for piece in save.pieces:
        update = coord_cache.get(piece.name)
        if update is None:
            continue
        new_px, new_py = update
        if 0 <= piece.cmd_index < len(commands):
            updated = _update_coords_in_cmd(
                commands[piece.cmd_index], new_px, new_py, piece.map_name
            )
            commands[piece.cmd_index] = updated

    # Drop the 'begin_save\x1b' prefix that will be re-added
    if commands and commands[0].startswith("\x00begin_save"):
        commands = commands[1:]

    _write_saved_game_to_zip(
        output_path,
        commands,
        save.module_name,
        save.module_version or MODULE_VERSION,
        save.vassal_version or VASSAL_VERSION,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Convenience: extract location dict from .vsav
# ──────────────────────────────────────────────────────────────────────────────

def vsav_to_locations(vsav_path: str) -> Dict[str, str]:
    """Return {piece_name: location_id} from a .vsav file."""
    save = parse_vsav(vsav_path)
    return save.all_locations()


def locations_to_vsav(
    locations: Dict[str, str],
    template_vsav: str,
    output_path: str,
) -> None:
    """Write a new .vsav by applying location_overrides to a template .vsav.

    Parameters
    ----------
    locations : dict
        {piece_name: location_id} — full desired state.
    template_vsav : str
        Path to a .vsav to use as the structural template.
    output_path : str
        Where to write the new .vsav.
    """
    save = parse_vsav(template_vsav)
    write_vsav(save, output_path, location_overrides=locations)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python vsav_codec.py <file.vsav>")
        sys.exit(1)

    vsav_path = sys.argv[1]
    save = parse_vsav(vsav_path)
    print(f"Parsed {len(save.pieces)} pieces from {vsav_path}")
    print(f"Commands: {len(save.raw_commands)}")
    print()
    print("First 10 piece placements:")
    for p in save.pieces[:10]:
        print(f"  {p.name:35s}  {p.location_id:25s}  px={p.pixel_x:.0f} py={p.pixel_y:.0f}")
