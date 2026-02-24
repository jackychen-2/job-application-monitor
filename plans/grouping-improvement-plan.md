# Email Grouping Improvement Plan

## Problem

Two emails from the **same Zoom application** were split into different groups because the LLM extracted inconsistent company names:

- Email #77: `"Zoom Communications"` → normalized to `"zoom communications"`
- Email #78: `"Zoom"` → normalized to `"zoom"`

Since `"zoom" ≠ "zoom communications"`, the DB candidate query returned zero results — the LLM confirmation step was **never reached** — and a duplicate group was created.

The fix operates at two levels: fix the **extraction** to be consistent, and fix the **disambiguation** to be more resilient.

---

## Improvement 1: LLM Extraction Prompt Enhancement

**File**: [`backend/job_monitor/extraction/llm.py`](backend/job_monitor/extraction/llm.py:82)

**Goal**: Make the LLM consistently extract the full, canonical company name so that normalization always produces the same key.

### Current Prompt (excerpt)
```
- company: the real hiring company name, not ATS vendor.
```

### Proposed Addition
Add a dedicated section to `_SYSTEM_PROMPT`:

```python
"COMPANY NAME RULES:\n"
"- Use the FULL official company name as stated in the email body or signature.\n"
"  Prefer the complete form: 'Zoom Communications' over 'Zoom', 'Amazon Web Services' over 'AWS'.\n"
"- If the subject uses a short form but the body/sender uses the full name, use the full name.\n"
"- Do NOT use ATS platform names (Greenhouse, Workday, Lever, iCIMS) as the company.\n"
"- Strip personal address prefixes only: 'Your Zoom' → 'Zoom', 'Welcome to Google' → 'Google'.\n"
"- Preserve brand-meaningful words: 'Meta Platforms', 'Amazon Web Services', 'Zoom Communications'.\n"
```

### Why This Works

In the Zoom case:
- Subject: `"Zoom – Senior Data Engineer – Online Assessment"` → short form `"Zoom"`
- Body: `"Thank you for your interest in the Senior Data Engineer position at Zoom"` → still short

The LLM would need to recognize that if a prior email said `"Zoom Communications"` and this email is from the same sender domain, it should prefer the longer form. However, since each email is processed independently, the safer instruction is to **always use the full form when available in the body/signature**.

**Expected outcome**: Both emails extract `"Zoom Communications"`, normalize to the same key, and are correctly grouped without needing any disambiguation.

---

## Improvement 2: LLM-Based Group Disambiguation

**File**: [`backend/job_monitor/linking/resolver.py`](backend/job_monitor/linking/resolver.py:303)

**Goal**: When company normalization produces zero candidates (due to minor name variations), fall back to a broader fuzzy search and let the LLM confirm membership — rather than immediately creating a new group.

### Current Behavior

```
normalize("Zoom") → "zoom"
DB query: normalized_company = "zoom"
Result: 0 candidates
→ Create new group (wrong)
```

### Proposed Enhancement

After the zero-candidate case, add a **rescue pass** using a broader fuzzy search before creating a new group:

```python
# After: if not candidates → currently creates new group immediately

# NEW: Rescue pass — try fuzzy company match
if not candidates and llm_provider is not None:
    fuzzy_candidates = _find_fuzzy_company_candidates(session, normalized, threshold=0.75)
    
    if fuzzy_candidates:
        for candidate in fuzzy_candidates[:3]:  # Check top 3 most recent
            confirm = llm_provider.confirm_same_application(
                email_subject=email_subject,
                email_sender=email_sender,
                email_body=email_body,
                app_company=candidate.company,
                app_job_title=candidate.job_title or "",
                app_status=candidate.status,
                app_last_email_subject=candidate.email_subject or "",
            )
            if confirm.is_same_application:
                logger.info(
                    "linked_by_fuzzy_llm_rescue",
                    company=company,
                    matched_company=candidate.company,
                    application_id=candidate.id,
                )
                return LinkResult(
                    application_id=candidate.id,
                    confidence=0.75,
                    link_method="company_fuzzy",
                )
```

### Fuzzy Candidate Search

```python
def _find_fuzzy_company_candidates(
    session: Session,
    normalized: str,
    threshold: float = 0.75,
) -> list[Application]:
    """Find applications with similar normalized company names.

    Uses SequenceMatcher to catch cases like:
      "zoom" vs "zoom communications" → similarity 0.80 → match
      "tesla" vs "tesla motors"       → similarity 0.83 → match
      "google" vs "amazon"            → similarity 0.31 → no match
    """
    from difflib import SequenceMatcher

    all_apps = session.query(Application).all()
    scored = []
    for app in all_apps:
        existing_norm = app.normalized_company or ""
        sim = SequenceMatcher(None, normalized, existing_norm).ratio()
        if sim >= threshold:
            scored.append((sim, app))

    # Return sorted by similarity descending, then by most recent
    scored.sort(key=lambda x: (-x[0], -(x[1].id)))
    return [app for _, app in scored]
```

### LLM Prompt for Disambiguation

The **existing** [`confirm_same_application()`](backend/job_monitor/extraction/llm.py:174) is reused as-is. The LLM receives:

```
Existing Application:
- Company: Zoom Communications
- Job Title: Senior Data Engineer
- Current Status: 已申请
- Last Email Subject: "Jacky, Thank you for your application to Zoom Communications"

New Email:
- Subject: "Zoom – Senior Data Engineer – Online Assessment Invitation"
- From: no-reply@zoom.us
- Body: Hi Jacky, Thank you for your interest in the Senior Data Engineer position at Zoom...

Is this new email about the SAME or a DIFFERENT job application?
```

**Expected LLM response**: `same` → groups correctly linked.

### Zoom Case With Both Improvements

```
Email #77: "Thank you for your application to Zoom Communications"
  ↓ Prompt improvement: LLM extracts "Zoom Communications"
  ↓ normalize("Zoom Communications") → "zoom communications"
  ↓ No existing apps → create Group #81

Email #78: "Zoom – Senior Data Engineer – Online Assessment"
  ↓ LLM extracts "Zoom" (body only says "Zoom", no full name)
  ↓ normalize("Zoom") → "zoom"
  ↓ DB query for "zoom" → 0 candidates
  ↓ [RESCUE] fuzzy search: "zoom" vs "zoom communications" → 0.80 ≥ 0.75 ✓
  ↓ LLM confirm: "same" ✓
  ↓ Linked to Group #81 ✅
```

Both improvements together cover all failure modes:
- **Improvement 1** prevents the inconsistency at extraction time (most cases)
- **Improvement 2** catches the remaining cases at linking time (safety net)

---

## Files to Change

| File | Change |
|------|--------|
| [`backend/job_monitor/extraction/llm.py`](backend/job_monitor/extraction/llm.py:82) | Add company name rules to `_SYSTEM_PROMPT` |
| [`backend/job_monitor/linking/resolver.py`](backend/job_monitor/linking/resolver.py:303) | Add `_find_fuzzy_company_candidates()` and rescue pass in `resolve_by_company()` |
| [`backend/job_monitor/eval/runner.py`](backend/job_monitor/eval/runner.py:254) | Apply same fuzzy rescue logic in the eval grouping stage |
