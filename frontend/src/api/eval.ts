/**
 * API client for evaluation endpoints.
 */

import type {
  CachedEmailDetail,
  CachedEmailListResponse,
  CacheDownloadRequest,
  CacheDownloadResult,
  CacheStats,
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
}): Promise<CachedEmailListResponse> {
  const qs = new URLSearchParams();
  if (params.page) qs.set("page", String(params.page));
  if (params.page_size) qs.set("page_size", String(params.page_size));
  if (params.review_status) qs.set("review_status", params.review_status);
  if (params.search) qs.set("search", params.search);
  return request(`/cache/emails?${qs}`);
}

export function getCachedEmail(id: number): Promise<CachedEmailDetail> {
  return request(`/cache/emails/${id}`);
}

// ── Labels ───────────────────────────────────────────────

export function getLabel(cachedEmailId: number): Promise<EvalLabel | null> {
  return request(`/labels/${cachedEmailId}`);
}

export function upsertLabel(cachedEmailId: number, data: EvalLabelInput): Promise<EvalLabel> {
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

export function listGroups(): Promise<EvalApplicationGroup[]> {
  return request("/groups");
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

/** Open an SSE stream for a new evaluation run.
 *  Returns the EventSource so the caller can close it on unmount.
 */
export function streamEvalRun(name?: string): EventSource {
  const qs = name ? `?name=${encodeURIComponent(name)}` : "";
  return new EventSource(`${BASE}/runs/stream${qs}`);
}
