import { useState, useRef, useEffect, useCallback } from "react";
import { format, subDays, subWeeks, startOfDay } from "date-fns";
import { DayPicker, DateRange } from "react-day-picker";
import "react-day-picker/style.css";
import {
  getScanStreamUrl,
  cancelScanStream,
  getScanRunning,
  getScanProgress,
  getLastScanResult,
} from "../api/client";
import type { ScanJob, ScanResult } from "../types";

interface Props {
  mode?: "loading" | "initial" | "default";
  onScanComplete: (result: ScanResult) => void;
}

type AdvancedPresetKey = "last1day" | "last3days" | "lastweek" | null;
type InitialPresetKey = "last7days" | "last30days" | null;

const ADVANCED_PRESETS: {
  key: Exclude<AdvancedPresetKey, null>;
  label: string;
  getDates: () => { from: Date; to: Date };
}[] = [
  {
    key: "last1day",
    label: "Last 1 Day",
    getDates: () => ({ from: subDays(startOfDay(new Date()), 1), to: new Date() }),
  },
  {
    key: "last3days",
    label: "Last 3 Days",
    getDates: () => ({ from: subDays(startOfDay(new Date()), 3), to: new Date() }),
  },
  {
    key: "lastweek",
    label: "Last Week",
    getDates: () => ({ from: subWeeks(startOfDay(new Date()), 1), to: new Date() }),
  },
];

const INITIAL_PRESETS: {
  key: Exclude<InitialPresetKey, null>;
  label: string;
  description: string;
  getDates: () => { from: Date; to: Date };
}[] = [
  {
    key: "last7days",
    label: "Last 7 Days",
    description: "Recommended for a new journey",
    getDates: () => ({ from: subDays(startOfDay(new Date()), 7), to: new Date() }),
  },
  {
    key: "last30days",
    label: "Last 30 Days",
    description: "Use when you want broader history",
    getDates: () => ({ from: subDays(startOfDay(new Date()), 30), to: new Date() }),
  },
];

const EMAIL_COUNT_OPTIONS = [5, 10, 15, 20, 50, 75, 100, 200, 500];

function jobToScanResult(job: ScanJob): ScanResult {
  return {
    emails_scanned: job.processed_messages,
    emails_matched: job.emails_matched,
    skipped_social_or_promotions: job.skipped_social_or_promotions,
    skipped_not_job_related: job.skipped_not_job_related,
    skipped_message_unavailable: job.skipped_message_unavailable,
    non_job_reason_counts: job.non_job_reason_counts,
    applications_created: job.applications_created,
    applications_updated: job.applications_updated,
    applications_deleted: job.applications_deleted,
    created_application_ids: job.created_application_ids,
    updated_application_ids: job.updated_application_ids,
    total_prompt_tokens: job.total_prompt_tokens,
    total_completion_tokens: job.total_completion_tokens,
    total_estimated_cost: job.total_estimated_cost,
    errors: job.errors,
    cancelled: job.status === "cancelled",
  };
}

export default function ScanButton({ mode = "default", onScanComplete }: Props) {
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState<{
    processed: number;
    total: number;
    currentSubject: string;
  } | null>(null);
  const [dateRange, setDateRange] = useState<DateRange | undefined>(undefined);
  const [selectedPreset, setSelectedPreset] = useState<AdvancedPresetKey>(null);
  const [selectedInitialPreset, setSelectedInitialPreset] = useState<InitialPresetKey>(null);
  const [selectedCount, setSelectedCount] = useState(50);
  const [showOptions, setShowOptions] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const scanInProgressRef = useRef(false);
  const controlsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (controlsRef.current && !controlsRef.current.contains(e.target as Node)) {
        setShowOptions(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    setShowOptions(false);
  }, [mode]);

  useEffect(() => {
    let pollInterval: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;

    async function checkAndPoll() {
      try {
        const { running } = await getScanRunning();
        if (!running || cancelled) return;

        scanInProgressRef.current = true;
        setScanning(true);

        pollInterval = setInterval(async () => {
          if (cancelled) {
            if (pollInterval) clearInterval(pollInterval);
            return;
          }
          try {
            const prog = await getScanProgress();
            if (prog.type === "idle") {
              if (pollInterval) clearInterval(pollInterval);
              scanInProgressRef.current = false;
              setScanning(false);
              setProgress(null);
              try {
                const result = await getLastScanResult();
                if (result) onScanComplete(result);
              } catch {
                // ignore
              }
              return;
            }
            if (prog.type === "progress") {
              setProgress({
                processed: prog.processed,
                total: prog.total,
                currentSubject: prog.current_subject || "",
              });
            }
          } catch {
            if (pollInterval) clearInterval(pollInterval);
            scanInProgressRef.current = false;
            setScanning(false);
            setProgress(null);
          }
        }, 1000);
      } catch {
        // getScanRunning failed
      }
    }

    checkAndPoll();

    return () => {
      cancelled = true;
      if (pollInterval) clearInterval(pollInterval);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const formatDate = (date: Date | undefined) => {
    if (!date) return "";
    return format(date, "yyyy-MM-dd");
  };

  const formatRangeLabel = (range: DateRange | undefined) => {
    if (!range?.from) return "Choose a date range";
    const from = format(range.from, "MMM dd, yyyy");
    const to = range.to ? format(range.to, "MMM dd, yyyy") : format(new Date(), "MMM dd, yyyy");
    return `${from} -> ${to}`;
  };

  const handleJobPayload = useCallback((job: ScanJob) => {
    setProgress({
      processed: job.processed_messages,
      total: job.total_messages,
      currentSubject: job.current_subject || "",
    });

    if (job.status === "completed" || job.status === "cancelled") {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      scanInProgressRef.current = false;
      setScanning(false);
      setProgress(null);
      onScanComplete(jobToScanResult(job));
      return;
    }

    if (job.status === "failed") {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      scanInProgressRef.current = false;
      setScanning(false);
      setProgress(null);
      setError(job.errors[0] || "Scan failed");
    }
  }, [onScanComplete]);

  const handleStreamMessage = useCallback((payload: unknown) => {
    if (!payload || typeof payload !== "object") {
      return;
    }

    const maybeLegacy = payload as {
      type?: string;
      processed?: number;
      total?: number;
      current_subject?: string;
      result?: ScanResult;
      message?: string;
    };
    if (maybeLegacy.type === "progress") {
      setProgress({
        processed: maybeLegacy.processed || 0,
        total: maybeLegacy.total || 0,
        currentSubject: maybeLegacy.current_subject || "",
      });
      return;
    }
    if (maybeLegacy.type === "complete" && maybeLegacy.result) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      scanInProgressRef.current = false;
      setScanning(false);
      setProgress(null);
      onScanComplete(maybeLegacy.result);
      return;
    }
    if (maybeLegacy.type === "error") {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      scanInProgressRef.current = false;
      setScanning(false);
      setProgress(null);
      setError(maybeLegacy.message || "Scan failed");
      return;
    }

    const maybeJob = payload as Partial<ScanJob>;
    if (
      typeof maybeJob.id === "number" &&
      typeof maybeJob.status === "string" &&
      typeof maybeJob.processed_messages === "number" &&
      typeof maybeJob.total_messages === "number"
    ) {
      handleJobPayload(maybeJob as ScanJob);
    }
  }, [handleJobPayload, onScanComplete]);

  const handleScan = useCallback((options: {
    incremental?: boolean;
    scan_all?: boolean;
    mode?: "incremental" | "full" | "date_range";
    since_date?: string;
    before_date?: string;
    max_emails?: number;
  }) => {
    if (scanInProgressRef.current) return;

    scanInProgressRef.current = true;
    setShowOptions(false);
    setScanning(true);
    setError("");
    setProgress(null);

    const url = getScanStreamUrl({
      max_emails: options.max_emails ?? (options.incremental ? 100 : undefined),
      incremental: options.incremental,
      scan_all: options.scan_all,
      mode: options.mode,
      since_date: options.since_date,
      before_date: options.before_date,
    });

    const es = new EventSource(url, { withCredentials: true });
    eventSourceRef.current = es;

    const parseEvent = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data);
        handleStreamMessage(data);
      } catch {
        // ignore parse errors
      }
    };

    es.onmessage = parseEvent;
    es.addEventListener("job", parseEvent as EventListener);
    es.addEventListener("done", parseEvent as EventListener);
    es.addEventListener("error", ((event: Event) => {
      const messageEvent = event as MessageEvent<string>;
      if (!messageEvent.data) {
        return;
      }
      parseEvent(messageEvent);
    }) as EventListener);

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      if (scanInProgressRef.current) {
        const pollInterval = setInterval(async () => {
          try {
            const prog = await getScanProgress();
            if (prog.type === "idle") {
              clearInterval(pollInterval);
              scanInProgressRef.current = false;
              setScanning(false);
              setProgress(null);
              try {
                const result = await getLastScanResult();
                if (result) onScanComplete(result);
              } catch {
                // ignore
              }
              return;
            }
            if (prog.type === "progress") {
              setProgress({
                processed: prog.processed,
                total: prog.total,
                currentSubject: prog.current_subject || "",
              });
            }
          } catch {
            clearInterval(pollInterval);
            scanInProgressRef.current = false;
            setScanning(false);
            setProgress(null);
            setError("Connection lost during scan");
          }
        }, 1000);
      }
    };
  }, [handleStreamMessage, onScanComplete]);

  const handleCancel = async () => {
    try {
      await cancelScanStream();
    } catch {
      // ignore
    }

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    scanInProgressRef.current = false;
    setScanning(false);
    setProgress(null);
  };

  const handleScanRange = () => {
    if (!dateRange?.from) return;
    setSelectedPreset(null);
    setSelectedInitialPreset(null);
    handleScan({
      mode: "date_range",
      since_date: formatDate(dateRange.from),
      before_date: dateRange.to ? formatDate(dateRange.to) : formatDate(new Date()),
    });
  };

  const handleScanCount = () => {
    handleScan({ mode: "full", scan_all: true, max_emails: selectedCount });
  };

  const handleScanNew = () => {
    handleScan({ incremental: true });
  };

  const handlePresetScan = (preset: typeof ADVANCED_PRESETS[number]) => {
    const dates = preset.getDates();
    setSelectedPreset(preset.key);
    setSelectedInitialPreset(null);
    setDateRange({ from: dates.from, to: dates.to });
    handleScan({
      mode: "date_range",
      since_date: formatDate(dates.from),
      before_date: formatDate(dates.to),
    });
  };

  const handleInitialPresetSelect = (preset: typeof INITIAL_PRESETS[number]) => {
    const dates = preset.getDates();
    setSelectedInitialPreset(preset.key);
    setSelectedPreset(null);
    setDateRange({ from: dates.from, to: dates.to });
  };

  if (mode === "loading") {
    return (
      <div className="flex items-center gap-2">
        <div className="h-10 w-32 animate-pulse rounded-md bg-gray-200" />
        <div className="h-10 w-24 animate-pulse rounded-md bg-gray-200" />
      </div>
    );
  }

  if (scanning) {
    return (
      <div className="flex items-center gap-3 rounded-lg bg-white px-4 py-2 shadow">
        <svg className="h-5 w-5 animate-spin text-indigo-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
        <span className="text-sm text-gray-700">
          {progress ? `Scanning: ${progress.processed}/${progress.total}` : "Starting scan..."}
        </span>
        {progress?.currentSubject && (
          <span className="max-w-[200px] truncate text-xs text-gray-400" title={progress.currentSubject}>
            {progress.currentSubject}
          </span>
        )}
        <button
          onClick={handleCancel}
          className="ml-2 rounded bg-red-500 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-red-600"
        >
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-2">
      {error && (
        <div className="rounded bg-red-50 px-3 py-1 text-sm text-red-600">{error}</div>
      )}

      <div className="relative flex flex-col items-end gap-2" ref={controlsRef}>
        {mode === "initial" ? (
          <div className="flex flex-col items-end gap-1">
            <button
              type="button"
              onClick={() => setShowOptions((current) => !current)}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700"
            >
              Initial Scan
            </button>
            <p className="max-w-xs text-right text-xs text-amber-700">
              New journey: choose a time range for the first import.
            </p>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowOptions((current) => !current)}
              className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
            >
              Advanced Scan
            </button>
            <button
              type="button"
              onClick={handleScanNew}
              className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700"
            >
              Scan New
            </button>
          </div>
        )}

        {showOptions && (
          <div className="absolute right-0 top-full z-50 mt-2 w-96 max-w-[92vw] rounded-xl border border-gray-200 bg-white p-4 shadow-xl">
            {mode === "initial" ? (
              <div className="space-y-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Choose initial import range</h3>
                  <p className="mt-1 text-xs leading-5 text-gray-500">
                    Start with a limited time range. After this import, this journey can use Scan New.
                  </p>
                </div>

                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {INITIAL_PRESETS.map((preset) => (
                    <button
                      key={preset.key}
                      type="button"
                      onClick={() => handleInitialPresetSelect(preset)}
                      className={`rounded-lg border px-3 py-3 text-left transition-colors ${
                        selectedInitialPreset === preset.key
                          ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                          : "border-gray-200 bg-white text-gray-700 hover:bg-gray-50"
                      }`}
                    >
                      <div className="text-sm font-medium">{preset.label}</div>
                      <div className="mt-1 text-xs text-gray-500">{preset.description}</div>
                    </button>
                  ))}
                </div>

                <div className="rounded-lg border border-gray-200 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Custom Range</div>
                    <button
                      type="button"
                      onClick={() => {
                        setDateRange(undefined);
                        setSelectedInitialPreset(null);
                      }}
                      className="text-xs text-gray-500 hover:text-gray-700"
                    >
                      Reset
                    </button>
                  </div>

                  <DayPicker
                    mode="range"
                    selected={dateRange}
                    onSelect={(range) => {
                      setDateRange(range);
                      setSelectedInitialPreset(null);
                    }}
                    captionLayout="dropdown"
                    fromYear={2020}
                    toYear={new Date().getFullYear()}
                    disabled={{ after: new Date() }}
                    numberOfMonths={1}
                    showOutsideDays
                  />

                  <div className="mt-3 flex items-center justify-between border-t border-gray-100 pt-3">
                    <span className="pr-4 text-xs text-gray-500">{formatRangeLabel(dateRange)}</span>
                    <button
                      type="button"
                      onClick={handleScanRange}
                      disabled={!dateRange?.from}
                      className="rounded-md bg-indigo-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      Scan Selected Range
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Advanced Scan</h3>
                  <p className="mt-1 text-xs leading-5 text-gray-500">
                    Run a targeted rescan for a recent range, a custom range, or a fixed number of recent emails.
                  </p>
                </div>

                <div>
                  <div className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">Quick Ranges</div>
                  <div className="flex flex-wrap gap-2">
                    {ADVANCED_PRESETS.map((preset) => (
                      <button
                        key={preset.key}
                        type="button"
                        onClick={() => handlePresetScan(preset)}
                        className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                          selectedPreset === preset.key
                            ? "border-indigo-300 bg-indigo-100 text-indigo-700"
                            : "border-gray-300 bg-white text-gray-600 hover:bg-gray-50"
                        }`}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="rounded-lg border border-gray-200 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Custom Range</div>
                    <button
                      type="button"
                      onClick={() => {
                        setDateRange(undefined);
                        setSelectedPreset(null);
                      }}
                      className="text-xs text-gray-500 hover:text-gray-700"
                    >
                      Reset
                    </button>
                  </div>

                  <DayPicker
                    mode="range"
                    selected={dateRange}
                    onSelect={(range) => {
                      setDateRange(range);
                      setSelectedPreset(null);
                    }}
                    captionLayout="dropdown"
                    fromYear={2020}
                    toYear={new Date().getFullYear()}
                    disabled={{ after: new Date() }}
                    numberOfMonths={1}
                    showOutsideDays
                  />

                  <div className="mt-3 flex items-center justify-between border-t border-gray-100 pt-3">
                    <span className="pr-4 text-xs text-gray-500">{formatRangeLabel(dateRange)}</span>
                    <button
                      type="button"
                      onClick={handleScanRange}
                      disabled={!dateRange?.from}
                      className="rounded-md bg-indigo-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      Scan Range
                    </button>
                  </div>
                </div>

                <div className="flex items-end gap-2">
                  <label className="flex-1">
                    <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">
                      Scan Latest N Emails
                    </span>
                    <select
                      value={selectedCount}
                      onChange={(e) => setSelectedCount(Number(e.target.value))}
                      className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    >
                      {EMAIL_COUNT_OPTIONS.map((n) => (
                        <option key={n} value={n}>
                          {n} emails
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    onClick={handleScanCount}
                    className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700"
                  >
                    Scan Latest
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
