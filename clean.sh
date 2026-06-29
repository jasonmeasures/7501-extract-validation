#!/bin/bash
# Clean build artifacts, caches, and runtime logs for playground deploy

set -e
cd "$(dirname "$0")"

echo "🧹 Cleaning CBP 7501 project..."

# Python caches
find . -type d -name "__pycache__" -not -path "./venv/*" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -not -path "./venv/*" -delete 2>/dev/null || true

# Old venv backups from rebuild.sh
rm -rf venv.backup.* 2>/dev/null || true

# Runtime temp dirs and logs
mkdir -p /tmp/cbp_uploads /tmp/cbp_outputs
rm -f /tmp/cbp_uploads/* /tmp/cbp_outputs/* 2>/dev/null || true
: > /tmp/cbp_debug.log 2>/dev/null || true

# Debug response dumps left in upload folder
rm -f /tmp/cbp_uploads/*_api1_response.json /tmp/cbp_uploads/*_parsed_response.json 2>/dev/null || true

echo "✅ Clean complete"
echo "   Project size: $(du -sh . | cut -f1)"
echo "   venv:         $(du -sh venv 2>/dev/null | cut -f1 || echo 'not found — run ./rebuild.sh')"
