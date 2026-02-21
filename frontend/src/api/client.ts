/**
 * API client — typed fetch wrapper for the FastAPI backend.
 * In dev mode, Vite proxies /api to http://localhost:8000.
 */

import type {
  Application,
  ApplicationCreate,
  ApplicationDetail,
  ApplicationListResponse,
  ApplicationUpdate,
  FlowData,
  LinkedEmail,
  PendingReviewEmail,
  ScanResult,
  ScanState,
  Stats,
} from "../types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

// ── Applications ─────────────────────────────────────────

export async function listApplications(params: {
  page?: number;
  page_size?: number;
  status?: string;
  company?: string;
  sort_by?: string;
  sort_order?: string;
}): Promise<ApplicationListResponse> {
  const qs = new URLSearchParams();
  if (params.page) qs.set("page", String(params.page));
  if (params.page_size) qs.set("page_size", String(params.page_size));
  if (params.status) qs.set("status", params.status);
  if (params.company) qs.set("company", params.company);
  if (params.sort_by) qs.set("sort_by", params.sort_by);
  if (params.sort_order) qs.set("sort_order", params.sort_order);
  return request<ApplicationListResponse>(`/applications?${qs}`);
}

export async function getApplication(id: number): Promise<ApplicationDetail> {
  return request<ApplicationDetail>(`/applications/${id}`);
}

export async function createApplication(data: ApplicationCreate): Promise<Application> {
  return request<Application>("/applications", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateApplication(
  id: number,
  data: ApplicationUpdate
): Promise<Application> {
  return request<Application>(`/applications/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteApplication(id: number): Promise<void> {
  return request<void>(`/applications/${id}`, { method: "DELETE" });
}

export async function getApplicationEmails(id: number): Promise<LinkedEmail[]> {
  return request<LinkedEmail[]>(`/applications/${id}/emails`);
}

export async function mergeApplications(targetId: number, sourceId: number): Promise<Application> {
  return request<Application>(`/applications/${targetId}/merge`, {
    method: "POST",
    body: JSON.stringify({ source_application_id: sourceId }),
  });
}

// ── Emails (review/linking) ──────────────────────────────

export async function getPendingReviewEmails(): Promise<PendingReviewEmail[]> {
  return request<PendingReviewEmail[]>("/emails/pending-review");
}

export async function linkEmail(emailId: number, applicationId: number): Promise<LinkedEmail> {
  return request<LinkedEmail>(`/emails/${emailId}/link`, {
    method: "PATCH",
    body: JSON.stringify({ application_id: applicationId }),
  });
}

export async function unlinkEmail(emailId: number): Promise<LinkedEmail> {
  return request<LinkedEmail>(`/emails/${emailId}/link`, { method: "DELETE" });
}

export async function dismissReview(emailId: number): Promise<void> {
  return request<void>(`/emails/${emailId}/dismiss-review`, { method: "POST" });
}

// ── Scan ─────────────────────────────────────────────────

export async function triggerScan(options?: {
  max_emails?: number;
  scan_all?: boolean;
}): Promise<{ message: string; max_emails: number }> {
  const qs = new URLSearchParams();
  if (options?.max_emails) qs.set("max_emails", String(options.max_emails));
  if (options?.scan_all) qs.set("scan_all", "true");
  const query = qs.toString() ? `?${qs}` : "";
  return request<{ message: string; max_emails: number }>(`/scan${query}`, { method: "POST" });
}

export async function getScanStatus(): Promise<ScanState | null> {
  return request<ScanState | null>("/scan/status");
}

export async function getScanRunning(): Promise<{ running: boolean }> {
  return request<{ running: boolean }>("/scan/running");
}

export async function getLastScanResult(): Promise<ScanResult | null> {
  return request<ScanResult | null>("/scan/last-result");
}

export async function cancelScan(): Promise<{ message: string }> {
  return request<{ message: string }>("/scan/cancel", { method: "POST" });
}

// ── SSE Scan Stream ──────────────────────────────────────

export function getScanStreamUrl(options: {
  max_emails?: number;
  incremental?: boolean;
  since_date?: string;  // YYYY-MM-DD
  before_date?: string; // YYYY-MM-DD
}): string {
  const params = new URLSearchParams();
  if (options.max_emails) params.set('max_emails', String(options.max_emails));
  if (options.incremental) params.set('incremental', 'true');
  if (options.since_date) params.set('since_date', options.since_date);
  if (options.before_date) params.set('before_date', options.before_date);
  return `${BASE}/scan/stream?${params.toString()}`;
}

export async function cancelScanStream(): Promise<{ message: string }> {
  return request<{ message: string }>("/scan/stream/cancel", { method: "POST" });
}

export async function getScanProgress(): Promise<{
  type: string;
  processed: number;
  total: number;
  current_subject: string;
  status: string;
}> {
  return request("/scan/progress");
}

// ── Stats ────────────────────────────────────────────────

export async function getStats(): Promise<Stats> {
  return request<Stats>("/stats");
}

export async function getFlowData(): Promise<FlowData> {
  return request<FlowData>("/stats/flow");
}
