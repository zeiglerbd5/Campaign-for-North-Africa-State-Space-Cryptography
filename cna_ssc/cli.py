"""
cna_ssc/cli.py
==============
Command-line interface for CNA-SSC.

Usage:
    python -m cna_ssc img-encode --message MSG --key KEY [--out OUT.png]
    python -m cna_ssc img-decode --in IN.png --key KEY

    # Or use the shortcut scripts:
    ./encode --message "secret" --key "passphrase"
    ./decode --in cna_board.png --key "passphrase"

Run with no arguments for an interactive prompt.
"""

from __future__ import annotations
import argparse
import os
import sys


def cmd_img_encode(args: argparse.Namespace) -> None:
    """Encode a message into a CNA board image (PNG)."""
    from .image_steg import encode_message

    out = args.out or "cna_board.png"
    print(f"Encoding {len(args.message)}-char message into board image...")
    png_data = encode_message(args.message, args.key, n_noise=args.noise, output_path=out)
    print(f"Saved: {out} ({len(png_data) // 1024} KB)")
    print("Share this image — it looks like a normal CNA game state.")


def cmd_img_decode(args: argparse.Namespace) -> None:
    """Decode a message from a CNA board image (PNG)."""
    from .image_steg import decode_message

    with open(args.input, "rb") as f:
        png_data = f.read()

    print(f"Reading board image ({len(png_data) // 1024} KB)...")
    message = decode_message(png_data, args.key, threshold=args.threshold)
    print(f"Decoded message: {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cna_ssc",
        description="CNA-SSC: Steganographic messaging hidden in CNA board images.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ie = sub.add_parser("img-encode", help="Encode a message into a CNA board image (PNG)")
    ie.add_argument("--message", required=True, help="Plaintext message (max 50 chars)")
    ie.add_argument("--out", help="Output PNG path (default: cna_board.png)")
    ie.add_argument("--noise", type=int, default=80,
                    help="Number of decoy pieces to place (default: 80)")
    ie.add_argument("--key", default="",
                    help="Optional extra passphrase (both sides must match)")

    id_ = sub.add_parser("img-decode", help="Decode a message from a CNA board image (PNG)")
    id_.add_argument("--in", dest="input", required=True, help="Input PNG image path")
    id_.add_argument("--threshold", type=float, default=0.75,
                     help="Template matching confidence threshold (default: 0.75)")
    id_.add_argument("--key", default="",
                     help="Optional extra passphrase (must match encoder)")

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

    if choice in ("e", "encode"):
        message = input("Enter message to encode (max 50 chars): ").strip()
        if not message:
            print("No message entered. Exiting.")
            return
        if len(message.encode("utf-8")) > 50:
            print(f"Message too long ({len(message.encode('utf-8'))} bytes). Max is 50.")
            return

        key = input("Passphrase (leave blank for none): ").strip()
        out_path = input("Output file [cna_board.png]: ").strip() or "cna_board.png"
        print("\nEncoding...")

        from .image_steg import encode_message
        png_data = encode_message(message, key=key, n_noise=80, output_path=out_path)

        print(f"\nDone! Board image saved to: {out_path}")
        print(f"File size: {len(png_data) // 1024} KB")
        print("Send this image to your recipient.")

    elif choice in ("d", "decode"):
        in_path = input("Enter path to board image: ").strip()
        if not in_path:
            print("No path entered. Exiting.")
            return
        if not os.path.isfile(in_path):
            print(f"File not found: {in_path}")
            return

        key = input("Passphrase (leave blank for none): ").strip()
        print("\nDecoding...")

        from .image_steg import decode_message
        with open(in_path, "rb") as f:
            png_data = f.read()
        message = decode_message(png_data, key=key)
        print(f"\nDecoded message: {message}")

    elif choice in ("q", "quit"):
        return

    else:
        print(f"Unknown option: {choice!r}")


def main() -> None:
    # If no arguments given, run interactive mode
    if len(sys.argv) <= 1:
        interactive_mode()
        return

    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "img-encode": cmd_img_encode,
        "img-decode": cmd_img_decode,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
