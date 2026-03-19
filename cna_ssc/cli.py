"""
cna_ssc/cli.py
==============
Command-line interface for CNA-SSC.

Usage:
    python -m cna_ssc img-encode --key KEY --message MSG [--out OUT.png]
    python -m cna_ssc img-decode --key KEY --in  IN.png

    python -m cna_ssc encode  --key KEY --message MSG  --out OUT.vsav
    python -m cna_ssc decode  --key KEY --in  IN.vsav  [--length N]
    python -m cna_ssc inspect --in  IN.vsav
    python -m cna_ssc export  --in  IN.vsav  --out OUT.png
    python -m cna_ssc demo

Key formats:
    --key "my secret passphrase"       (UTF-8 string, HKDF-stretched)
    --key hex:0123456789abcdef...      (raw hex bytes)
    --key file:/path/to/keyfile        (read key from file)

Examples:
    # Encode a message
    python -m cna_ssc.cli encode \\
        --key "operation torch commences at dawn" \\
        --message "Proceed to Tobruk immediately. Destroy supply depot." \\
        --out message.vsav

    # Decode
    python -m cna_ssc.cli decode \\
        --key "operation torch commences at dawn" \\
        --in message.vsav

    # Inspect a .vsav file (no key needed)
    python -m cna_ssc.cli inspect --in Operation_Brevity.vsav

    # Export schematic PNG
    python -m cna_ssc.cli export --in message.vsav --out board.png

    # Run full demo
    python -m cna_ssc.cli demo
"""

from __future__ import annotations
import argparse
import hashlib
import os
import sys
import textwrap


# ---------------------------------------------------------------------------
# Key parsing
# ---------------------------------------------------------------------------

def parse_key(key_str: str) -> bytes:
    """
    Parse key from string:
        "passphrase"        -> SHA-256 of UTF-8 passphrase (32 bytes)
        "hex:0123abcd..."   -> raw hex bytes
        "file:/path/..."    -> contents of file
    """
    if key_str.startswith("hex:"):
        return bytes.fromhex(key_str[4:])
    elif key_str.startswith("file:"):
        path = key_str[5:]
        with open(path, "rb") as f:
            return f.read()
    else:
        # Passphrase: stretch with SHA-256 to get 32-byte key
        return hashlib.sha256(key_str.encode("utf-8")).digest()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_encode(args: argparse.Namespace) -> None:
    from .encoder import encode_to_file, encode

    key     = parse_key(args.key)
    message = args.message.encode("utf-8") if args.message else sys.stdin.buffer.read()

    salt = os.urandom(16) if not args.no_salt else b""
    approach = args.approach

    print(f"Encoding {len(message)} bytes with approach={approach!r}...")

    if args.out:
        encode_to_file(message, key, args.out, approach=approach, salt=salt)
        size = os.path.getsize(args.out)
        print(f"Written to: {args.out} ({size:,} bytes)")
    else:
        vsav = encode(message, key, approach=approach, salt=salt)
        sys.stdout.buffer.write(vsav)


def cmd_decode(args: argparse.Namespace) -> None:
    from .decoder import decode_from_file, decode

    key      = parse_key(args.key)
    length   = args.length or -1
    approach = args.approach

    if args.input:
        message = decode_from_file(args.input, key, approach=approach, length=length)
    else:
        vsav = sys.stdin.buffer.read()
        message = decode(vsav, key, approach=approach, length=length)

    if args.output:
        with open(args.output, "wb") as f:
            f.write(message)
        print(f"Decoded message written to: {args.output} ({len(message)} bytes)")
    else:
        try:
            sys.stdout.write(message.decode("utf-8"))
            sys.stdout.write("\n")
        except UnicodeDecodeError:
            sys.stdout.buffer.write(message)


def cmd_inspect(args: argparse.Namespace) -> None:
    from .vsav_reader import parse_vsav_raw, read_vsav
    from collections  import Counter

    in_path = args.input
    with open(in_path, "rb") as f:
        vsav_bytes = f.read()

    print(f"\n{'='*60}")
    print(f"File: {in_path}  ({len(vsav_bytes):,} bytes)")
    print(f"{'='*60}\n")

    # Parse commands
    commands, raw = parse_vsav_raw(vsav_bytes)
    types = Counter(c["type"] for c in commands)
    print(f"Commands: {len(commands)} total")
    for t, n in types.most_common():
        print(f"  {t:<20s} {n:4d}")
    print()

    # Parse full state
    gs = read_vsav(vsav_bytes)
    print(f"Game state: turn={gs.turn}, op_stage={gs.op_stage}, weather={gs.weather}")
    print()

    # Pieces on map
    on_map = gs.pieces_on_map()
    print(f"Pieces on main map: {len(on_map)}")
    for ps in sorted(on_map, key=lambda p: p.name)[:20]:
        loc  = ps.location
        flags = ps.active_status_markers()
        flags_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {ps.name:<45s} hex({loc['col']:3d},{loc['row']:2d}){flags_str}")
    if len(on_map) > 20:
        print(f"  ... and {len(on_map)-20} more")
    print()

    # Pieces in zones
    from .constants import OFFMAP_ZONES
    for zone_name, _, _ in OFFMAP_ZONES:
        in_zone = gs.pieces_in_zone(zone_name)
        if in_zone:
            print(f"  {zone_name}: {len(in_zone)} pieces")
            for ps in in_zone[:5]:
                print(f"    {ps.name}")
            if len(in_zone) > 5:
                print(f"    ... and {len(in_zone)-5} more")


def cmd_export(args: argparse.Namespace) -> None:
    from .vsav_reader  import read_vsav
    from .image_export import export_png_metadata, export_schematic

    in_path  = args.input
    out_path = args.out

    with open(in_path, "rb") as f:
        vsav_bytes = f.read()

    gs = read_vsav(vsav_bytes)
    print(f"Loaded: {gs.summary()}")

    if args.metadata_only:
        png = export_schematic(gs)
    else:
        png = export_png_metadata(gs)

    with open(out_path, "wb") as f:
        f.write(png)
    print(f"Exported PNG ({len(png):,} bytes) to: {out_path}")


def cmd_img_encode(args: argparse.Namespace) -> None:
    """Encode a message into a CNA board image (PNG)."""
    from .image_steg import encode_message

    key = args.key
    message = args.message

    out = args.out or "cna_board.png"
    noise = args.noise

    print(f"Encoding {len(message)}-char message into board image...")
    png_data = encode_message(message, key, n_noise=noise, output_path=out)
    print(f"Saved: {out} ({len(png_data) // 1024} KB)")
    print("Share this image — it looks like a normal CNA game state.")


def cmd_img_decode(args: argparse.Namespace) -> None:
    """Decode a message from a CNA board image (PNG)."""
    from .image_steg import decode_message

    key = args.key
    in_path = args.input

    with open(in_path, "rb") as f:
        png_data = f.read()

    print(f"Reading board image ({len(png_data) // 1024} KB)...")
    message = decode_message(png_data, key, threshold=args.threshold)
    print(f"Decoded message: {message}")


def cmd_demo(args: argparse.Namespace) -> None:
    """Run a full encode -> decode demo."""
    from .encoder import encode
    from .decoder import decode

    print("\n" + "="*60)
    print(" CNA-SSC Demo: State Space Steganography")
    print("="*60 + "\n")

    key = b"OperationCompassKeyMaterial_1940"
    messages = [
        b"8th Army advances at 0300. Objective: Bardia.",
        b"Supply convoy delayed. Fuel critical at Tobruk.",
        b"Air cover requested grid B3520. Enemy armor sighted.",
    ]

    for msg in messages:
        print(f"Original:  {msg.decode()!r}")

        # Encode
        vsav_bytes = encode(msg, key)
        print(f"Encoded:   {len(vsav_bytes):,} byte .vsav file")

        # Decode
        recovered = decode(vsav_bytes, key, length=len(msg))
        print(f"Recovered: {recovered.decode(errors='replace')!r}")
        match = "✓ MATCH" if recovered == msg else "✗ MISMATCH"
        print(f"Result:    {match}")
        print()

    # State space summary
    print("-"*60)
    print("State Space Summary:")
    from .constants import PIECES, get_radices, RADICES_META
    import math
    log10 = sum(
        math.log10(math.prod(get_radices(p["type"])))
        for p in PIECES
    ) + math.log10(math.prod(RADICES_META))
    bits = log10 * math.log2(10)
    print(f"  Pieces:      {len(PIECES)}")
    print(f"  Locations:   4,979 (4,959 hex + 19 zones + 1 eliminated)")
    print(f"  State space: ~10^{log10:.0f}")
    print(f"  Capacity:    ~{bits:.0f} bits  (~{int(bits/8):,} bytes per .vsav)")
    print(f"  Go game:     10^170  (CNA exceeds by {log10-170:.0f} orders of magnitude)")
    print(f"  Quantum resistance: Grover's algorithm -> 10^{log10/2:.0f} operations")
    print(f"  Universe ops:       ~10^140")
    print(f"  Gap:                10^{log10/2 - 140:.0f} orders of magnitude")
    print()
    print("Security: physical impossibility, not computational hardness.")
    print("="*60)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cna_ssc.cli",
        description=textwrap.dedent("""\
            CNA-SSC: Campaign for North Africa State Space Cryptography
            Steganographic encoding using the CNA Vassal module's 10^8528 state space.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ---- encode ----
    enc = sub.add_parser("encode", help="Encode a message into a .vsav file")
    enc.add_argument("--key",      required=True, help="Encryption key (passphrase, hex:..., or file:...)")
    enc.add_argument("--message",  help="Plaintext message (or pipe via stdin)")
    enc.add_argument("--out",      help="Output .vsav file path (or stdout)")
    enc.add_argument("--approach", choices=["hash", "positional", "constraint_aware"],
                     default="hash", help="Encoding approach (default: hash)")
    enc.add_argument("--no-salt",  action="store_true", help="Disable random salt (for testing)")

    # ---- decode ----
    dec = sub.add_parser("decode", help="Decode a message from a .vsav file")
    dec.add_argument("--key",      required=True, help="Encryption key")
    dec.add_argument("--in",       dest="input",  help="Input .vsav file path (or stdin)")
    dec.add_argument("--out",      dest="output", help="Output file for plaintext (or stdout)")
    dec.add_argument("--length",   type=int, default=-1, help="Expected plaintext length")
    dec.add_argument("--approach", choices=["hash", "positional", "constraint_aware"],
                     default="hash", help="Encoding approach (must match encode)")

    # ---- inspect ----
    ins = sub.add_parser("inspect", help="Inspect a .vsav file (no key required)")
    ins.add_argument("--in", dest="input", required=True, help="Input .vsav file path")

    # ---- export ----
    exp = sub.add_parser("export", help="Export .vsav state as PNG image")
    exp.add_argument("--in",            dest="input",  required=True, help="Input .vsav path")
    exp.add_argument("--out",           required=True, help="Output .png path")
    exp.add_argument("--metadata-only", action="store_true",
                     help="Embed state only (no schematic rendering)")

    # ---- img-encode ----
    ie = sub.add_parser("img-encode", help="Encode a message into a CNA board image (PNG)")
    ie.add_argument("--message", required=True, help="Plaintext message (max 50 chars)")
    ie.add_argument("--out", help="Output PNG path (default: cna_board.png)")
    ie.add_argument("--noise", type=int, default=80,
                    help="Number of decoy pieces to place (default: 80)")
    ie.add_argument("--key", default="",
                    help="Optional extra passphrase (both sides must match)")

    # ---- img-decode ----
    id_ = sub.add_parser("img-decode", help="Decode a message from a CNA board image (PNG)")
    id_.add_argument("--in", dest="input", required=True, help="Input PNG image path")
    id_.add_argument("--threshold", type=float, default=0.75,
                     help="Template matching confidence threshold (default: 0.75)")
    id_.add_argument("--key", default="",
                     help="Optional extra passphrase (must match encoder)")

    # ---- demo ----
    sub.add_parser("demo", help="Run a full encode/decode demonstration")

    return parser


def interactive_mode() -> None:
    """Run the interactive prompt-based interface."""
    print()
    print("==========================================")
    print("  CNA-SSC: Steganographic Messenger")
    print("==========================================")
    print()
    print("  [E] Encode a message into a board image")
    print("  [D] Decode a message from a board image")
    print("  [Q] Quit")
    print()

    choice = input("Select an option: ").strip().lower()

    if choice in ('e', 'encode'):
        print()
        message = input("Enter message to encode (max 50 chars): ").strip()
        if not message:
            print("No message entered. Exiting.")
            return
        if len(message.encode('utf-8')) > 50:
            print(f"Message too long ({len(message.encode('utf-8'))} bytes). Max is 50.")
            return

        key = input("Passphrase (leave blank for none): ").strip()
        out_path = input("Output file [cna_board.png]: ").strip() or "cna_board.png"
        print()
        print("Encoding...")

        from .image_steg import encode_message
        png_data = encode_message(message, key=key, n_noise=80, output_path=out_path)

        print()
        print(f"Done! Board image saved to: {out_path}")
        print(f"File size: {len(png_data) // 1024} KB")
        print("Send this image to your recipient.")

    elif choice in ('d', 'decode'):
        print()
        in_path = input("Enter path to board image: ").strip()
        if not in_path:
            print("No path entered. Exiting.")
            return

        if not os.path.isfile(in_path):
            print(f"File not found: {in_path}")
            return

        key = input("Passphrase (leave blank for none): ").strip()
        print()
        print("Decoding...")

        from .image_steg import decode_message
        with open(in_path, "rb") as f:
            png_data = f.read()
        message = decode_message(png_data, key=key)

        print()
        print(f"Decoded message: {message}")

    elif choice in ('q', 'quit'):
        return

    else:
        print(f"Unknown option: {choice!r}")


def main() -> None:
    # If no arguments given, run interactive mode
    if len(sys.argv) <= 1:
        interactive_mode()
        return

    parser = build_parser()
    args   = parser.parse_args()

    dispatch = {
        "encode":     cmd_encode,
        "decode":     cmd_decode,
        "inspect":    cmd_inspect,
        "export":     cmd_export,
        "img-encode": cmd_img_encode,
        "img-decode": cmd_img_decode,
        "demo":       cmd_demo,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
