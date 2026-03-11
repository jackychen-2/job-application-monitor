# Job Application Monitor

A Gmail-driven job tracker with Google OAuth, FastAPI, React, and a database-backed incremental scan job system that works both locally with SQLite and on Vercel with Postgres.

## Features

- **Google Login** — Secure Google OAuth login with server-side sessions (HttpOnly cookies)
- **Per-User Mailbox Scanning** — Each logged-in user scans their own Gmail mailbox
- **Email Scanning** — Connects via Gmail API (read-only), scans for job-related emails using keyword classification
- **LLM Extraction** — Uses OpenAI (GPT-4o-mini) to extract company, job title, and status with rule-based fallback
- **Web Dashboard** — React frontend with filterable table, status charts, and stats cards
- **REST API** — FastAPI backend with full CRUD for applications
- **Status Tracking** — Audit trail of all status changes with timestamps
- **Duplicate Detection** — Prevents re-processing emails and duplicate application entries
- **Export** — Download applications as CSV or Excel
- **Retry Logic** — Gmail API and LLM calls retry on transient failures (tenacity)
- **Database-Backed Scan Jobs** — Incremental scans persist progress/results in the database instead of in-memory SSE state
- **Vercel Ready** — Split frontend/backend deployment with Postgres for production

## Architecture

```
backend/job_monitor/
├── main.py              # FastAPI app
├── config.py            # Pydantic settings
├── models.py            # SQLAlchemy ORM
├── schemas.py           # API schemas
├── database.py          # DB engine + sessions
├── api/                 # REST endpoints
├── email/               # Gmail client, parser, classifier
├── extraction/          # Rules + LLM pipeline
└── export/              # CSV + Excel exporters

frontend/src/
├── App.tsx              # React router
├── api/client.ts        # Typed API client
├── components/          # Reusable UI components
└── pages/               # Dashboard + Detail pages
```

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- A Google Cloud OAuth app (for Google sign-in + Gmail access)
- OpenAI API key (optional, for LLM extraction)

### 1. Clone and configure

```bash
cp .env.example .env
# Edit .env with Google OAuth + encryption settings and optional OpenAI key
```

### 2. Backend setup

```bash
python3 -m venv .venv
source .venv/bin/activate
cd backend && pip install -e ".[dev]"
```

### 3. Frontend setup

```bash
cd frontend && npm install
```

### 4. Run (development)

In two terminal windows:

```bash
# Terminal 1: Backend
cd backend && uvicorn job_monitor.main:app --reload --port 8000

# Terminal 2: Frontend
cd frontend && npm run dev
```

Open **http://localhost:5173** in your browser.

### 5. Run (Docker)

```bash
docker compose up --build
```

Open **http://localhost:8000** in your browser.

## Vercel Production Shape

- Frontend Vercel project:
  root directory `frontend/`
  domain `https://offerthread.com`
- Backend Vercel project:
  root directory `backend/`
  domain `https://api.offerthread.com`
- Database:
  hosted Postgres via Vercel Marketplace / Neon

This repo now supports:

- local development with `SQLite`
- production deployment with `Postgres`
- front-end polling of incremental scan jobs via:
  - `POST /api/scan/jobs`
  - `GET /api/scan/jobs/active`
  - `GET /api/scan/jobs/{job_id}`
  - `POST /api/scan/jobs/{job_id}/step`
  - `POST /api/scan/jobs/{job_id}/cancel`

The first Vercel release intentionally removes the evaluation UI from the main app shell and only keeps incremental scanning.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/applications` | List applications (with filters) |
| GET | `/api/applications/{id}` | Get application + history |
| POST | `/api/applications` | Create application manually |
| PATCH | `/api/applications/{id}` | Update application |
| DELETE | `/api/applications/{id}` | Delete application |
| POST | `/api/scan` | Compatibility wrapper that creates an incremental scan job |
| POST | `/api/scan/jobs` | Create or reuse the active incremental scan job |
| GET | `/api/scan/jobs/active` | Return the active scan job for the current journey |
| GET | `/api/scan/jobs/{id}` | Read scan job progress |
| POST | `/api/scan/jobs/{id}/step` | Process the next batch of messages |
| POST | `/api/scan/jobs/{id}/cancel` | Request cancellation |
| GET | `/api/scan/status` | Last scan state |
| GET | `/api/stats` | Dashboard statistics |
| GET | `/api/export?format=csv` | Download CSV |
| GET | `/api/export?format=excel` | Download Excel |

## Environment Variables

See [`.env.example`](.env.example) and [`frontend/.env.example`](frontend/.env.example) for local examples.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IMAP_HOST` | ❌ | `imap.gmail.com` | Legacy IMAP host (not used in Gmail API mode) |
| `EMAIL_USERNAME` | ❌ | — | Legacy fallback username (not used in Gmail API mode) |
| `EMAIL_PASSWORD` | ❌ | — | Legacy fallback password (not used in Gmail API mode) |
| `GOOGLE_CLIENT_ID` | ✅ | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | ✅ | — | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | ✅ | — | OAuth callback URL (e.g. `http://localhost:8000/api/auth/google/callback`) |
| `GOOGLE_OAUTH_SCOPES` | ✅ | `openid,email,profile,https://www.googleapis.com/auth/gmail.readonly` | Required OAuth scopes |
| `TOKEN_ENCRYPTION_KEY` | ✅ | — | Fernet key used to encrypt stored OAuth tokens |
| `LEGACY_OWNER_EMAIL` | ✅ (existing DB) | — | Existing rows are backfilled to this owner on startup |
| `AUTH_COOKIE_NAME` | ❌ | `job_monitor_session` | Session cookie name |
| `AUTH_SESSION_TTL_DAYS` | ❌ | `30` | Login session lifetime |
| `AUTH_COOKIE_SECURE` | ❌ | `false` | Use `true` for HTTPS deployments |
| `CORS_ORIGINS` | ❌ | `http://localhost:5173,http://localhost:3000` | Allowed browser origins |
| `LLM_ENABLED` | ❌ | `true` | Enable LLM extraction |
| `LLM_API_KEY` | ❌ | — | OpenAI API key |
| `DATABASE_URL` | ❌ | `sqlite:///job_monitor.db` | Database URL |
| `SCAN_JOB_BATCH_SIZE` | ❌ | `5` | Max emails processed per scan step |

## Google OAuth Setup

1. Create OAuth credentials in Google Cloud Console.
2. Add authorized redirect URI: `http://localhost:8000/api/auth/google/callback` (and your production callback).
3. Put client ID/secret in `.env`.
4. Generate a Fernet key and set `TOKEN_ENCRYPTION_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

After login, scans run against the logged-in user's Gmail account.

## Data Migration

To move your existing local SQLite data into production Postgres:

```bash
./.venv-new/bin/python backend/scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path job_monitor.db \
  --database-url "$DATABASE_URL" \
  --truncate
```

The migration script copies the core user/application tables and intentionally skips `auth_sessions`.

## Development

```bash
make lint      # Run ruff linter
make test      # Run pytest with coverage
make backend   # Start backend dev server
make frontend  # Start frontend dev server
```

## License

MIT
