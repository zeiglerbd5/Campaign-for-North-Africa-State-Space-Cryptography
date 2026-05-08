#!/bin/bash
# CNA-SSC Installer
# Run this after unzipping the package: ./install.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== CNA-SSC Installer ==="
echo ""

# Check Python version
python3 -c "import sys; assert sys.version_info >= (3,9), 'Python 3.9+ required'" 2>/dev/null || {
    echo "ERROR: Python 3.9 or higher is required."
    echo "Install it from https://www.python.org/downloads/"
    exit 1
}

# Install system dependencies:
#   cairo   — required by CairoSVG (renders counter art)
#   poppler — provides pdftoppm (renders the map PDF)
if command -v apt-get &> /dev/null; then
    echo "Installing system dependencies (libcairo2, poppler-utils)..."
    sudo apt-get install -y libcairo2-dev poppler-utils > /dev/null 2>&1 || true
elif command -v brew &> /dev/null; then
    echo "Installing system dependencies (cairo, poppler)..."
    brew install cairo poppler > /dev/null 2>&1 || true
fi

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install "Pillow>=10.0" "opencv-python-headless>=4.8,<4.11" "numpy>=1.24,<2" "cairosvg>=2.7" --quiet

# Create convenience scripts
cat > "$SCRIPT_DIR/encode" << 'ENCODE_EOF'
#!/bin/bash
cd "$(dirname "$0")"
python3 -m cna_ssc img-encode "$@"
ENCODE_EOF
chmod +x "$SCRIPT_DIR/encode"

cat > "$SCRIPT_DIR/decode" << 'DECODE_EOF'
#!/bin/bash
cd "$(dirname "$0")"
python3 -m cna_ssc img-decode "$@"
DECODE_EOF
chmod +x "$SCRIPT_DIR/decode"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Usage (run from this directory):"
echo "  Encode:  python3 -m cna_ssc img-encode --message \"your secret message\""
echo "  Decode:  python3 -m cna_ssc img-decode --in cna_board.png"
echo ""
echo "Or use the shortcuts:"
echo "  ./encode --message \"your secret message\""
echo "  ./decode --in cna_board.png"
echo ""
echo "The output image (cna_board.png) looks like a normal CNA game board."
echo "Send it to your recipient — they run ./decode on their copy."
