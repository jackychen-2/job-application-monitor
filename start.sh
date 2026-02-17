#!/bin/bash
# Start Job Application Monitor — single server, everything at http://127.0.0.1:8000
# Usage: ./start.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv-new/bin"

# Kill any existing process on port 8000
lsof -i :8000 -t 2>/dev/null | xargs kill -9 2>/dev/null
sleep 1

echo "🚀 Starting Job Application Monitor..."
echo "   (First startup takes ~30 seconds to load Python packages)"
echo ""

cd "$DIR/backend"
exec "$VENV/uvicorn" job_monitor.main:app --host 127.0.0.1 --port 8000
