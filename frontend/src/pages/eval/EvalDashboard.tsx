import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getCacheStats, listEvalRuns, triggerEvalRun, downloadEmails } from "../../api/eval";
import type { CacheStats, EvalRun, CacheDownloadResult } from "../../types/eval";

export default function EvalDashboard() {
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [downloading, setDownloading] = useState(false);
  const [downloadResult, setDownloadResult] = useState<CacheDownloadResult | null>(null);
  const [runningEval, setRunningEval] = useState(false);
  const [sinceDate, setSinceDate] = useState("");
  const [beforeDate, setBeforeDate] = useState("");
  const [maxCount, setMaxCount] = useState(500);

  const refresh = () => {
    getCacheStats().then(setStats);
    listEvalRuns().then(setRuns);
  };

  useEffect(() => { refresh(); }, []);

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

  const handleRunEval = async () => {
    setRunningEval(true);
    try {
      await triggerEvalRun();
      refresh();
    } finally {
      setRunningEval(false);
    }
  };

  const latestRun = runs[0];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Evaluation Dashboard</h1>
        <div className="flex gap-2">
          <Link to="/eval/review" className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium">
            Review Emails
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
            Fetched {downloadResult.total_fetched} emails: {downloadResult.new_emails} new, {downloadResult.skipped_duplicates} duplicates, {downloadResult.errors} errors
          </div>
        )}
      </div>

      {/* Run Evaluation */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Run Evaluation</h2>
        <p className="text-sm text-gray-600 mb-3">
          Re-run the pipeline on all {stats?.total_cached || 0} cached emails and score against {stats?.total_labeled || 0} labels.
        </p>
        <button onClick={handleRunEval} disabled={runningEval || !stats?.total_cached}
          className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm font-medium">
          {runningEval ? "Running..." : "Run Evaluation"}
        </button>
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
  const color = value === null ? "text-gray-400" : value >= 0.8 ? "text-green-600" : value >= 0.5 ? "text-yellow-600" : "text-red-600";
  return (
    <div className="text-center">
      <div className={`text-2xl font-bold ${color}`}>{pct}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
    </div>
  );
}
