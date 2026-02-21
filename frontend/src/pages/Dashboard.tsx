import { useCallback, useEffect, useState } from "react";
import { listApplications, getStats, getFlowData } from "../api/client";
import type { Application, FlowData, ScanResult, Stats } from "../types";
import StatsCards from "../components/StatsCards";
import FilterBar from "../components/FilterBar";
import ApplicationTable from "../components/ApplicationTable";
import ScanButton from "../components/ScanButton";
import ActivityHeatmap from "../components/ActivityHeatmap";
import SankeyFlow from "../components/SankeyFlow";
import CostChart from "../components/CostChart";
import ReviewQueue from "../components/ReviewQueue";

export default function Dashboard() {
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
  }, [page, statusFilter, companySearch]);

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
  }, []);

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
  }, []);

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
          <ScanButton onScanComplete={handleScanComplete} />
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

      {/* Stats cards */}
      <StatsCards stats={stats} loading={statsLoading} />

      {/* Activity Heatmap (GitHub/LeetCode style) */}
      <ActivityHeatmap
        data={stats?.daily_applications ?? []}
        totalApplications={stats?.total_applications ?? 0}
      />

      {/* Sankey Flow + LLM Cost Chart */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <SankeyFlow
          flowData={flowData}
          loading={flowLoading}
        />
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <CostChart
            data={stats?.daily_llm_costs ?? []}
            totalCost={stats?.total_llm_cost ?? 0}
          />
        </div>
      </div>

      {/* Review Queue (shows only if there are pending emails) */}
      <ReviewQueue onResolved={handleRefresh} />

      {/* Filters + Table */}
      <div className="space-y-4">
        <FilterBar
          statusFilter={statusFilter}
          companySearch={companySearch}
          onStatusChange={(s) => { setStatusFilter(s); setPage(1); }}
          onCompanyChange={(c) => { setCompanySearch(c); setPage(1); }}
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
    </div>
  );
}
