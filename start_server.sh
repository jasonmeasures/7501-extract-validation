#!/bin/bash
# Start Flask server — playground-ready (reads secrets from env)

cd "$(dirname "$0")"

# Playground mode: INFO logging, no Flask debug reloader, no debug JSON dumps
export APP_ENV="${APP_ENV:-playground}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# Required: set A79_API_KEY in environment or .env before starting
if [ -z "$A79_API_KEY" ]; then
    echo "❌ A79_API_KEY is not set."
    echo "   export A79_API_KEY=\"your-key\" && ./start_server.sh"
    exit 1
fi

# Optional: enables self-healing verify → repair loop
# export CLAUDE_API_KEY="your-key"

# Check for port conflicts
if lsof -ti:5002 > /dev/null 2>&1; then
    echo "⚠️  Port 5002 is already in use — stopping existing process..."
    kill -9 $(lsof -ti:5002) 2>/dev/null
    sleep 2
fi

# Ensure runtime dirs exist and logs are fresh
mkdir -p /tmp/cbp_uploads /tmp/cbp_outputs
: > /tmp/cbp_debug.log

echo "🚀 Starting Flask server..."
echo "📂 Working directory: $(pwd)"
echo "🐍 Python: $(./venv/bin/python --version)"
echo "🌍 APP_ENV: $APP_ENV"
echo "🔑 A79_API_KEY: ${A79_API_KEY:0:20}..."
if [ -n "$CLAUDE_API_KEY" ]; then
    echo "🔑 CLAUDE_API_KEY: configured (self-healing enabled)"
else
    echo "⚠️  CLAUDE_API_KEY: not set (direct A79 extraction only)"
fi
echo ""

./venv/bin/python app_v3.5.10.py
