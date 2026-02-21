# Evaluation Framework — Usage Guide

## Quick Start

### 1. First-time setup

```bash
./setup_eval.sh
```

This recreates the venv with Python 3.12, installs all dependencies, and creates database tables.

### 2. Start the servers

```bash
# Terminal 1: Backend
.venv/bin/uvicorn job_monitor.main:app --reload --host 0.0.0.0 --port 8000 --app-dir backend

# Terminal 2: Frontend
cd frontend && npm run dev
```

Visit **http://localhost:5173/eval**

---

## Workflow

### Phase 1: Build your labeled dataset

1. Go to `/eval` (Evaluation Dashboard)
2. In "Download Emails to Cache":
   - Set date range (e.g., `since: 2025-01-01, before: 2026-01-01`)
   - Set `max_count` (default 500)
   - Click **Download**
3. Emails are fetched from IMAP once and stored locally with raw RFC822 bytes in `cached_emails` table

### Phase 2: Label emails (human-in-the-loop)

1. Click **Review Emails** or go to `/eval/review`
2. Filter to "unlabeled" emails
3. Click **Review** on any email to open the split-panel labeling UI

#### The Review UI (3-column layout)

**Left column: Source Email**
- Subject, sender, date, full body text
- Expandable metadata (Message-ID, Thread-ID, UID)

**Middle column: Pipeline Predictions (read-only)**
- Shows what the current pipeline extracted
- Color-coded borders:
  - 🟢 Green = matches your label
  - 🔴 Red = differs from your label
  - ⚪️ Gray = no label yet (no comparison)

**Right column: Ground Truth Form (editable)**
All fields use dropdowns populated from existing data:
- **Is Job Related**: Yes/No toggle
- **Correct Company**: Searchable dropdown from `applications` + previous labels
- **Correct Job Title**: Searchable dropdown
- **Correct Status**: Fixed list (`已申请`, `面试`, `拒绝`, `Offer`, `Unknown`)
- **Application Group**: Pick existing or click "＋ New Group" to create inline
  - Groups represent a single real-world job application
  - Emails with the same group ID are treated as belonging together
- **Notes**: Free-text comments

**Keyboard shortcuts:**
- `⌘S` or `Ctrl+S` — Save
- `⌘Enter` or `Ctrl+Enter` — Save & Next
- `← / →` buttons — Navigate between emails

**Bulk actions** (from Review Queue):
- Select multiple emails
- "Mark Not Job" — bulk label as `is_job_related=false`
- "Skip" — mark as skipped (excluded from metrics)

### Phase 3: Run evaluation

1. Go back to `/eval` dashboard
2. Click **Run Evaluation**
   - Replays all cached emails through the pipeline
   - Compares predictions against your labels
   - Scores accuracy at each stage independently
3. View results: `/eval/runs` shows all historical runs

### Phase 4: Analyze results

Click any run to see `/eval/runs/:id` with full metrics:

**Classification Accuracy**
- 2×2 confusion matrix (TP/FP/TN/FN)
- Precision, recall, F1
- Clickable FP/FN examples linking to review page

**Field Extraction Accuracy**
- Per-field table: exact match, partial match, wrong, missing
- Exact accuracy % and partial accuracy % (partial = fuzzy ≥0.8 similarity)
- Error examples showing `predicted → expected` for each field

**Status Detection Accuracy**
- Full N×N confusion matrix heatmap
- Per-class precision/recall/F1/support
- Overall accuracy

**Grouping / Deduplication Accuracy**
- **ARI** (Adjusted Rand Index) — overall clustering agreement
- **Homogeneity** — each predicted group contains only one true application
- **Completeness** — each true application is in one predicted group
- **V-measure** — harmonic mean of homogeneity and completeness
- **Split errors**: One application fragmented into multiple groups
- **Merge errors**: Multiple applications collapsed into one group
- Specific error examples with email subjects and group IDs

### Phase 5: Iterate and improve

1. Make changes to the pipeline:
   - Edit classifier keywords in [`classifier.py`](backend/job_monitor/email/classifier.py)
   - Update regex patterns in [`rules.py`](backend/job_monitor/extraction/rules.py)
   - Adjust LLM prompts in [`llm.py`](backend/job_monitor/extraction/llm.py)
   - Modify linking logic in [`resolver.py`](backend/job_monitor/linking/resolver.py)

2. Run another evaluation — no need to re-download emails or re-label
3. Compare accuracy across runs in `/eval/runs` table
4. Click error examples to jump to the email and verify if the label is wrong or the pipeline needs fixing

---

## API Reference

All endpoints under `/api/eval/*`:

### Cache Management
- `POST /api/eval/cache/download` — Fetch emails from IMAP and cache
- `GET /api/eval/cache/stats` — Cache statistics
- `GET /api/eval/cache/emails?page=1&review_status=unlabeled` — List cached emails
- `GET /api/eval/cache/emails/{id}` — Get single email with predictions

### Labeling
- `GET /api/eval/labels/{cached_email_id}` — Get label for an email
- `PUT /api/eval/labels/{cached_email_id}` — Create or update label
- `POST /api/eval/labels/bulk` — Bulk update labels

### Application Groups
- `GET /api/eval/groups` — List all groups with email counts
- `POST /api/eval/groups` — Create new group
- `PUT /api/eval/groups/{id}` — Update group
- `DELETE /api/eval/groups/{id}` — Delete group

### Dropdown Data
- `GET /api/eval/dropdown/options` — Get companies, titles, statuses for dropdowns

### Evaluation Runs
- `POST /api/eval/runs` — Trigger a new evaluation run
- `GET /api/eval/runs` — List all runs
- `GET /api/eval/runs/{id}` — Get run detail with full report
- `GET /api/eval/runs/{id}/results?errors_only=true` — Get per-email results
- `DELETE /api/eval/runs/{id}` — Delete run

---

## Database Schema

New tables created in `job_monitor.db`:

- **`cached_emails`** — Raw RFC822 bytes + parsed metadata
- **`eval_application_groups`** — Named groups representing job applications
- **`eval_labels`** — Ground truth annotations per email
- **`eval_runs`** — Evaluation run records with aggregate metrics
- **`eval_run_results`** — Per-email predictions + correctness flags per run

All tables are auto-created when the backend starts via `Base.metadata.create_all()`.

---

## Metrics Reference

### Classification
- **Accuracy** = (TP + TN) / Total
- **Precision** = TP / (TP + FP) — of emails predicted as job-related, how many were correct
- **Recall** = TP / (TP + FN) — of actual job emails, how many were detected
- **F1** = 2 × P × R / (P + R) — harmonic mean

### Field Extraction
- **Exact match** — normalized strings are identical
- **Partial match** — fuzzy similarity ≥ 0.8 (e.g., "Meta Platforms" vs "Meta")
- **Wrong** — neither exact nor partial
- **Missing** — ground truth has value, prediction is empty

### Status Detection
- Multi-class classification across: `已申请`, `面试`, `拒绝`, `Offer`, `Unknown`
- Per-class precision/recall/F1
- Confusion matrix shows which statuses are commonly misclassified

### Grouping
- **ARI** (Adjusted Rand Index) — measures clustering agreement, adjusted for chance
  - 1.0 = perfect match
  - 0.0 = random clustering
  - Negative = worse than random
- **Homogeneity** — all emails in a predicted group belong to same true application (penalizes merges)
- **Completeness** — all emails of a true application are in same predicted group (penalizes splits)
- **V-measure** — harmonic mean of homogeneity and completeness

---

## Troubleshooting

**Backend won't start:**
- Check `.venv/bin/python --version` is ≥3.10
- Re-run `./setup_eval.sh`
- Ensure `.env` file exists with IMAP credentials

**Frontend TypeScript errors:**
- Run `cd frontend && npm install`
- Check `npx tsc --noEmit` for any type errors

**"No module named sqlalchemy":**
- The venv is broken or using wrong Python
- Run `rm -rf .venv && ./setup_eval.sh`

**Eval tables not found:**
- The import in [`database.py`](backend/job_monitor/database.py) line 17 must load eval models
- Restart backend to trigger `create_all()`

**No emails in cache:**
- Visit `/eval` dashboard
- Use "Download Emails to Cache" form
- Check IMAP credentials in `.env`
