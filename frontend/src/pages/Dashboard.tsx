import { useCallback, useEffect, useState } from "react";
import { createApplication, getFlowData, getStats, listApplications } from "../api/client";
import type { Application, ApplicationCreate, FlowData, ScanResult, Stats } from "../types";
import { STATUSES } from "../types";
import StatsCards from "../components/StatsCards";
import FilterBar from "../components/FilterBar";
import ApplicationTable from "../components/ApplicationTable";
import ScanButton from "../components/ScanButton";
import ActivityHeatmap from "../components/ActivityHeatmap";
import SankeyFlow from "../components/SankeyFlow";
import CostChart from "../components/CostChart";
import ReviewQueue from "../components/ReviewQueue";
import { useJourney } from "../journey/JourneyContext";

export default function Dashboard() {
  const { activeJourney } = useJourney();
  const [applications, setApplications] = useState<Application[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [companySearch, setCompanySearch] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [flowData, setFlowData] = useState<FlowData | null>(null);
  const [flowLoading, setFlowLoading] = useState(true);
  const [lastScan, setLastScan] = useState<ScanResult | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creatingApplication, setCreatingApplication] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [newApplication, setNewApplication] = useState<ApplicationCreate>({
    company: "",
    job_title: "",
    req_id: "",
    status: "已申请",
    notes: "",
    source: "manual",
  });
  const pageSize = 20;

  const fetchApplications = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listApplications({
        page,
        page_size: pageSize,
        status: statusFilter || undefined,
        company: companySearch || undefined,
        sort_by: "email_date",
        sort_order: "desc",
      });
      setApplications(res.items);
      setTotal(res.total);
    } catch (err) {
      console.error("Failed to fetch applications:", err);
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, companySearch, activeJourney?.id]);

  const fetchStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const s = await getStats();
      setStats(s);
    } catch (err) {
      console.error("Failed to fetch stats:", err);
    } finally {
      setStatsLoading(false);
    }
  }, [activeJourney?.id]);

  const fetchFlowData = useCallback(async () => {
    setFlowLoading(true);
    try {
      const fd = await getFlowData();
      setFlowData(fd);
    } catch (err) {
      console.error("Failed to fetch flow data:", err);
    } finally {
      setFlowLoading(false);
    }
  }, [activeJourney?.id]);

  useEffect(() => {
    setPage(1);
    setApplications([]);
    setTotal(0);
    setStats(null);
    setFlowData(null);
    setLastScan(null);
  }, [activeJourney?.id]);

  useEffect(() => {
    fetchApplications();
  }, [fetchApplications]);

  useEffect(() => {
    fetchStats();
    fetchFlowData();
  }, [fetchStats, fetchFlowData]);

  const handleScanComplete = (result: ScanResult) => {
    setLastScan(result);
    fetchApplications();
    fetchStats();
    fetchFlowData();
  };

  const handleRefresh = () => {
    fetchApplications();
    fetchStats();
    fetchFlowData();
  };

  const openCreateModal = () => {
    resetCreateModal();
    setShowCreateModal(true);
  };

  const resetCreateModal = () => {
    setNewApplication({
      company: "",
      job_title: "",
      req_id: "",
      status: "已申请",
      notes: "",
      source: "manual",
    });
    setCreateError(null);
    setCreatingApplication(false);
  };

  const handleCreateApplication = async () => {
    const company = (newApplication.company || "").trim();
    if (!company) {
      setCreateError("Company is required.");
      return;
    }

    setCreatingApplication(true);
    setCreateError(null);
    try {
      const created = await createApplication({
        company,
        job_title: (newApplication.job_title || "").trim() || undefined,
        req_id: (newApplication.req_id || "").trim() || undefined,
        status: newApplication.status || "已申请",
        notes: (newApplication.notes || "").trim() || undefined,
        source: "manual",
      });
      setShowCreateModal(false);
      resetCreateModal();
      setPage(1);
      if (page === 1) {
        fetchApplications();
      }
      fetchStats();
      fetchFlowData();
      console.info("application_created", { id: created.id });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCreateError(message.includes("409") ? "Application already exists." : "Failed to create application.");
    } finally {
      setCreatingApplication(false);
    }
  };

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-6">
      {/* Header row */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">
            {total} application{total !== 1 ? "s" : ""} tracked
            {stats ? ` · ${stats.total_emails_scanned} emails scanned · $${stats.total_llm_cost.toFixed(4)} LLM cost` : ""}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ScanButton key={activeJourney?.id ?? "journey-none"} onScanComplete={handleScanComplete} />
        </div>
      </div>

      {/* Scan result banner */}
      {lastScan && (
        <div className={`rounded-md p-3 text-sm ${
          lastScan.cancelled
            ? "bg-orange-50 border border-orange-200 text-orange-700"
            : "bg-indigo-50 border border-indigo-200 text-indigo-700"
        }`}>
          {lastScan.cancelled ? (
            <>
              Scan was cancelled · LLM cost: <span className="font-semibold">${lastScan.total_estimated_cost.toFixed(4)}</span>
            </>
          ) : (
            <>
              Scan complete: {lastScan.emails_scanned} emails scanned, {lastScan.emails_matched} matched,{" "}
              {lastScan.applications_created} new, {lastScan.applications_updated} updated
              {lastScan.applications_deleted > 0 && `, ${lastScan.applications_deleted} deleted`}
              {" · "}LLM cost: <span className="font-semibold">${lastScan.total_estimated_cost.toFixed(4)}</span>
              {lastScan.errors.length > 0 && ` · ${lastScan.errors.length} error(s)`}
            </>
          )}
        </div>
      )}

      {/* Applications + LLM cost (same area) */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <h2 className="text-sm font-medium text-gray-700 mb-3">Applications + LLM Cost</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-1 gap-3">
            <div className="rounded-md border border-gray-200 bg-gray-50 px-4 py-3">
              <div className="text-xs uppercase tracking-wide text-gray-500">Applications Tracked</div>
              <div className={`mt-1 text-2xl font-bold ${statsLoading ? "animate-pulse text-gray-300" : "text-gray-900"}`}>
                {statsLoading ? "—" : (stats?.total_applications ?? 0)}
              </div>
            </div>
            <div className="rounded-md border border-gray-200 bg-gray-50 px-4 py-3">
              <div className="text-xs uppercase tracking-wide text-gray-500">Total LLM Cost</div>
              <div className={`mt-1 text-2xl font-bold ${statsLoading ? "animate-pulse text-gray-300" : "text-indigo-700"}`}>
                {statsLoading ? "—" : `$${(stats?.total_llm_cost ?? 0).toFixed(4)}`}
              </div>
              <div className="mt-1 text-xs text-gray-500">
                {statsLoading ? "" : `${stats?.total_emails_scanned ?? 0} emails scanned`}
              </div>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 xl:col-span-2">
          <CostChart
            data={stats?.daily_llm_costs ?? []}
            totalCost={stats?.total_llm_cost ?? 0}
          />
        </div>
      </div>

      {/* Sankey (single, larger section) */}
      <SankeyFlow
        flowData={flowData}
        loading={flowLoading}
        height={430}
      />

      {/* Status cards */}
      <StatsCards stats={stats} loading={statsLoading} />

      {/* Activity Heatmap (GitHub/LeetCode style) */}
      <ActivityHeatmap
        data={stats?.daily_applications ?? []}
        totalApplications={stats?.total_applications ?? 0}
      />

      {/* Review Queue (shows only if there are pending emails) */}
      <ReviewQueue key={activeJourney?.id ?? "journey-none"} onResolved={handleRefresh} />

      {/* Filters + Table */}
      <div className="space-y-4">
        <FilterBar
          statusFilter={statusFilter}
          companySearch={companySearch}
          onStatusChange={(s) => { setStatusFilter(s); setPage(1); }}
          onCompanyChange={(c) => { setCompanySearch(c); setPage(1); }}
          onAddApplication={openCreateModal}
        />
        <ApplicationTable
          applications={applications}
          loading={loading}
          onRefresh={handleRefresh}
        />

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between">
            <button
              onClick={() => setPage(Math.max(1, page - 1))}
              disabled={page === 1}
              className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm disabled:opacity-50"
            >
              ← Previous
            </button>
            <span className="text-sm text-gray-500">
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage(Math.min(totalPages, page + 1))}
              disabled={page === totalPages}
              className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm disabled:opacity-50"
            >
              Next →
            </button>
          </div>
        )}
      </div>

      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-gray-900">Create Application</h2>
            <p className="mt-1 text-sm text-gray-500">Add an application manually.</p>

            <div className="mt-4 grid grid-cols-1 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">
                  Company
                </label>
                <input
                  value={newApplication.company || ""}
                  onChange={(e) => setNewApplication((prev) => ({ ...prev, company: e.target.value }))}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  placeholder="e.g. Stripe"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">
                  Job Title
                </label>
                <input
                  value={newApplication.job_title || ""}
                  onChange={(e) => setNewApplication((prev) => ({ ...prev, job_title: e.target.value }))}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  placeholder="e.g. Software Engineer"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">
                  Req ID
                </label>
                <input
                  value={newApplication.req_id || ""}
                  onChange={(e) => setNewApplication((prev) => ({ ...prev, req_id: e.target.value }))}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  placeholder="e.g. R0615432"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">
                  Status
                </label>
                <select
                  value={newApplication.status || "已申请"}
                  onChange={(e) => setNewApplication((prev) => ({ ...prev, status: e.target.value }))}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                >
                  {STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">
                  Notes
                </label>
                <textarea
                  rows={3}
                  value={newApplication.notes || ""}
                  onChange={(e) => setNewApplication((prev) => ({ ...prev, notes: e.target.value }))}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  placeholder="Optional notes"
                />
              </div>
              {createError && <p className="text-sm text-red-600">{createError}</p>}
            </div>

            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  resetCreateModal();
                }}
                className="rounded-md px-4 py-2 text-sm text-gray-600 hover:text-gray-900"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateApplication}
                disabled={creatingApplication}
                className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {creatingApplication ? "Creating..." : "Create"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
