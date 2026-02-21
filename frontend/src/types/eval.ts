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
  predicted_company: string | null;
  predicted_job_title: string | null;
  predicted_status: string | null;
  predicted_application_group: number | null;
  predicted_application_group_display: string | null;
  predicted_confidence: number | null;
}

export interface CachedEmailListResponse {
  items: CachedEmail[];
  total: number;
  page: number;
  page_size: number;
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

export interface EvalLabel {
  id: number;
  cached_email_id: number;
  is_job_related: boolean | null;
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
}

export interface EvalLabelInput {
  is_job_related?: boolean | null;
  correct_company?: string | null;
  correct_job_title?: string | null;
  correct_status?: string | null;
  correct_recruiter_name?: string | null;
  correct_date_applied?: string | null;
  correct_application_group_id?: number | null;
  notes?: string | null;
  review_status?: string;
}

export interface EvalApplicationGroup {
  id: number;
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
