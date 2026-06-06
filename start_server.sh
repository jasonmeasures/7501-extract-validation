#!/bin/bash
# Start Flask server with proper environment

cd "/Users/jasonmeasures/Library/CloudStorage/OneDrive-KlearNow/VS Scripts/Clear Audit 7501"

# Check for port conflicts
if lsof -ti:5002 > /dev/null 2>&1; then
    echo "⚠️  Port 5002 is already in use!"
    echo "Killing existing process..."
    kill -9 $(lsof -ti:5002) 2>/dev/null
    sleep 2
fi

# Set API key from A79_API_SETUP.md
export A79_API_KEY="sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i"

# Activate virtual environment and start server
echo "🚀 Starting Flask server..."
echo "📂 Working directory: $(pwd)"
echo "🐍 Python: $(./venv/bin/python --version)"
echo "🔑 API Key: ${A79_API_KEY:0:20}... (configured)"
echo ""

# Start the server
./venv/bin/python app_v3.5.10.py




