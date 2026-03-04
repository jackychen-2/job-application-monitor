# Job Application Monitor

A production-quality tool that monitors your Gmail inbox via Google OAuth + Gmail API (read-only), detects job-application related messages using regex rules + LLM (OpenAI), and tracks applications in a SQLite database with a web dashboard.

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
- **Docker Ready** — Single-command deployment with Docker Compose

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
cp backend/.env.example .env
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

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/applications` | List applications (with filters) |
| GET | `/api/applications/{id}` | Get application + history |
| POST | `/api/applications` | Create application manually |
| PATCH | `/api/applications/{id}` | Update application |
| DELETE | `/api/applications/{id}` | Delete application |
| POST | `/api/scan` | Trigger email scan |
| GET | `/api/scan/status` | Last scan state |
| GET | `/api/stats` | Dashboard statistics |
| GET | `/api/export?format=csv` | Download CSV |
| GET | `/api/export?format=excel` | Download Excel |

## Environment Variables

See [`backend/.env.example`](backend/.env.example) for all configuration options.

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
| `LLM_ENABLED` | ❌ | `true` | Enable LLM extraction |
| `LLM_API_KEY` | ❌ | — | OpenAI API key |
| `DATABASE_URL` | ❌ | `sqlite:///job_monitor.db` | Database URL |

## Google OAuth Setup

1. Create OAuth credentials in Google Cloud Console.
2. Add authorized redirect URI: `http://localhost:8000/api/auth/google/callback` (and your production callback).
3. Put client ID/secret in `.env`.
4. Generate a Fernet key and set `TOKEN_ENCRYPTION_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

After login, scans run against the logged-in user's Gmail account.

## Development

```bash
make lint      # Run ruff linter
make test      # Run pytest with coverage
make backend   # Start backend dev server
make frontend  # Start frontend dev server
```

## License

MIT
