#!/bin/bash
# Rebuild script for CBP 7501 Flask Application

set -e  # Exit on error

echo "═══════════════════════════════════════════════════════════════════════════════"
echo "🔧 REBUILDING CBP 7501 FLASK APPLICATION"
echo "═══════════════════════════════════════════════════════════════════════════════"
echo ""

cd "$(dirname "$0")"

# Step 1: Kill any running processes
echo "1️⃣  Stopping any running processes..."
lsof -ti:5002 | xargs kill -9 2>/dev/null || echo "   ✅ No processes on port 5002"
pkill -f "app_v3.5.10.py" 2>/dev/null || echo "   ✅ No app processes found"
sleep 2

# Step 2: Remove old venv backups and existing venv
echo ""
echo "2️⃣  Removing old virtual environments..."
rm -rf venv.backup.* 2>/dev/null || true
if [ -d "venv" ]; then
    rm -rf venv
    echo "   ✅ Removed existing venv"
else
    echo "   ✅ No existing venv"
fi

# Step 3: Create new virtual environment
echo ""
echo "3️⃣  Creating new virtual environment..."
python3 -m venv venv
echo "   ✅ Virtual environment created"

# Step 4: Activate and upgrade pip
echo ""
echo "4️⃣  Upgrading pip..."
source venv/bin/activate
pip install --upgrade pip setuptools wheel

# Step 5: Install dependencies
echo ""
echo "5️⃣  Installing dependencies..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo "   ✅ Dependencies installed from requirements.txt"
else
    echo "   ⚠️  requirements.txt not found, installing manually..."
    pip install flask pandas PyPDF2 requests psutil openpyxl
fi

# Step 6: Verify installation
echo ""
echo "6️⃣  Verifying installation..."
python -c "import flask; print(f'   ✅ Flask {flask.__version__}')" || echo "   ❌ Flask not installed"
python -c "import pandas; print(f'   ✅ Pandas {pandas.__version__}')" || echo "   ❌ Pandas not installed"
python -c "import PyPDF2; print(f'   ✅ PyPDF2 {PyPDF2.__version__}')" || echo "   ❌ PyPDF2 not installed"
python -c "import requests; print(f'   ✅ Requests {requests.__version__}')" || echo "   ❌ Requests not installed"
python -c "import psutil; print(f'   ✅ psutil {psutil.__version__}')" || echo "   ❌ psutil not installed"
python -c "import openpyxl; print(f'   ✅ openpyxl {openpyxl.__version__}')" || echo "   ❌ openpyxl not installed"

# Step 7: Create required directories
echo ""
echo "7️⃣  Creating required directories and clearing logs..."
mkdir -p /tmp/cbp_uploads
mkdir -p /tmp/cbp_outputs
: > /tmp/cbp_debug.log 2>/dev/null || true
rm -f /tmp/cbp_uploads/* /tmp/cbp_outputs/* 2>/dev/null || true
echo "   ✅ Directories created, logs cleared"

# Step 8: Test app import
echo ""
echo "8️⃣  Testing application import..."
python -c "
import sys
try:
    # Test if app can be imported
    with open('app_v3.5.10.py', 'r') as f:
        code = f.read()
    compile(code, 'app_v3.5.10.py', 'exec')
    print('   ✅ Application code compiles successfully')
except SyntaxError as e:
    print(f'   ❌ Syntax error: {e}')
    sys.exit(1)
except Exception as e:
    print(f'   ⚠️  Warning: {e}')
"

echo ""
echo "═══════════════════════════════════════════════════════════════════════════════"
echo "✅ REBUILD COMPLETE!"
echo "═══════════════════════════════════════════════════════════════════════════════"
echo ""
echo "To start the server, run:"
echo "  source venv/bin/activate"
echo "  python app_v3.5.10.py"
echo ""
echo "Or use the startup script:"
echo "  ./start_server.sh"
echo ""




