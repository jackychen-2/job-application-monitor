# Linking Workflow v2 (Req-ID Priority + Timeline LLM)

## Scope
This workflow updates application linking/grouping with:
- Req ID exact match as a hard direct-link rule.
- Title fallback when Req ID is missing/unmatched.
- Timeline-aware LLM confirmation for ambiguous candidates and fuzzy rescue.

## Workflow Chart

```mermaid
flowchart TD
    A["Incoming email"] --> XE{"LLM enabled for extraction?"}
    XE -- yes --> B1["Stage 1A (LLM): classify + extract<br/>timeout/failure -> rules fallback"]
    XE -- no --> B2["Stage 1B (Rules): classify + extract"]
    B1 --> B["Extraction output<br/>company/title/req_id/status/date"]
    B2 --> B
    B --> C{"company normalized?<br/>normalize_company(company) non-empty"}
    C -- no --> N["Create new application group"]
    C -- yes --> D["Load same-company candidates"]
    D --> D1{"same-company candidates found?"}
    D1 -- no --> N
    D1 -- yes --> E

    E{"Rule 0: incoming req_id exists?"}

    E -- yes --> F{"Any candidate req_id == incoming req_id<br/>AND title exact match?"}
    F -- yes --> G["Direct link to matched application<br/>link_method=company_req_id<br/>No LLM confirm (company+req_id+title)"]
    F -- no --> H["Build base candidate pool<br/>if req match but title mismatch -> keep req-matched pool<br/>else keep legacy no-req candidates"]
    E -- no --> H["Build base candidate pool<br/>no req_id -> keep all same-company candidates"]
    H --> I{"title available and similar matches exist?"}
    I -- yes --> J["Prioritize title-matched subset"]
    I -- no --> K["Use base candidate pool"]

    J --> P["Candidate pool after Rule 0 + title fallback"]
    K --> P

    P --> Q["Apply Rule 1 filter<br/>new status=已申请 and old in progressed"]
    Q --> R{"candidate pool empty?"}
    R -- no --> S1["Use filtered same-company pool"]
    R -- yes --> X0{"LLM enabled?"}
    X0 -- no --> N
    X0 -- yes --> V["Build fuzzy-company rescue pool (top-N)"]
    V --> W{"rescue pool empty?"}
    W -- yes --> N
    W -- no --> S2["Use fuzzy rescue pool"]

    S1 --> X{"LLM enabled?"}
    S2 --> X
    X -- no --> N
    X -- yes --> S["For each candidate:<br/>LLM confirm_same_application with timeline"]
    S --> T{"any same?"}
    T -- yes --> U["Link to confirmed application<br/>link_method=company / company_fuzzy"]
    T -- no --> N

    G --> AA["Persist email + update status history"]
    U --> AA
    N --> AA

    classDef llm fill:#FFF4CC,stroke:#D9A300,stroke-width:2px,color:#5A4300;
    class XE,B1,V,W,S2,X0,X,S,T,U llm;
```

## Timeline payload sent to `confirm_same_application(...)`
- `new_email_date`
- `app_created_at`
- `app_last_email_date`
- `days_since_last_email`
- `recent_events` (latest 3-5 events, mixed from email + status history)

## Deployment Plan
1. Pre-deploy checks
   - Run targeted tests:
     - `backend/tests/test_recruiter_reach_out_pipeline.py`
     - `backend/tests/test_thread_linking.py` (known legacy failures should be documented if unchanged)
   - Verify no schema migration is required for this specific linking change.
2. Deploy backend
   - Release backend service with updated resolver + LLM confirm prompt/signature.
   - Confirm environment has LLM credentials/timeouts unchanged.
3. Smoke validation in staging
   - Case A: same `req_id` follow-up email links directly (no LLM call).
   - Case B: no `req_id`, same title, same company links through LLM confirm.
   - Case C: rejection then later fresh application with same title is split into new group when timeline indicates new cycle.
   - Case D: fuzzy company variant (e.g., short vs full company name) links via fuzzy rescue + LLM.
4. Production rollout
   - Deploy during low-traffic window.
   - Monitor logs for:
     - `linked_by_company_req_id_exact`
     - `linked_by_company_llm_confirmed`
     - `linked_by_fuzzy_llm_rescue`
     - `company_link_llm_rejected_all`
5. Post-deploy acceptance
   - In review UI, verify job title and req_id are displayed as separate fields.
   - Sample-check re-predict behavior to ensure grouping decisions are recomputed and persisted correctly.
6. Rollback plan
   - Revert backend to previous image/tag.
   - No data migration rollback is needed for this change path.

## Decision Notes
- Req ID equality is treated as strongest identity signal and bypasses LLM.
- Timeline context is used by LLM to distinguish continuation vs new application cycle.
- Title similarity is a prioritization step, not the final authority when LLM is available.
