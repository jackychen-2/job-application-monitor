/** TypeScript interfaces matching the backend Pydantic schemas. */

export interface Application {
  id: number;
  company: string;
  job_title: string | null;
  req_id: string | null;
  email_subject: string | null;
  email_sender: string | null;
  email_date: string | null;
  status: string;
  source: string;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
  email_count: number;  // Number of linked emails in thread
}

export interface StatusHistory {
  id: number;
  old_status: string | null;
  new_status: string;
  change_source: string | null;
  changed_at: string | null;
}

export interface LinkedEmail {
  id: number;
  uid: number;
  subject: string | null;
  sender: string | null;
  email_date: string | null;
  gmail_thread_id: string | null;
  processed_at: string | null;
  link_method: string | null;
  needs_review: boolean;
}

export interface PendingReviewEmail {
  id: number;
  uid: number;
  subject: string | null;
  sender: string | null;
  email_date: string | null;
  application_id: number | null;
  application_company: string | null;
}

export interface ApplicationDetail extends Application {
  status_history: StatusHistory[];
  linked_emails: LinkedEmail[];
  email_count: number;
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
  req_id?: string;
  status?: string;
  notes?: string;
  source?: string;
}

export interface ApplicationUpdate {
  company?: string;
  job_title?: string;
  req_id?: string;
  status?: string;
  notes?: string;
}

export interface ScanResult {
  emails_scanned: number;
  emails_matched: number;
  applications_created: number;
  applications_updated: number;
  applications_deleted: number;
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

export interface StatusTransition {
  from_status: string;
  to_status: string;
  count: number;
}

export interface FlowData {
  status_counts: StatusCount[];
  transitions: StatusTransition[];
  total: number;
}

/** Status value constants */
export const STATUSES = ["Recruiter Reach-out", "已申请", "OA", "面试", "Offer", "Onboarding", "拒绝", "Unknown"] as const;
export type Status = (typeof STATUSES)[number];

/** Status color mapping */
export const STATUS_COLORS: Record<string, string> = {
  "Recruiter Reach-out": "bg-orange-100 text-orange-700",
  已申请: "bg-gray-100 text-gray-700",
  OA: "bg-cyan-100 text-cyan-700",
  面试: "bg-blue-100 text-blue-700",
  Offer: "bg-green-100 text-green-700",
  Onboarding: "bg-teal-100 text-teal-700",
  拒绝: "bg-red-100 text-red-700",
  Unknown: "bg-yellow-100 text-yellow-700",
};
