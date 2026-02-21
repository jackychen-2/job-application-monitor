#!/bin/bash
# Setup script for the evaluation framework

set -e

echo "🔧 Setting up evaluation framework..."

# 1. Recreate venv with Python 3.12 if current venv is broken
if [ ! -f .venv/bin/python ] || [ "$(.venv/bin/python --version 2>&1 | grep -o '3\.[0-9]*' | head -1)" != "3.12" ]; then
  echo "📦 Recreating virtual environment with Python 3.12..."
  rm -rf .venv
  /opt/homebrew/bin/python3.12 -m venv .venv
fi

# 2. Install backend dependencies
echo "📦 Installing backend dependencies..."
cd backend
../.venv/bin/pip install --upgrade pip > /dev/null 2>&1
../.venv/bin/pip install -e ".[dev]"
cd ..

# 3. Install frontend dependencies
echo "📦 Installing frontend dependencies..."
cd frontend
npm install
cd ..

# 4. Create tables
echo "🗄️  Creating database tables..."
cd backend
../.venv/bin/python -c "from job_monitor.main import app; from job_monitor.config import get_config; from job_monitor.database import init_db; init_db(get_config()); print('✓ Database initialized')"
cd ..

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start the evaluation framework:"
echo ""
echo "  Terminal 1: .venv/bin/uvicorn job_monitor.main:app --reload --host 0.0.0.0 --port 8000 --app-dir backend"
echo "  Terminal 2: cd frontend && npm run dev"
echo ""
echo "Then visit: http://localhost:5173/eval"
