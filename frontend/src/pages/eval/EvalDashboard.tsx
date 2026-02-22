import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getCacheStats, listEvalRuns, streamEvalRun, cancelEvalRun, downloadEmails, getEvalSettings, setLlmEnabled } from "../../api/eval";
import type { EvalSettings } from "../../api/eval";
import type { CacheStats, EvalRun, CacheDownloadResult } from "../../types/eval";

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
    // Reset state
    setEvalLogs([]);
    setEvalProgress(0);
    setEvalTotal(0);
    setCancelRequested(false);
    setRunningEval(true);

    const es = streamEvalRun(undefined, maxEmails > 0 ? maxEmails : undefined);
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



  const latestRun = runs[0];
  const progressPct = evalTotal > 0 ? Math.round((evalProgress / evalTotal) * 100) : 0;

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
          Re-run the pipeline on all {stats?.total_cached || 0} cached emails and score against{" "}
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

        {/* Action buttons */}
        <div className="flex gap-3 items-center flex-wrap">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Emails to evaluate</label>
            <input
              type="number"
              value={maxEmails || ""}
              onChange={e => setMaxEmails(Number(e.target.value))}
              placeholder={`All (${stats?.total_cached || 0})`}
              min={1}
              max={stats?.total_cached || undefined}
              className="border rounded px-3 py-2 text-sm w-32"
            />
          </div>
          <div className="flex gap-2 items-end pt-4">
            <button
              onClick={handleRunEval}
              disabled={runningEval || !stats?.total_cached}
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm font-medium"
            >
              {runningEval ? "Running…" : "Run Evaluation"}
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
