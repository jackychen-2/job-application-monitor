import { useEffect, useRef, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  getCacheStats,
  listEvalRuns,
  streamEvalRun,
  cancelEvalRun,
  downloadEmails,
  getEvalSettings,
  setLlmEnabled,
  listCachedEmails,
} from "../../api/eval";
import type { EvalSettings } from "../../api/eval";
import type { CacheStats, EvalRun, CacheDownloadResult, CachedEmail } from "../../types/eval";

interface EvalLogEntry {
  message: string;
  level: "info" | "error" | "success";
}

export default function EvalDashboard() {
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [downloading, setDownloading] = useState(false);
  const [downloadResult, setDownloadResult] = useState<CacheDownloadResult | null>(null);
  const [runningEval, setRunningEval] = useState(false);
  const [sinceDate, setSinceDate] = useState("");
  const [beforeDate, setBeforeDate] = useState("");
  const [maxCount, setMaxCount] = useState(500);

  // Eval run options
  const [maxEmails, setMaxEmails] = useState<number>(0); // 0 = all

  // Eval progress state
  const [evalLogs, setEvalLogs] = useState<EvalLogEntry[]>([]);
  const [evalProgress, setEvalProgress] = useState(0);
  const [evalTotal, setEvalTotal] = useState(0);
  const [cancelRequested, setCancelRequested] = useState(false);

  // LLM settings
  const [settings, setSettings] = useState<EvalSettings | null>(null);

  // ── Email selector state ──────────────────────────────
  const [showSelector, setShowSelector] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectorSearch, setSelectorSearch] = useState("");
  const [selectorStatus, setSelectorStatus] = useState("");
  const [selectorEmails, setSelectorEmails] = useState<CachedEmail[]>([]);
  const [selectorTotal, setSelectorTotal] = useState(0);
  const [selectorLoading, setSelectorLoading] = useState(false);

  const esRef = useRef<EventSource | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  const refresh = () => {
    getCacheStats().then(setStats);
    listEvalRuns().then(setRuns);
  };

  useEffect(() => {
    refresh();
    getEvalSettings().then(setSettings).catch(() => {});
  }, []);

  // Auto-scroll log panel to bottom on new entries
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [evalLogs]);

  // Cleanup SSE on unmount
  useEffect(() => {
    return () => { esRef.current?.close(); };
  }, []);

  // ── Fetch emails for selector ─────────────────────────
  const fetchSelectorEmails = useCallback(async () => {
    setSelectorLoading(true);
    try {
      const resp = await listCachedEmails({
        page: 1,
        page_size: 10000,
        search: selectorSearch || undefined,
        review_status: selectorStatus || undefined,
      });
      setSelectorEmails(resp.items);
      setSelectorTotal(resp.total);
    } finally {
      setSelectorLoading(false);
    }
  }, [selectorSearch, selectorStatus]);

  useEffect(() => {
    if (showSelector) fetchSelectorEmails();
  }, [showSelector, fetchSelectorEmails]);

  const handleDownload = async () => {
    setDownloading(true);
    setDownloadResult(null);
    try {
      const result = await downloadEmails({
        since_date: sinceDate || undefined,
        before_date: beforeDate || undefined,
        max_count: maxCount,
      });
      setDownloadResult(result);
      refresh();
    } finally {
      setDownloading(false);
    }
  };

  const handleRunEval = () => {
    setEvalLogs([]);
    setEvalProgress(0);
    setEvalTotal(0);
    setCancelRequested(false);
    setRunningEval(true);

    const ids = selectedIds.size > 0 ? Array.from(selectedIds) : undefined;
    const es = streamEvalRun(undefined, maxEmails > 0 ? maxEmails : undefined, ids);
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "log") {
          setEvalLogs(prev => [...prev, { message: msg.message, level: "info" }]);
          if (msg.total > 0) {
            setEvalProgress(msg.current);
            setEvalTotal(msg.total);
          }
        } else if (msg.type === "done") {
          setEvalLogs(prev => [
            ...prev,
            { message: `✓ Run #${msg.run_id} saved successfully.`, level: "success" },
          ]);
          setRunningEval(false);
          es.close();
          esRef.current = null;
          refresh();
        } else if (msg.type === "cancelled") {
          setEvalLogs(prev => [...prev, { message: "⚠ Evaluation cancelled.", level: "error" }]);
          setRunningEval(false);
          es.close();
          esRef.current = null;
          refresh();
        } else if (msg.type === "error") {
          setEvalLogs(prev => [
            ...prev,
            { message: `✗ Error: ${msg.message}`, level: "error" },
          ]);
          setRunningEval(false);
          es.close();
          esRef.current = null;
        }
      } catch {
        // ignore parse errors on keep-alive comments
      }
    };

    es.onerror = () => {
      setEvalLogs(prev => [
        ...prev,
        { message: "Connection lost — evaluation may still be running on the server.", level: "error" },
      ]);
      setRunningEval(false);
      es.close();
      esRef.current = null;
    };
  };

  const handleCancelEval = async () => {
    setCancelRequested(true);
    setEvalLogs(prev => [...prev, { message: "Cancellation requested — finishing current email…", level: "info" }]);
    try {
      await cancelEvalRun();
    } catch {
      // best-effort
    }
  };

  // ── Selector helpers ──────────────────────────────────
  const toggleEmail = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectPageAll = () => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      selectorEmails.forEach(e => next.add(e.id));
      return next;
    });
  };

  const clearPageAll = () => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      selectorEmails.forEach(e => next.delete(e.id));
      return next;
    });
  };

  const clearAll = () => setSelectedIds(new Set());



  const latestRun = runs[0];
  const progressPct = evalTotal > 0 ? Math.round((evalProgress / evalTotal) * 100) : 0;

  const evalTarget = selectedIds.size > 0
    ? `${selectedIds.size} selected email${selectedIds.size !== 1 ? "s" : ""}`
    : `All (${stats?.total_cached || 0})`;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Evaluation Dashboard</h1>
        <div className="flex gap-2">
          <Link
            to={latestRun ? `/eval/review?run_id=${latestRun.id}` : "/eval/review"}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium"
          >
            Review Emails{latestRun ? ` (Run #${latestRun.id})` : ""}
          </Link>
          <Link to="/eval/runs" className="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 text-sm font-medium">
            All Runs
          </Link>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Cached Emails" value={stats.total_cached} />
          <StatCard label="Labeled" value={stats.total_labeled} color="green" />
          <StatCard label="Unlabeled" value={stats.total_unlabeled} color="yellow" />
          <StatCard label="Skipped" value={stats.total_skipped} color="gray" />
        </div>
      )}

      {/* Label Coverage */}
      {stats && stats.total_cached > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex justify-between text-sm mb-1">
            <span className="text-gray-600">Label Coverage</span>
            <span className="font-medium">{Math.round((stats.total_labeled / stats.total_cached) * 100)}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-3">
            <div
              className="bg-green-500 h-3 rounded-full transition-all"
              style={{ width: `${(stats.total_labeled / stats.total_cached) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* Latest Run Metrics */}
      {latestRun && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Latest Run: {latestRun.run_name}</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <MetricCard label="Classification F1" value={latestRun.classification_f1} />
            <MetricCard label="Field Accuracy" value={latestRun.field_extraction_accuracy} />
            <MetricCard label="Status Accuracy" value={latestRun.status_detection_accuracy} />
            <MetricCard label="Grouping ARI" value={latestRun.grouping_ari} />
          </div>
          <Link to={`/eval/runs/${latestRun.id}`} className="text-blue-600 text-sm hover:underline mt-3 inline-block">
            View full report →
          </Link>
        </div>
      )}

      {/* Download Section */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Download Emails to Cache</h2>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Since Date</label>
            <input type="date" value={sinceDate} onChange={e => setSinceDate(e.target.value)}
              className="border rounded px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Before Date</label>
            <input type="date" value={beforeDate} onChange={e => setBeforeDate(e.target.value)}
              className="border rounded px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Max Count</label>
            <input type="number" value={maxCount} onChange={e => setMaxCount(Number(e.target.value))}
              className="border rounded px-3 py-2 text-sm w-24" min={1} max={5000} />
          </div>
          <button onClick={handleDownload} disabled={downloading}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium">
            {downloading ? "Downloading..." : "Download"}
          </button>
        </div>
        {downloadResult && (
          <div className="mt-3 text-sm text-gray-700 bg-gray-50 p-3 rounded">
            Fetched {downloadResult.total_fetched} emails: {downloadResult.new_emails} new,{" "}
            {downloadResult.skipped_duplicates} duplicates, {downloadResult.errors} errors
          </div>
        )}
      </div>

      {/* Run Evaluation */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-2">Run Evaluation</h2>
        <p className="text-sm text-gray-600 mb-4">
          Re-run the pipeline on cached emails and score against{" "}
          {stats?.total_labeled || 0} labels.
        </p>

        {/* LLM toggle */}
        {settings && (
          <div className="flex items-center gap-3 mb-4 p-3 bg-gray-50 rounded-lg">
            <span className="text-sm text-gray-600">LLM</span>
            <button
              onClick={async () => {
                const updated = await setLlmEnabled(!settings.llm_enabled);
                setSettings(updated);
              }}
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                settings.llm_enabled ? "bg-green-500" : "bg-gray-300"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${
                  settings.llm_enabled ? "translate-x-4" : "translate-x-0"
                }`}
              />
            </button>
            <span className={`text-sm font-medium ${settings.llm_enabled ? "text-green-700" : "text-gray-500"}`}>
              {settings.llm_enabled ? `Enabled (${settings.llm_provider} / ${settings.llm_model})` : "Disabled — rule-based only"}
            </span>
          </div>
        )}

        {/* Email target selector toggle */}
        <div className="mb-4">
          <div className="flex items-center gap-3 mb-2">
            <button
              onClick={() => {
                setShowSelector(s => !s);
                if (showSelector) clearAll();
              }}
              className={`px-3 py-1.5 text-sm rounded-lg border font-medium transition-colors ${
                showSelector
                  ? "bg-violet-100 border-violet-400 text-violet-800"
                  : "border-gray-300 text-gray-600 hover:bg-gray-50"
              }`}
            >
              {showSelector ? "▲ Hide Email Selector" : "▼ Select Specific Emails"}
            </button>
            {selectedIds.size > 0 && (
              <span className="text-sm font-semibold text-violet-700 bg-violet-50 border border-violet-200 px-2 py-0.5 rounded-full">
                {selectedIds.size} selected
              </span>
            )}
            {selectedIds.size > 0 && (
              <button onClick={clearAll} className="text-xs text-gray-400 hover:text-red-500 underline">
                Clear all
              </button>
            )}
          </div>

          {/* Email selector panel */}
          {showSelector && (
            <div className="border border-violet-200 rounded-lg bg-violet-50/30 overflow-hidden">
              {/* Filters row */}
              <div className="flex flex-wrap gap-2 p-3 border-b border-violet-200 bg-white">
                <input
                  type="text"
                  placeholder="Search subject / sender…"
                  value={selectorSearch}
                  onChange={e => setSelectorSearch(e.target.value)}
                  className="border rounded px-3 py-1.5 text-sm flex-1 min-w-48"
                />
                <select
                  value={selectorStatus}
                  onChange={e => setSelectorStatus(e.target.value)}
                  className="border rounded px-3 py-1.5 text-sm"
                >
                  <option value="">All statuses</option>
                  <option value="labeled">Labeled</option>
                  <option value="unlabeled">Unlabeled</option>
                  <option value="skipped">Skipped</option>
                </select>
                <button
                  onClick={selectPageAll}
                  className="px-3 py-1.5 text-xs font-medium bg-violet-600 text-white rounded hover:bg-violet-700"
                >
                  Select page
                </button>
                <button
                  onClick={clearPageAll}
                  className="px-3 py-1.5 text-xs font-medium border border-gray-300 text-gray-600 rounded hover:bg-gray-50"
                >
                  Deselect page
                </button>
              </div>

              {/* Email list */}
              <div className="max-h-72 overflow-y-auto">
                {selectorLoading ? (
                  <div className="py-8 text-center text-sm text-gray-400">Loading…</div>
                ) : selectorEmails.length === 0 ? (
                  <div className="py-8 text-center text-sm text-gray-400">No emails found</div>
                ) : (
                  <table className="min-w-full text-sm">
                    <tbody className="divide-y divide-gray-100">
                      {selectorEmails.map(email => {
                        const checked = selectedIds.has(email.id);
                        return (
                          <tr
                            key={email.id}
                            onClick={() => toggleEmail(email.id)}
                            className={`cursor-pointer transition-colors ${
                              checked ? "bg-violet-50" : "hover:bg-gray-50"
                            }`}
                          >
                            <td className="pl-3 pr-2 py-2 w-8">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleEmail(email.id)}
                                onClick={e => e.stopPropagation()}
                                className="accent-violet-600"
                              />
                            </td>
                            <td className="px-2 py-2 max-w-xs truncate font-medium text-gray-800">
                              {email.subject || "(no subject)"}
                            </td>
                            <td className="px-2 py-2 text-gray-500 truncate max-w-[180px]">
                              {email.sender || "—"}
                            </td>
                            <td className="px-2 py-2 text-gray-400 whitespace-nowrap text-xs">
                              {email.email_date ? new Date(email.email_date).toLocaleDateString() : "—"}
                            </td>
                            <td className="px-3 py-2 w-24">
                              <StatusPill status={email.review_status || "unlabeled"} />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>

              {/* Total count footer */}
              {selectorTotal > 0 && (
                <div className="px-3 py-2 border-t border-violet-200 bg-white text-xs text-gray-500">
                  {selectorTotal} email{selectorTotal !== 1 ? "s" : ""} total
                </div>
              )}
            </div>
          )}
        </div>

        {/* Action buttons row */}
        <div className="flex gap-3 items-center flex-wrap">
          {/* Max emails input — hidden when using selector */}
          {!showSelector || selectedIds.size === 0 ? (
            <div>
              <label className="block text-xs text-gray-500 mb-1">Emails to evaluate</label>
              <input
                type="number"
                value={maxEmails || ""}
                onChange={e => setMaxEmails(Number(e.target.value))}
                placeholder={`All (${stats?.total_cached || 0})`}
                min={1}
                max={stats?.total_cached || undefined}
                disabled={selectedIds.size > 0}
                className="border rounded px-3 py-2 text-sm w-36 disabled:opacity-50 disabled:bg-gray-50"
              />
            </div>
          ) : (
            <div className="flex items-center gap-2 bg-violet-50 border border-violet-200 rounded px-3 py-2">
              <span className="text-sm font-semibold text-violet-800">{selectedIds.size} emails selected</span>
            </div>
          )}

          <div className="flex gap-2 items-end pt-4">
            <button
              onClick={handleRunEval}
              disabled={runningEval || !stats?.total_cached}
              title={`Run evaluation on ${evalTarget}`}
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm font-medium"
            >
              {runningEval
                ? "Running…"
                : selectedIds.size > 0
                ? `▶ Run on ${selectedIds.size} Email${selectedIds.size !== 1 ? "s" : ""}`
                : "▶ Run Evaluation"}
            </button>
            {runningEval && (
              <button
                onClick={handleCancelEval}
                disabled={cancelRequested}
                className="px-4 py-2 bg-red-500 text-white rounded-lg hover:bg-red-600 disabled:opacity-50 text-sm font-medium"
              >
                {cancelRequested ? "Cancelling…" : "Cancel"}
              </button>
            )}
          </div>
        </div>

        {/* Progress bar */}
        {(runningEval || evalLogs.length > 0) && evalTotal > 0 && (
          <div className="mt-4">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>Progress</span>
              <span>{evalProgress} / {evalTotal} ({progressPct}%)</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2.5 overflow-hidden">
              <div
                className={`h-2.5 rounded-full transition-all duration-300 ${
                  cancelRequested ? "bg-red-400" : "bg-green-500"
                }`}
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>
        )}

        {/* Log panel */}
        {evalLogs.length > 0 && (
          <div className="mt-4 bg-gray-900 rounded-lg p-3 max-h-64 overflow-y-auto font-mono text-xs">
            {evalLogs.map((entry, i) => (
              <div
                key={i}
                className={
                  entry.level === "error"
                    ? "text-red-400"
                    : entry.level === "success"
                    ? "text-green-400"
                    : "text-gray-300"
                }
              >
                {entry.message}
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────

function StatCard({ label, value, color = "blue" }: { label: string; value: number; color?: string }) {
  const colors: Record<string, string> = {
    blue: "bg-blue-50 text-blue-700",
    green: "bg-green-50 text-green-700",
    yellow: "bg-yellow-50 text-yellow-700",
    gray: "bg-gray-50 text-gray-700",
  };
  return (
    <div className={`rounded-lg p-4 ${colors[color]}`}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-sm opacity-75">{label}</div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: number | null }) {
  const pct = value !== null ? `${(value * 100).toFixed(1)}%` : "—";
  const color =
    value === null ? "text-gray-400" : value >= 0.8 ? "text-green-600" : value >= 0.5 ? "text-yellow-600" : "text-red-600";
  return (
    <div className="text-center">
      <div className={`text-2xl font-bold ${color}`}>{pct}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    labeled:   "bg-green-100 text-green-700",
    skipped:   "bg-gray-100 text-gray-500",
    unlabeled: "bg-yellow-100 text-yellow-700",
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${map[status] ?? "bg-gray-100 text-gray-500"}`}>
      {status}
    </span>
  );
}
