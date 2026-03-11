# Deployment Guide

## Target Shape

- Frontend:
  Vercel project rooted at `frontend/`
  production domain `https://offerthread.com`
- Backend:
  Vercel project rooted at `backend/`
  production domain `https://api.offerthread.com`
- Database:
  hosted Postgres, for example Neon via Vercel Marketplace

This repo now keeps two modes:

- local mode:
  frontend + backend on your machine with `SQLite`
- deployed mode:
  Vercel frontend + Vercel backend + `Postgres`

## Local Development

### 1. Configure env files

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
```

For local development, keep:

```env
DATABASE_URL=sqlite:///job_monitor.db
FRONTEND_URL=http://localhost:5173
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback
AUTH_COOKIE_SECURE=false
```

### 2. Run the backend

```bash
cd backend
PYTHONPATH=. ../.venv-new/bin/uvicorn job_monitor.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Run the frontend

```bash
cd frontend
npm run dev
```

## Vercel Deployment

### 1. Create the backend database

Provision a Postgres database and copy its connection string.

Example:

```env
DATABASE_URL=postgresql+psycopg://...
```

### 2. Migrate your local SQLite data

Run the one-off migration script before your first production launch:

```bash
./.venv-new/bin/python backend/scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path job_monitor.db \
  --database-url "$DATABASE_URL" \
  --truncate
```

Migrated tables:

- `users`
- `google_accounts`
- `journeys`
- `applications`
- `status_history`
- `processed_emails`
- `scan_state`
- `application_merge_events`
- `application_merge_items`

Not migrated:

- `auth_sessions`

Users should log in again after the first production cutover.

### 3. Deploy the backend Vercel project

Project settings:

- Root Directory:
  `backend`
- Framework:
  Other / Python
- Entry file:
  `app.py`

Set these environment variables in the backend project:

```env
DATABASE_URL=postgresql+psycopg://...
FRONTEND_URL=https://offerthread.com
CORS_ORIGINS=https://offerthread.com
AUTH_COOKIE_SECURE=true
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://api.offerthread.com/api/auth/google/callback
TOKEN_ENCRYPTION_KEY=...
LLM_API_KEY=...
LOG_LEVEL=INFO
SCAN_JOB_BATCH_SIZE=5
```

### 4. Deploy the frontend Vercel project

Project settings:

- Root Directory:
  `frontend`

Set this environment variable in the frontend project:

```env
VITE_API_BASE_URL=https://api.offerthread.com/api
```

### 5. Connect domains

- `offerthread.com` -> frontend Vercel project
- `api.offerthread.com` -> backend Vercel project

### 6. Update Google OAuth

Google Cloud Console must include:

- Authorized origin:
  `https://offerthread.com`
- Authorized redirect URI:
  `https://api.offerthread.com/api/auth/google/callback`

### 7. Smoke test production

Verify:

- `https://offerthread.com`
- `https://api.offerthread.com/api/health`
- Google login
- scan job creation
- scan job resume after refresh
- application data still present

## Scan Behavior in Production

This Vercel version intentionally supports only incremental scanning.

- No SSE scan stream
- No full mailbox scan UI
- No date-range scan UI
- No evaluation UI in the main app shell

The scan flow is now:

1. Frontend creates or reuses an active scan job.
2. Backend stores Gmail message IDs in `scan_job_messages`.
3. Frontend polls every 2 seconds.
4. Each poll triggers a small `step` call.
5. Progress and results persist in the database.

If the user closes the page, the job stops progressing until they return. If they reopen the page, the frontend restores the active job and continues.
