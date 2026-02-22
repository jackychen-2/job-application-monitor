import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { listCachedEmails, bulkUpdateLabels, listEvalRuns } from "../../api/eval";
import type { CachedEmailListResponse, EvalRun } from "../../types/eval";

const STATUS_BADGE: Record<string, string> = {
  unlabeled: "bg-gray-100 text-gray-600",
  labeled: "bg-green-100 text-green-700",
  skipped: "bg-yellow-100 text-yellow-700",
  uncertain: "bg-orange-100 text-orange-700",
};

export default function ReviewQueue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<CachedEmailListResponse | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState(searchParams.get("search") || "");
  const [runs, setRuns] = useState<EvalRun[]>([]);

  const page = Number(searchParams.get("page") || 1);
  const filter = searchParams.get("status") || "";
  const runId = searchParams.get("run_id") ? Number(searchParams.get("run_id")) : undefined;

  const load = () => {
    listCachedEmails({
      page,
      page_size: 50,
      review_status: filter || undefined,
      search: search || undefined,
      run_id: runId,
    }).then(setData);
  };

  useEffect(() => { load(); }, [page, filter, search, runId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { listEvalRuns().then(setRuns); }, []);

  const setRunFilter = (id: number | undefined) => {
    const sp = new URLSearchParams(searchParams);
    if (id) sp.set("run_id", String(id)); else sp.delete("run_id");
    sp.set("page", "1");
    setSearchParams(sp);
  };

  const setPage = (p: number) => {
    const sp = new URLSearchParams(searchParams);
    sp.set("page", String(p));
    setSearchParams(sp);
  };

  const setFilter = (s: string) => {
    const sp = new URLSearchParams(searchParams);
    if (s) sp.set("status", s); else sp.delete("status");
    sp.set("page", "1");
    setSearchParams(sp);
  };

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleBulkAction = async (action: string) => {
    if (selected.size === 0) return;
    if (action === "not_job") {
      await bulkUpdateLabels({ cached_email_ids: [...selected], is_job_related: false, review_status: "labeled" });
    } else if (action === "skip") {
      await bulkUpdateLabels({ cached_email_ids: [...selected], review_status: "skipped" });
    }
    setSelected(new Set());
    load();
  };

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Review Queue</h1>
        <Link to="/eval" className="text-sm text-blue-600 hover:underline">← Back to Dashboard</Link>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        {/* Run filter */}
        <div>
          <select
            value={runId ?? ""}
            onChange={e => setRunFilter(e.target.value ? Number(e.target.value) : undefined)}
            className="border rounded px-3 py-1.5 text-sm bg-white"
          >
            <option value="">All emails</option>
            {runs.map(r => (
              <option key={r.id} value={r.id}>
                Run #{r.id} — {r.run_name || new Date(r.started_at).toLocaleDateString()} ({r.total_emails} emails)
              </option>
            ))}
          </select>
        </div>

        {/* Status filter */}
        <div className="flex gap-1">
          {["", "unlabeled", "labeled", "skipped"].map(s => (
            <button key={s} onClick={() => setFilter(s)}
              className={`px-3 py-1.5 rounded text-sm ${filter === s ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}>
              {s || "All"}
            </button>
          ))}
        </div>
        <input type="text" placeholder="Search subject/sender..." value={search}
          onChange={e => setSearch(e.target.value)}
          className="border rounded px-3 py-1.5 text-sm flex-1 max-w-xs" />
        {selected.size > 0 && (
          <div className="flex gap-2 ml-auto">
            <span className="text-sm text-gray-500">{selected.size} selected</span>
            <button onClick={() => handleBulkAction("not_job")} className="px-3 py-1 text-xs bg-red-100 text-red-700 rounded hover:bg-red-200">
              Mark Not Job
            </button>
            <button onClick={() => handleBulkAction("skip")} className="px-3 py-1 text-xs bg-yellow-100 text-yellow-700 rounded hover:bg-yellow-200">
              Skip
            </button>
          </div>
        )}
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-3 w-8">
                <input type="checkbox" onChange={e => {
                  if (e.target.checked && data) setSelected(new Set(data.items.map(i => i.id)));
                  else setSelected(new Set());
                }} />
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Subject</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Sender</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
              <th className="px-4 py-3 w-20"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {data?.items.map(email => (
              <tr key={email.id} className="hover:bg-gray-50">
                <td className="px-3 py-3">
                  <input type="checkbox" checked={selected.has(email.id)} onChange={() => toggleSelect(email.id)} />
                </td>
                <td className="px-4 py-3 text-sm text-gray-900 max-w-md truncate">{email.subject || "(no subject)"}</td>
                <td className="px-4 py-3 text-sm text-gray-600 max-w-xs truncate">{email.sender}</td>
                <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">
                  {email.email_date ? new Date(email.email_date).toLocaleDateString() : "—"}
                </td>
                <td className="px-4 py-3">
                  <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_BADGE[email.review_status || "unlabeled"] || STATUS_BADGE.unlabeled}`}>
                    {email.review_status || "unlabeled"}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <Link to={`/eval/review/${email.id}${runId ? `?run_id=${runId}` : ""}`} className="text-blue-600 text-sm hover:underline">Review</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-600">
            {data?.total} emails total — Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <button onClick={() => setPage(page - 1)} disabled={page <= 1}
              className="px-3 py-1 border rounded text-sm disabled:opacity-50">← Prev</button>
            <button onClick={() => setPage(page + 1)} disabled={page >= totalPages}
              className="px-3 py-1 border rounded text-sm disabled:opacity-50">Next →</button>
          </div>
        </div>
      )}
    </div>
  );
}
