"""
cna_ssc/formats/buildfile_parser.py

Parse CNAv2.1.0.vmod's buildFile.xml and generate the piece registry data
file used by piece_registry.py.

Usage:
    python -m experimental.formats.buildfile_parser buildFile.xml

This writes:
    cna_ssc/engine/piece_registry_data.json

The JSON contains the canonical ordered list of all pieces with their
metadata.  The ORDER is fixed at generation time and must not change.

Canonical ordering:
    1. Markers (m-prefix) — game track, initiative, weather
    2. Ground units — by nation: GE, IT, BR, IN, AU, SA, NZ
    3. Aircraft — GE, IT, AL
    4. Naval / airfield counters — AL, AX
    5. Trackers, labels, other
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Category inference
# ──────────────────────────────────────────────────────────────────────────────

NATION_PREFIXES = {"GE", "IT", "BR", "IN", "AU", "SA", "NZ"}

def _infer_category(name: str, data: str, proto: str) -> str:
    if proto in ("Markers", "MobileMarkers") or name.startswith("m"):
        return "marker"
    if "- Air -" in name or proto == "AirPlanes":
        return "aircraft"
    if name[:2] in NATION_PREFIXES:
        return "ground_unit"
    if proto in ("Royal Navy", "Coastal Shipping"):
        return "airfield_naval"
    if name.startswith(("AL", "AX")):
        if any(k in name for k in ("Airstrip", "Airfield", "Alighting", "Basin", "Navy")):
            return "airfield_naval"
        return "airfield_naval"
    if "Tracker" in name or "Resource" in name:
        return "tracker"
    if "Weather" in name:
        return "weather"
    if "User-Label" in name or "Abandoned" in name or "Tim" in name or "Clay" in name:
        return "label"
    return "unknown"


def _infer_prototype(data: str) -> str:
    for pname in [
        "AirPlanes", "AlliedPiece", "AxisPiece", "Royal Navy",
        "Coastal Shipping", "Markers", "MobileMarkers",
        "Place Names", "Common Properties", "Properties - Units",
    ]:
        if f"prototype;{pname}" in data:
            return pname
    return ""


def _extract_image(data: str) -> str:
    m = re.search(r'piece;;;([^;]+?\.(?:svg|png))', data, re.IGNORECASE)
    return m.group(1) if m else ""


# ──────────────────────────────────────────────────────────────────────────────
# Nation / sort key for canonical ordering
# ──────────────────────────────────────────────────────────────────────────────

CAT_ORDER = {
    "marker": 0,
    "ground_unit": 1,
    "aircraft": 2,
    "airfield_naval": 3,
    "tracker": 4,
    "weather": 5,
    "label": 6,
    "unknown": 7,
}

NATION_ORDER = {
    "GE": 0, "IT": 1, "BR": 2,
    "IN": 3, "AU": 4, "SA": 5, "NZ": 6,
    "AL": 7, "AX": 8,
}

def _sort_key(piece: Dict) -> tuple:
    cat_ord = CAT_ORDER.get(piece["cat"], 99)
    nation = piece["name"][:2]
    nation_ord = NATION_ORDER.get(nation, 50)
    return (cat_ord, nation_ord, piece["name"])


# ──────────────────────────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────────────────────────

def parse_buildfile(buildfile_path: str) -> List[Dict]:
    """Parse buildFile.xml and return ordered piece list."""
    tree = ET.parse(buildfile_path)
    root = tree.getroot()

    slots = root.findall(".//VASSAL.build.widget.PieceSlot")
    pieces = []
    seen_names: Dict[str, int] = {}

    for s in slots:
        name = s.get("entryName", "").strip()
        gpid = s.get("gpid", "")
        data = (s.text or "").strip()

        if not name:
            continue

        proto = _infer_prototype(data)
        cat   = _infer_category(name, data, proto)
        image = _extract_image(data)

        # Deduplicate: if we've seen this name, append a counter
        base_name = name
        if name in seen_names:
            seen_names[name] += 1
            name = f"{base_name}#{seen_names[base_name]}"
        else:
            seen_names[base_name] = 0

        pieces.append({
            "name":  name,
            "gpid":  gpid,
            "image": image,
            "cat":   cat,
            "proto": proto,
        })

    # Apply canonical ordering
    pieces.sort(key=_sort_key)
    return pieces


def write_registry(pieces: List[Dict], output_path: str) -> None:
    """Write piece list to JSON file."""
    with open(output_path, "w") as f:
        json.dump(pieces, f, indent=2)
    print(f"Wrote {len(pieces)} pieces to {output_path}")


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: python -m experimental.formats.buildfile_parser <buildFile.xml> "
            "[output.json]"
        )
        sys.exit(1)

    buildfile_path = sys.argv[1]
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "..", "engine", "piece_registry_data.json")
    output_path  = sys.argv[2] if len(sys.argv) > 2 else default_out
    output_path  = os.path.normpath(output_path)

    print(f"Parsing {buildfile_path}...")
    pieces = parse_buildfile(buildfile_path)

    from collections import Counter
    cats = Counter(p["cat"] for p in pieces)
    print(f"Found {len(pieces)} pieces:")
    for cat, count in sorted(cats.items(), key=lambda x: x[1], reverse=True):
        print(f"  {cat:20s}: {count}")

    write_registry(pieces, output_path)
    print(f"\nPiece registry written to {output_path}")
    print("Now import experimental.engine.piece_registry to use it.")


if __name__ == "__main__":
    main()
