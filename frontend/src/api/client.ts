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

// ── Stats ────────────────────────────────────────────────

export async function getStats(): Promise<Stats> {
  return request<Stats>("/stats");
}

// ── Export ────────────────────────────────────────────────

export function getExportUrl(format: "csv" | "excel"): string {
  return `${BASE}/export?format=${format}`;
}
