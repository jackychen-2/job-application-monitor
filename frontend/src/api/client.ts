/**
 * API client — typed fetch wrapper for the FastAPI backend.
 * In dev mode, Vite proxies /api to http://localhost:8000.
 */

import type {
  Application,
  ApplicationCreate,
  ApplicationDetail,
  ApplicationListResponse,
  ApplicationMergeEvent,
  SplitApplicationRequest,
  SplitApplicationResult,
  ApplicationUpdate,
  AccountDetails,
  AuthUser,
  DashboardData,
  DeleteAccountResponse,
  FlowData,
  Journey,
  JourneyCreateRequest,
  JourneyDeleteResult,
  JourneyUpdateRequest,
  LinkedEmail,
  PendingReviewEmail,
  CreateScanJobResult,
  ScanResult,
  ScanJob,
  ScanJobMode,
  ScanJobStepResult,
  ScanState,
  Stats,
  UnmergeApplicationResult,
} from "../types";

const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim().replace(/\/+$/, "") ?? "";
const BASE = configuredBase || "/api";
const inflightGetRequests = new Map<string, Promise<unknown>>();

type RequestOptions = RequestInit & {
  timeoutMs?: number;
};

async function performRequest<T>(path: string, init?: RequestOptions): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const timeoutMs = init?.timeoutMs ?? 15000;
  const timeoutController = new AbortController();
  const timeoutId = window.setTimeout(() => {
    timeoutController.abort(new DOMException("Request timed out", "AbortError"));
  }, timeoutMs);

  try {
    const res = await fetch(`${BASE}${path}`, {
      credentials: "include",
      ...init,
      headers,
      signal: init?.signal ?? timeoutController.signal,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`API ${res.status}: ${body}`);
    }
    // 204 No Content
    if (res.status === 204) return undefined as unknown as T;
    return res.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`API request timed out after ${timeoutMs}ms`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  if (method !== "GET") {
    return performRequest<T>(path, init);
  }

  const key = `${method}:${BASE}${path}`;
  const existing = inflightGetRequests.get(key);
  if (existing) {
    return existing as Promise<T>;
  }

  const promise = performRequest<T>(path, init).finally(() => {
    inflightGetRequests.delete(key);
  });
  inflightGetRequests.set(key, promise as Promise<unknown>);
  return promise;
}

// ── Auth ────────────────────────────────────────────────

export async function authMe(): Promise<AuthUser> {
  return request<AuthUser>("/auth/me", { timeoutMs: 8000 });
}

export function startGoogleLogin(): void {
  window.location.href = `${BASE}/auth/google/start`;
}

export async function logout(): Promise<{ status: string }> {
  return request<{ status: string }>("/auth/logout", { method: "POST" });
}

export async function getAccount(): Promise<AccountDetails> {
  return request<AccountDetails>("/auth/account", { timeoutMs: 8000 });
}

export async function updateProfile(data: {
  display_name: string | null;
}): Promise<AccountDetails> {
  return request<AccountDetails>("/auth/profile", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteAccount(): Promise<DeleteAccountResponse> {
  return request<DeleteAccountResponse>("/auth/account", { method: "DELETE" });
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

export async function listAllApplications(params: {
  status?: string;
  company?: string;
  sort_by?: string;
  sort_order?: string;
  maxItems?: number;
} = {}): Promise<Application[]> {
  const pageSize = 100;
  const maxItems = params.maxItems ?? 1000;
  const items: Application[] = [];
  let page = 1;

  while (items.length < maxItems) {
    const response = await listApplications({
      ...params,
      page,
      page_size: pageSize,
    });
    items.push(...response.items);

    if (response.items.length < pageSize || items.length >= response.total) {
      break;
    }
    page += 1;
  }

  return items.slice(0, maxItems);
}

export async function getApplication(id: number): Promise<ApplicationDetail> {
  return request<ApplicationDetail>(`/applications/${id}`);
}

export async function getMergeCandidates(excludeId?: number): Promise<Application[]> {
  const qs = new URLSearchParams();
  if (excludeId != null) qs.set("exclude_id", String(excludeId));
  return request<Application[]>(`/applications/merge-candidates?${qs.toString()}`);
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

export async function getApplicationMergeEvents(id: number): Promise<ApplicationMergeEvent[]> {
  return request<ApplicationMergeEvent[]>(`/applications/${id}/merge-events`);
}

export async function unmergeApplication(
  targetId: number,
  mergeEventId: number,
): Promise<UnmergeApplicationResult> {
  return request<UnmergeApplicationResult>(`/applications/${targetId}/unmerge/${mergeEventId}`, {
    method: "POST",
  });
}

export async function splitApplication(
  sourceId: number,
  data: SplitApplicationRequest,
): Promise<SplitApplicationResult> {
  return request<SplitApplicationResult>(`/applications/${sourceId}/split`, {
    method: "POST",
    body: JSON.stringify(data),
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
  incremental?: boolean;
  scan_all?: boolean;
  mode?: ScanJobMode;
  since_date?: string;
  before_date?: string;
}): Promise<{ message: string; max_emails: number }> {
  const qs = new URLSearchParams();
  if (options?.max_emails !== undefined) qs.set("max_emails", String(options.max_emails));
  if (options?.incremental !== undefined) qs.set("incremental", String(options.incremental));
  if (options?.scan_all) qs.set("scan_all", "true");
  if (options?.mode) qs.set("mode", options.mode);
  if (options?.since_date) qs.set("since_date", options.since_date);
  if (options?.before_date) qs.set("before_date", options.before_date);
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
  scan_all?: boolean;
  mode?: ScanJobMode;
  since_date?: string;  // YYYY-MM-DD
  before_date?: string; // YYYY-MM-DD
}): string {
  const params = new URLSearchParams();
  if (options.max_emails !== undefined) params.set("max_emails", String(options.max_emails));
  if (options.incremental !== undefined) params.set("incremental", String(options.incremental));
  if (options.scan_all) params.set("scan_all", "true");
  if (options.mode) params.set("mode", options.mode);
  if (options.since_date) params.set('since_date', options.since_date);
  if (options.before_date) params.set('before_date', options.before_date);
  return `${BASE}/scan/stream?${params.toString()}`;
}

export function getScanJobStreamUrl(jobId: number): string {
  return `${BASE}/scan/jobs/${jobId}/stream`;
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

export async function createScanJob(options?: {
  max_emails?: number;
  mode?: ScanJobMode;
  scan_all?: boolean;
  since_date?: string;
  before_date?: string;
}): Promise<CreateScanJobResult> {
  const qs = new URLSearchParams();
  if (options?.max_emails !== undefined) qs.set("max_emails", String(options.max_emails));
  if (options?.mode) qs.set("mode", options.mode);
  if (options?.scan_all) qs.set("scan_all", "true");
  if (options?.since_date) qs.set("since_date", options.since_date);
  if (options?.before_date) qs.set("before_date", options.before_date);
  const query = qs.toString() ? `?${qs}` : "";
  return request<CreateScanJobResult>(`/scan/jobs${query}`, { method: "POST" });
}

export async function getActiveScanJob(): Promise<ScanJob | null> {
  return request<ScanJob | null>("/scan/jobs/active");
}

export async function getScanJob(jobId: number): Promise<ScanJob> {
  return request<ScanJob>(`/scan/jobs/${jobId}`);
}

export async function stepScanJob(jobId: number): Promise<ScanJobStepResult> {
  return request<ScanJobStepResult>(`/scan/jobs/${jobId}/step`, { method: "POST" });
}

export async function cancelScanJob(jobId: number): Promise<ScanJob> {
  return request<ScanJob>(`/scan/jobs/${jobId}/cancel`, { method: "POST" });
}

// ── Stats ────────────────────────────────────────────────

export async function getStats(): Promise<Stats> {
  return request<Stats>("/stats");
}

export async function getDashboardData(): Promise<DashboardData> {
  return request<DashboardData>("/stats/dashboard");
}

export async function getFlowData(): Promise<FlowData> {
  return request<FlowData>("/stats/flow");
}

// ── Journeys ─────────────────────────────────────────────

export async function listJourneys(): Promise<Journey[]> {
  return request<Journey[]>("/journeys");
}

export async function createJourney(data: JourneyCreateRequest): Promise<Journey> {
  return request<Journey>("/journeys", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function activateJourney(journeyId: number): Promise<Journey> {
  return request<Journey>(`/journeys/${journeyId}/activate`, {
    method: "POST",
  });
}

export async function renameJourney(journeyId: number, data: JourneyUpdateRequest): Promise<Journey> {
  return request<Journey>(`/journeys/${journeyId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteJourney(journeyId: number): Promise<JourneyDeleteResult> {
  return request<JourneyDeleteResult>(`/journeys/${journeyId}`, {
    method: "DELETE",
  });
}
