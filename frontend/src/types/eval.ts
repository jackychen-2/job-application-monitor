/** TypeScript interfaces for the evaluation framework. */

export interface CachedEmail {
  id: number;
  uid: number;
  email_account: string;
  email_folder: string;
  gmail_message_id: string | null;
  gmail_thread_id: string | null;
  subject: string | null;
  sender: string | null;
  email_date: string | null;
  body_text: string | null;
  fetched_at: string;
  review_status: string | null;
}

export interface CachedEmailDetail extends CachedEmail {
  predicted_is_job_related: boolean | null;
  predicted_email_category: string | null; // "job_application" | "not_job_related"
  predicted_company: string | null;
  predicted_job_title: string | null;
  predicted_status: string | null;
  predicted_application_group: number | null;
  predicted_application_group_display: string | null;
  predicted_confidence: number | null;
  decision_log_json: string | null; // step-by-step log from the actual eval run
}

export interface CachedEmailListResponse {
  items: CachedEmail[];
  total: number;
  page: number;
  page_size: number;
}

export interface EmailPredictionRun {
  run_id: number;
  run_name: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface CacheStats {
  total_cached: number;
  total_labeled: number;
  total_unlabeled: number;
  total_skipped: number;
  date_range_start: string | null;
  date_range_end: string | null;
}

export interface CacheDownloadRequest {
  since_date?: string;
  before_date?: string;
  max_count?: number;
}

export interface CacheDownloadResult {
  new_emails: number;
  skipped_duplicates: number;
  errors: number;
  total_fetched: number;
}

// ── Correction taxonomy ──────────────────────────────────

export interface ErrorTypeOption {
  key: string;
  label: string;
  desc: string;
}

export const CORRECTION_ERROR_TYPES: Record<string, ErrorTypeOption[]> = {
  company: [
    { key: "sender_domain_fallback",  label: "Sender-domain fallback",       desc: "Pipeline used the email domain instead of the real company name" },
    { key: "linkedin_inmail",          label: "LinkedIn InMail",               desc: "Sender is linkedin.com; actual hiring company is in subject/body" },
    { key: "ats_platform_sender",      label: "ATS platform sender",           desc: "Greenhouse / Lever / Workday sent the email, not the company" },
    { key: "recruiter_outreach",       label: "Third-party recruiter",         desc: "Recruiting agency email; hiring company is their client" },
    { key: "wrong_regex_match",        label: "Wrong regex match",             desc: "Subject regex latched onto the wrong token" },
    { key: "company_alias",            label: "Company alias / parent name",   desc: "Different legal/brand name (e.g. Alphabet vs Google)" },
    { key: "no_company_signal",        label: "No company signal in email",    desc: "Email has no extractable company name" },
  ],
  job_title: [
    { key: "title_too_generic",        label: "Title too generic",             desc: "Extracted title is too vague (e.g. just 'Engineer')" },
    { key: "title_includes_junk",      label: "Title includes extra tokens",   desc: "Regex captured surrounding words along with the title" },
    { key: "no_title_signal",          label: "No explicit title in email",    desc: "Email never states the job title explicitly" },
    { key: "wrong_pattern_phase",      label: "Wrong extraction phase",        desc: "Title came from a pattern/phase that was not the best match" },
  ],
  status: [
    { key: "soft_rejection_missed",    label: "Soft rejection not detected",   desc: "Polite 'keep your resume on file' was not caught" },
    { key: "on_hold_not_rejection",    label: "'On hold' = effective rejection", desc: "Position on hold; pipeline did not treat it as rejection" },
    { key: "wrong_keyword_matched",    label: "Wrong keyword fired",           desc: "A keyword matched a status that does not apply" },
    { key: "status_ambiguous",         label: "Status genuinely ambiguous",    desc: "Email could reasonably be interpreted multiple ways" },
  ],
  classification: [
    { key: "false_pos_newsletter",     label: "Newsletter / job alert",        desc: "Email is a digest or newsletter, not an application confirmation" },
    { key: "false_pos_verification",   label: "Security / verification email", desc: "OTP, password reset, or identity verification" },
    { key: "false_pos_recruiter",      label: "Recruiter cold outreach",       desc: "Recruiter inquiry — no application was submitted" },
    { key: "false_neg_no_keywords",    label: "Job email missing keywords",    desc: "Genuine job email but lacked signal keywords" },
  ],
  application_group: [
    { key: "same_app_split",           label: "Same application split",        desc: "Emails from one application split into multiple predicted groups" },
    { key: "different_apps_merged",    label: "Different applications merged", desc: "Emails from distinct applications merged into one predicted group" },
    { key: "thread_mismatch",          label: "Wrong thread merged",           desc: "Reply to a different job was merged with this application" },
    { key: "company_name_variant",     label: "Company name variant",          desc: "Predicted group used a different company name spelling/alias" },
  ],
  other: [
    { key: "other",                    label: "Other (see reason field)",      desc: "None of the above — fill in the reason text" },
  ],
};

// All field categories that can bear a correction
export type CorrectionField = "company" | "job_title" | "status" | "classification" | "application_group";

export interface CorrectionEntry {
  field: string;
  predicted: string | boolean | null;
  corrected: string | boolean | null;
  error_type: string | null;
  evidence: string | null;   // text from subject/body that supports the correction
  reason: string | null;     // free-text explanation of why the prediction failed
  at: string;                // ISO timestamp
}

export interface CorrectionEntryInput {
  field: string;
  predicted?: string | null;
  corrected?: string | null;
  error_type?: string | null;
  evidence?: string | null;
  reason?: string | null;
}

export type GroupDecisionType =
  | "CONFIRMED"
  | "MERGED_INTO_EXISTING"
  | "SPLIT_FROM_EXISTING"
  | "NEW_GROUP_CREATED"
  | "MARKED_NOT_JOB";

export type GroupingFailureCategory =
  | "KEY_MISMATCH"
  | "OVER_SPLIT"
  | "UNDER_SPLIT"
  | "EXTRACTION_ERROR"
  | "NORMALIZATION_WEAKNESS"
  | "POLICY_MISTAKE";

export interface GroupingAnalysis {
  // ── Section 1: Dedup key analysis ───────────────────────────────────────
  predicted_company: string | null;
  predicted_title: string | null;
  predicted_company_norm: string;
  predicted_title_norm: string;
  predicted_dedup_key: [string, string];

  correct_company: string | null;
  correct_title: string | null;
  correct_company_norm: string;
  correct_title_norm: string;
  correct_dedup_key: [string, string];

  dedup_key_failure: "company" | "title" | "both" | null;
  company_key_matches: boolean;
  title_key_matches: boolean;

  // ── Section 2: Group-ID level ────────────────────────────────────────────
  predicted_group_id: number | null;        // EvalPredictedGroup.id
  correct_group_id: number | null;          // EvalApplicationGroup.id
  group_id_match: boolean;                  // true when cluster membership is consistent
  predicted_group_size: number;             // # emails pipeline put in that predicted group
  correct_group_size: number;               // # labeled emails in correct group

  // ── Section 3: Cluster co-membership ────────────────────────────────────
  co_member_email_ids: number[];
  co_member_subjects: (string | null)[];            // subject line for each co-member email
  co_member_email_dates: (string | null)[];         // ISO send date for each co-member email
  co_member_count: number;
  co_member_predicted_group_ids: (number | null)[]; // predicted groups for each co-member
  co_member_predicted_group_names: (string | null)[]; // "#ID Company — Title" for each

  // ── Section 4: Decision classification ──────────────────────────────────
  group_decision_type: GroupDecisionType | null;
  grouping_failure_category: GroupingFailureCategory | null;

  at: string; // ISO timestamp
}

export interface EvalLabel {
  id: number;
  cached_email_id: number;
  is_job_related: boolean | null;
  email_category: string | null; // "job_application" | "not_job_related"
  correct_company: string | null;
  correct_job_title: string | null;
  correct_status: string | null;
  correct_recruiter_name: string | null;
  correct_date_applied: string | null;
  correct_application_group_id: number | null;
  labeler: string;
  labeled_at: string | null;
  notes: string | null;
  review_status: string;
  corrections_json: string | null;         // JSON-encoded CorrectionEntry[]
  grouping_analysis_json: string | null;   // JSON-encoded GroupingAnalysis
}

export interface EvalLabelInput {
  is_job_related?: boolean | null;
  email_category?: string | null; // "job_application" | "not_job_related"
  correct_company?: string | null;
  correct_job_title?: string | null;
  correct_status?: string | null;
  correct_recruiter_name?: string | null;
  correct_date_applied?: string | null;
  correct_application_group_id?: number | null;
  run_id?: number | null; // scopes correction log entries to a specific eval run
  notes?: string | null;
  review_status?: string;
}

export interface EvalApplicationGroup {
  id: number;
  eval_run_id: number | null;
  name: string;
  company: string | null;
  job_title: string | null;
  notes: string | null;
  created_at: string;
  email_count: number;
}

export interface EvalPredictedGroup {
  id: number;
  eval_run_id: number;
  company: string | null;
  job_title: string | null;
  created_at: string;
}

export interface EvalGroupInput {
  name?: string;
  company?: string;
  job_title?: string;
  notes?: string;
  eval_run_id?: number;
}

export interface DropdownOptions {
  companies: string[];
  job_titles: string[];
  statuses: string[];
}

export interface EvalRun {
  id: number;
  run_name: string | null;
  started_at: string;
  completed_at: string | null;
  total_emails: number;
  labeled_emails: number;
  classification_accuracy: number | null;
  classification_precision: number | null;
  classification_recall: number | null;
  classification_f1: number | null;
  field_extraction_accuracy: number | null;
  status_detection_accuracy: number | null;
  grouping_ari: number | null;
  grouping_v_measure: number | null;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_estimated_cost: number;
}

export interface EvalRunDetail extends EvalRun {
  report_json: string | null;
  config_snapshot: string | null;
}

export interface EvalRunResult {
  id: number;
  cached_email_id: number;
  predicted_is_job_related: boolean;
  predicted_email_category: string | null;
  predicted_company: string | null;
  predicted_job_title: string | null;
  predicted_status: string | null;
  predicted_application_group_id: number | null;
  predicted_group: EvalPredictedGroup | null;
  predicted_confidence: number | null;
  classification_correct: boolean | null;
  company_correct: boolean | null;
  company_partial: boolean | null;
  job_title_correct: boolean | null;
  status_correct: boolean | null;
  grouping_correct: boolean | null;
  llm_used: boolean;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_usd: number;
  email_subject: string | null;
  email_sender: string | null;
  // Human ground-truth labels
  label_is_job_related: boolean | null;
  label_company: string | null;
  label_job_title: string | null;
  label_status: string | null;
  label_review_status: string | null;
}

// Report JSON structure (parsed from report_json)
export interface EvalReport {
  classification: {
    tp: number; fp: number; tn: number; fn: number;
    accuracy: number; precision: number; recall: number; f1: number; total: number;
  };
  field_company: FieldMetrics;
  field_job_title: FieldMetrics;
  field_status: {
    confusion_matrix: Record<string, Record<string, number>>;
    per_class: Record<string, { precision: number; recall: number; f1: number; support: number }>;
    overall_accuracy: number;
  };
  grouping: {
    ari: number; homogeneity: number; completeness: number; v_measure: number;
    split_error_count: number; merge_error_count: number;
    split_errors: unknown[]; merge_errors: unknown[];
  };
  classification_fp_examples: { email_id: number; subject: string }[];
  classification_fn_examples: { email_id: number; subject: string }[];
  field_error_examples: { email_id: number; subject: string; errors: { field: string; predicted: string; expected: string }[] }[];
}

export interface FieldMetrics {
  exact_match: number;
  partial_match: number;
  wrong: number;
  missing_pred: number;
  missing_label: number;
  total_scored: number;
  exact_accuracy: number;
  partial_accuracy: number;
}
