/**
 * API client for evaluation endpoints.
 */

import type {
  CachedEmailDetail,
  CachedEmailListResponse,
  CacheDownloadRequest,
  CacheDownloadResult,
  CacheStats,
  EmailPredictionRun,
  CorrectionEntryInput,
  DropdownOptions,
  EvalApplicationGroup,
  EvalGroupInput,
  EvalLabel,
  EvalLabelInput,
  EvalRun,
  EvalRunDetail,
  EvalRunResult,
} from "../types/eval";

const BASE = "/api/eval";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",   // never serve stale GET responses from browser cache
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

// ── Cache ────────────────────────────────────────────────

export function downloadEmails(req: CacheDownloadRequest): Promise<CacheDownloadResult> {
  return request("/cache/download", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function getCacheStats(): Promise<CacheStats> {
  return request("/cache/stats");
}

export function listCachedEmails(params: {
  page?: number;
  page_size?: number;
  review_status?: string;
  search?: string;
  run_id?: number;
}): Promise<CachedEmailListResponse> {
  const qs = new URLSearchParams();
  if (params.page) qs.set("page", String(params.page));
  if (params.page_size) qs.set("page_size", String(params.page_size));
  if (params.review_status) qs.set("review_status", params.review_status);
  if (params.search) qs.set("search", params.search);
  if (params.run_id) qs.set("run_id", String(params.run_id));
  return request(`/cache/emails?${qs}`);
}

export function getCachedEmail(id: number, runId?: number): Promise<CachedEmailDetail> {
  const qs = runId ? `?run_id=${runId}` : "";
  return request(`/cache/emails/${id}${qs}`);
}

export function getEmailPredictionRuns(id: number): Promise<EmailPredictionRun[]> {
  return request(`/cache/emails/${id}/prediction-runs`);
}

// ── Labels ───────────────────────────────────────────────

export function getLabel(cachedEmailId: number, runId?: number): Promise<EvalLabel | null> {
  const qs = runId ? `?run_id=${runId}` : "";
  return request(`/labels/${cachedEmailId}${qs}`);
}

export function upsertLabel(
  cachedEmailId: number,
  data: EvalLabelInput & { corrections?: CorrectionEntryInput[] },
): Promise<EvalLabel> {
  return request(`/labels/${cachedEmailId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function bulkUpdateLabels(data: {
  cached_email_ids: number[];
  is_job_related?: boolean;
  review_status?: string;
}): Promise<{ updated: number }> {
  return request("/labels/bulk", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ── Groups ───────────────────────────────────────────────

export interface ApplicationForEval {
  id: number;
  company: string;
  job_title: string;
  date: string;
  status: string;
  email_count: number;
  email_previews: Array<{ subject: string; sender: string; date: string }>;
  display: string;
}

export function listApplicationsForEval(): Promise<ApplicationForEval[]> {
  return request("/applications");
}

export function listGroups(runId?: number): Promise<EvalApplicationGroup[]> {
  const qs = runId ? `?run_id=${runId}` : "";
  return request(`/groups${qs}`);
}

export function createGroup(data: EvalGroupInput): Promise<EvalApplicationGroup> {
  return request("/groups", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateGroup(id: number, data: EvalGroupInput): Promise<EvalApplicationGroup> {
  return request(`/groups/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteGroup(id: number): Promise<void> {
  return request(`/groups/${id}`, { method: "DELETE" });
}

export interface GroupMember {
  cached_email_id: number;
  subject: string;
  sender: string;
  email_date: string | null;
  review_status: string;
}

export function getGroupMembers(groupId: number): Promise<GroupMember[]> {
  return request(`/groups/${groupId}/members`);
}

// ── Dropdowns ────────────────────────────────────────────

export function getDropdownOptions(): Promise<DropdownOptions> {
  return request("/dropdown/options");
}

// ── Runs ─────────────────────────────────────────────────

export function triggerEvalRun(name?: string): Promise<EvalRun> {
  return request("/runs", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function listEvalRuns(): Promise<EvalRun[]> {
  return request("/runs");
}

export function getEvalRun(id: number): Promise<EvalRunDetail> {
  return request(`/runs/${id}`);
}

export function getEvalRunResults(id: number, errorsOnly?: boolean): Promise<EvalRunResult[]> {
  const qs = errorsOnly ? "?errors_only=true" : "";
  return request(`/runs/${id}/results${qs}`);
}

export function deleteEvalRun(id: number): Promise<void> {
  return request(`/runs/${id}`, { method: "DELETE" });
}

export function refreshEvalRunReport(id: number): Promise<EvalRunDetail> {
  return request(`/runs/${id}/refresh-report`, { method: "POST" });
}

export function cancelEvalRun(): Promise<{ cancelled: boolean; running: boolean }> {
  return request("/runs/cancel", { method: "POST" });
}

export interface ReplayLogEntry {
  stage: string;
  message: string;
  level: "info" | "success" | "warn" | "error";
}

export function replayEmailPipeline(emailId: number): Promise<{ logs: ReplayLogEntry[] }> {
  return request(`/cache/emails/${emailId}/replay`);
}

export function bootstrapGroups(): Promise<{ created: number; matched: number; labels_linked: number; run_id: number | null }> {
  return request("/bootstrap-groups", { method: "POST" });
}

export interface EvalSettings {
  llm_enabled: boolean;
  llm_provider: string;
  llm_model: string;
}

export function getEvalSettings(): Promise<EvalSettings> {
  return request("/settings");
}

export function setLlmEnabled(enabled: boolean): Promise<EvalSettings> {
  return request(`/settings?llm_enabled=${enabled}`, { method: "POST" });
}

/** Open an SSE stream for a new evaluation run.
 *  Returns the EventSource so the caller can close it on unmount.
 *  When `emailIds` is provided (non-empty), only those cached emails are evaluated
 *  and `maxEmails` is ignored.
 */
export function streamEvalRun(name?: string, maxEmails?: number, emailIds?: number[]): EventSource {
  const params = new URLSearchParams();
  if (name) params.set("name", name);
  if (emailIds && emailIds.length > 0) {
    params.set("email_ids", emailIds.join(","));
  } else if (maxEmails && maxEmails > 0) {
    params.set("max_emails", String(maxEmails));
  }
  const qs = params.toString() ? `?${params}` : "";
  return new EventSource(`${BASE}/runs/stream${qs}`);
}
