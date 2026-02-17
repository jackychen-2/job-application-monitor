/** TypeScript interfaces matching the backend Pydantic schemas. */

export interface Application {
  id: number;
  company: string;
  job_title: string | null;
  email_subject: string | null;
  email_sender: string | null;
  email_date: string | null;
  status: string;
  source: string;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface StatusHistory {
  id: number;
  old_status: string | null;
  new_status: string;
  change_source: string | null;
  changed_at: string | null;
}

export interface ApplicationDetail extends Application {
  status_history: StatusHistory[];
}

export interface ApplicationListResponse {
  items: Application[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApplicationCreate {
  company: string;
  job_title?: string;
  status?: string;
  notes?: string;
  source?: string;
}

export interface ApplicationUpdate {
  company?: string;
  job_title?: string;
  status?: string;
  notes?: string;
}

export interface ScanResult {
  emails_scanned: number;
  emails_matched: number;
  applications_created: number;
  applications_updated: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_estimated_cost: number;
  errors: string[];
  cancelled: boolean;
}

export interface ScanState {
  email_account: string;
  email_folder: string;
  last_uid: number;
  last_scan_at: string | null;
}

export interface StatusCount {
  status: string;
  count: number;
}

export interface DailyCost {
  date: string;
  cost: number;
}

export interface DailyCount {
  date: string;
  count: number;
}

export interface Stats {
  total_applications: number;
  status_breakdown: StatusCount[];
  recent_applications: Application[];
  total_emails_scanned: number;
  total_llm_cost: number;
  daily_llm_costs: DailyCost[];
  daily_applications: DailyCount[];
}

/** Status value constants */
export const STATUSES = ["已申请", "面试", "Offer", "拒绝", "Unknown"] as const;
export type Status = (typeof STATUSES)[number];

/** Status color mapping */
export const STATUS_COLORS: Record<string, string> = {
  已申请: "bg-gray-100 text-gray-700",
  面试: "bg-blue-100 text-blue-700",
  Offer: "bg-green-100 text-green-700",
  拒绝: "bg-red-100 text-red-700",
  Unknown: "bg-yellow-100 text-yellow-700",
};
