# Deployment Guide

## Development Setup (Local)

### Step 1: Environment Setup

You already have the environment configured with Python 3.12 and npm dependencies installed. Your existing `.env` file will work with the new system.

```bash
# Activate the virtual environment
source .venv-new/bin/activate
```

### Step 2: Run Backend

Start the FastAPI backend server:

```bash
cd backend
uvicorn job_monitor.main:app --reload --host 0.0.0.0 --port 8000
```

The backend will be available at **http://localhost:8000**. API docs at **http://localhost:8000/docs**.

### Step 3: Run Frontend (in a new terminal)

```bash
cd frontend
npm run dev
```

The React dashboard will be available at **http://localhost:5173**.

### Step 4: First Scan

1. Open **http://localhost:5173** in your browser
2. Click the **"Scan Emails"** button
3. The app will connect to your Gmail (using credentials from `.env`), scan for job emails, and populate the dashboard

---

## Production Deployment (Docker)

### Build and Run

```bash
docker compose up --build
```

Access the app at **http://localhost:8000**.

### Custom Configuration

Create a `.env` file in the project root with your credentials:

```bash
# Required
IMAP_HOST=imap.gmail.com
EMAIL_USERNAME=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
OPENAI_API_KEY=sk-...

# Optional tuning
MAX_SCAN_EMAILS=50
LLM_MODEL=gpt-4o-mini
LOG_LEVEL=INFO
```

---

## Migration from Old Script

The old [`monitor_job_apps.py`](monitor_job_apps.py) can coexist with the new system. Here's how to migrate:

### Option 1: Fresh Start

Simply use the new system going forward. The old Numbers file and JSON state are ignored.

### Option 2: Import Historical Data (Manual)

1. Export your [`job_application_tracker.numbers`](job_application_tracker.numbers) or [`.xlsx`](job_application_tracker.xlsx) to CSV
2. Use the API to bulk-import:

```python
import requests
import csv

with open('old_applications.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        requests.post('http://localhost:8000/api/applications', json={
            'company': row['Company'],
            'job_title': row['Job Title'],
            'status': row['Status'],
            'source': 'migrated'
        })
```

### Option 3: Transfer State Only

Copy the last UID from [`.job_monitor_state.json`](.job_monitor_state.json) to avoid re-scanning old emails:

```sql
INSERT INTO scan_state (email_account, email_folder, last_uid, last_scan_at)
VALUES ('jackychen9803@gmail.com', 'INBOX', 5738, datetime('now'));
```

---

## Next Steps for Production

### Immediate Improvements (Phase 7)

1. **Background Scanning** — Replace synchronous scan with Celery/background task queue
2. **WebSocket Support** — Real-time scan progress updates to the frontend
3. **Authentication** — Add user login (OAuth2 + JWT tokens) for multi-user support
4. **Rate Limiting** — Protect API endpoints with slowapi or similar
5. **Error Tracking** — Integrate Sentry for production error monitoring

### Testing (Phase 8)

```bash
cd backend && pytest -v --cov=job_monitor
```

Create tests in `tests/` directory:
- Unit tests for extraction rules
- Mock IMAP responses for email pipeline tests
- API integration tests with httpx
- Test fixtures in `conftest.py`

### Database Migration (Phase 9)

For multi-user production, switch from SQLite to PostgreSQL:

```bash
# Update .env
DATABASE_URL=postgresql://user:password@localhost:5432/job_monitor

# Run Alembic migrations
cd backend
alembic init alembic
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

### Monitoring (Phase 10)

Add observability:
- **Prometheus metrics** — FastAPI middleware for request/response metrics
- **Grafana dashboards** — Visualize scan frequency, LLM costs, error rates
- **Health checks** — `/api/health` already implemented

### Multi-User Expansion (Phase 11)

1. Add `users` table with email accounts per user
2. Add authentication (FastAPI Security + OAuth2)
3. Modify queries to filter by `user_id`
4. Add user settings page in frontend
5. Deploy to cloud (AWS ECS, GCP Cloud Run, or DigitalOcean)

---

## Architecture Benefits

Compared to the original 711-line script, the new architecture provides:

| Feature | Before | After |
|---------|--------|-------|
| **Testability** | Monolithic, hard to test | 15+ independent modules, mockable |
| **Cross-platform** | macOS-only (Numbers.app) | SQLite + CSV/Excel (any OS) |
| **Duplicate handling** | None — re-scans all emails | DB unique constraints prevent duplicates |
| **Status history** | No audit trail | Full history in `status_history` table |
| **Web interface** | None | React dashboard with filters and charts |
| **API** | None | RESTful API with Swagger docs |
| **Retry logic** | None — fails on transient errors | Tenacity retry for IMAP and LLM |
| **LLM providers** | Hardcoded OpenAI | Swappable via protocol interface |
| **Logging** | print() statements | Structured logging with levels and JSON output |
| **Deployment** | Manual Python script + cron/launchd | Docker Compose with one command |

---

## File Structure Summary

```
backend/                    # FastAPI + extraction engine
├── job_monitor/
│   ├── main.py            # FastAPI app (15 routes)
│   ├── config.py          # Pydantic settings
│   ├── models.py          # SQLAlchemy ORM (4 tables)
│   ├── schemas.py         # API request/response validation
│   ├── database.py        # Engine + session management
│   ├── api/               # 4 REST routers (apps, scan, stats, export)
│   ├── email/             # IMAP client, parser, classifier
│   ├── extraction/        # Rules + LLM + orchestration
│   └── export/            # CSV + Excel exporters

frontend/                   # React + Vite + Tailwind
├── src/
│   ├── App.tsx            # Router
│   ├── api/client.ts      # Typed fetch wrapper
│   ├── components/        # 7 reusable components
│   ├── pages/             # Dashboard + Detail
│   └── types/             # TypeScript interfaces

plans/architecture.md       # Full design document
```

---

## Quick Commands

```bash
# Install dependencies
make install

# Start backend
make backend

# Start frontend (in separate terminal)
make frontend

# Lint
make lint

# Test
make test

# Docker
docker compose up --build
```
