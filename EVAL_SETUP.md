# Evaluation Framework — Quick Setup

## Manual Setup Steps

```bash
# 1. Recreate venv with Python 3.12 (required for SQLAlchemy 2.0+)
rm -rf .venv
/opt/homebrew/bin/python3.12 -m venv .venv

# 2. Install backend dependencies
cd backend
../.venv/bin/pip install -e ".[dev]"
cd ..

# 3. Install frontend dependencies  
cd frontend
npm install
cd ..
```

## Start the App

```bash
# Terminal 1: Backend
.venv/bin/uvicorn job_monitor.main:app --reload --host 0.0.0.0 --port 8000 --app-dir backend

# Terminal 2: Frontend
cd frontend && npm run dev
```

Visit **http://localhost:5173/eval**

## Usage Flow

1. **Download emails** — `/eval` dashboard → set date range → click Download (fetches from IMAP once)
2. **Label emails** — `/eval/review` → click Review on any email → use split-panel UI with dropdowns
3. **Run evaluation** — `/eval` dashboard → click Run Evaluation (scores pipeline vs labels)
4. **Analyze results** — `/eval/runs/:id` → view confusion matrices, accuracy charts, error examples

See [`EVALUATION_GUIDE.md`](EVALUATION_GUIDE.md) for full documentation.
