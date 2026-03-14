import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { createApplication, getDashboardData, getLastScanResult, getScanStatus, listApplications } from "../api/client";
import type { Application, ApplicationCreate, FlowData, ScanResult, ScanState, Stats } from "../types";
import { STATUSES } from "../types";
import FilterBar from "../components/FilterBar";
import ApplicationTable from "../components/ApplicationTable";
import ScanButton from "../components/ScanButton";
import PipelineProgressPanel from "../components/PipelineProgressPanel";
import SankeyFlow from "../components/SankeyFlow";
import CostChart from "../components/CostChart";
import ReviewQueue from "../components/ReviewQueue";
import { useJourney } from "../journey/JourneyContext";

const NON_JOB_REASON_LABELS: Record<string, string> = {
  social_invitation: "social invitation",
  job_recommendation_digest: "job recommendation digest",
};

function buildPaginationItems(currentPage: number, totalPages: number): Array<number | "ellipsis"> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  if (currentPage <= 3) {
    return [1, 2, 3, 4, "ellipsis", totalPages];
  }

  if (currentPage >= totalPages - 2) {
    return [1, "ellipsis", totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
  }

  return [1, "ellipsis", currentPage - 1, currentPage, currentPage + 1, "ellipsis", totalPages];
}

function formatPreEvaluationSkipSummary(result: ScanResult): string {
  const skippedTotal =
    (result.skipped_social_or_promotions ?? 0) +
    (result.skipped_message_unavailable ?? 0);

  if (skippedTotal === 0) {
    return "0 skipped";
  }

  const parts: string[] = [];
  if (result.skipped_social_or_promotions > 0) {
    parts.push(`${result.skipped_social_or_promotions} social/promotions`);
  }
  if (result.skipped_message_unavailable > 0) {
    parts.push(`${result.skipped_message_unavailable} unavailable`);
  }

  return `${skippedTotal} skipped (${parts.join(", ")})`;
}

function formatNotJobRelatedSummary(result: ScanResult): string | null {
  if (result.skipped_not_job_related <= 0) {
    return null;
  }

  const topReasons = Object.entries(result.non_job_reason_counts ?? {})
    .sort((left, right) => right[1] - left[1])
    .slice(0, 2)
    .map(([reason, count]) => `${count} ${NON_JOB_REASON_LABELS[reason] ?? reason}`)
    .join(", ");

  return topReasons
    ? `${result.skipped_not_job_related} not job-related (${topReasons})`
    : `${result.skipped_not_job_related} not job-related`;
}

function buildCompletedScanSegments(result: ScanResult): string[] {
  const evaluatedCount = Math.max(
    0,
    result.emails_scanned -
      (result.skipped_social_or_promotions ?? 0) -
      (result.skipped_message_unavailable ?? 0)
  );

  const segments = [
    `${result.emails_scanned} emails found`,
    formatPreEvaluationSkipSummary(result),
    `${evaluatedCount} evaluated`,
    `${result.emails_matched} matched`,
  ];

  const notJobRelatedSummary = formatNotJobRelatedSummary(result);
  if (notJobRelatedSummary) {
    segments.push(notJobRelatedSummary);
  }

  segments.push(`${result.applications_created} new`);
  segments.push(`${result.applications_updated} updated`);

  if (result.applications_deleted > 0) {
    segments.push(`${result.applications_deleted} deleted`);
  }

  return segments;
}

export default function Dashboard() {
  const { activeJourney } = useJourney();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [applications, setApplications] = useState<Application[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [flowData, setFlowData] = useState<FlowData | null>(null);
  const [flowLoading, setFlowLoading] = useState(true);
  const [lastScan, setLastScan] = useState<ScanResult | null>(null);
  const [scanState, setScanState] = useState<ScanState | null>(null);
  const [scanStateLoading, setScanStateLoading] = useState(true);
  const [showScanErrors, setShowScanErrors] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creatingApplication, setCreatingApplication] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [scanHighlights, setScanHighlights] = useState<{
    createdIds: number[];
    updatedIds: number[];
  }>({ createdIds: [], updatedIds: [] });
  const [newApplication, setNewApplication] = useState<ApplicationCreate>({
    company: "",
    job_title: "",
    req_id: "",
    status: "已申请",
    notes: "",
    source: "manual",
  });
  const pageSize = 20;
  const journeyInitializedRef = useRef<number | null | undefined>(undefined);
  const restoredScrollKeyRef = useRef<string | null>(null);
  const parsedPage = Number(searchParams.get("page") || "1");
  const page = Number.isFinite(parsedPage) && parsedPage > 0 ? Math.floor(parsedPage) : 1;
  const statusFilter = searchParams.get("status") || "";
  const companySearch = searchParams.get("company") || "";
  const dashboardKey = `${location.pathname}${location.search}`;

  const updateDashboardSearch = useCallback((updates: {
    page?: number;
    status?: string;
    company?: string;
  }) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);

      if (updates.page !== undefined) {
        if (updates.page <= 1) {
          next.delete("page");
        } else {
          next.set("page", String(updates.page));
        }
      }

      if (updates.status !== undefined) {
        if (updates.status) {
          next.set("status", updates.status);
        } else {
          next.delete("status");
        }
      }

      if (updates.company !== undefined) {
        if (updates.company) {
          next.set("company", updates.company);
        } else {
          next.delete("company");
        }
      }

      return next;
    }, { replace: true });
  }, [setSearchParams]);

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

  const fetchDashboardMetrics = useCallback(async () => {
    setStatsLoading(true);
    setFlowLoading(true);
    try {
      const dashboard = await getDashboardData();
      const { flow, ...statsPayload } = dashboard;
      setStats(statsPayload);
      setFlowData(flow);
    } catch (err) {
      console.error("Failed to fetch dashboard data:", err);
    } finally {
      setStatsLoading(false);
      setFlowLoading(false);
    }
  }, [activeJourney?.id]);

  const fetchLastScan = useCallback(async () => {
    try {
      const result = await getLastScanResult();
      setLastScan(result);
      if (!result?.errors.length) {
        setShowScanErrors(false);
      }
    } catch (err) {
      console.error("Failed to fetch last scan result:", err);
    }
  }, [activeJourney?.id]);

  const fetchScanState = useCallback(async () => {
    setScanStateLoading(true);
    try {
      const state = await getScanStatus();
      setScanState(state);
    } catch (err) {
      console.error("Failed to fetch scan state:", err);
      setScanState(null);
    } finally {
      setScanStateLoading(false);
    }
  }, [activeJourney?.id]);

  useEffect(() => {
    setApplications([]);
    setTotal(0);
    setStats(null);
    setFlowData(null);
    setLastScan(null);
    setScanState(null);
    setScanStateLoading(true);
    setShowScanErrors(false);
    setScanHighlights({ createdIds: [], updatedIds: [] });

    const previousJourneyId = journeyInitializedRef.current;
    const nextJourneyId = activeJourney?.id;
    if (previousJourneyId !== undefined && previousJourneyId !== nextJourneyId) {
      updateDashboardSearch({ page: 1 });
    }
    journeyInitializedRef.current = nextJourneyId;
  }, [activeJourney?.id, updateDashboardSearch]);

  useEffect(() => {
    if (scanHighlights.createdIds.length === 0 && scanHighlights.updatedIds.length === 0) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setScanHighlights({ createdIds: [], updatedIds: [] });
    }, 8000);

    return () => window.clearTimeout(timeoutId);
  }, [scanHighlights]);

  useEffect(() => {
    window.sessionStorage.setItem("dashboard:returnTo", dashboardKey);
  }, [dashboardKey]);

  useEffect(() => {
    const restoreKey = window.sessionStorage.getItem("dashboard:restore");
    if (restoreKey !== dashboardKey) {
      if (restoreKey && restoreKey !== dashboardKey && location.pathname === "/") {
        window.sessionStorage.removeItem("dashboard:restore");
      }
      return;
    }
    if (restoredScrollKeyRef.current === dashboardKey) {
      return;
    }
    if (loading || statsLoading || flowLoading || scanStateLoading) {
      return;
    }

    const savedScroll = Number(window.sessionStorage.getItem(`dashboard:scroll:${dashboardKey}`));
    window.sessionStorage.removeItem("dashboard:restore");
    restoredScrollKeyRef.current = dashboardKey;

    if (!Number.isFinite(savedScroll)) {
      return;
    }

    let frameOne = 0;
    let frameTwo = 0;
    frameOne = window.requestAnimationFrame(() => {
      frameTwo = window.requestAnimationFrame(() => {
        window.scrollTo({ top: savedScroll, behavior: "auto" });
      });
    });

    return () => {
      window.cancelAnimationFrame(frameOne);
      window.cancelAnimationFrame(frameTwo);
    };
  }, [dashboardKey, loading, statsLoading, flowLoading, scanStateLoading, location.pathname]);

  useEffect(() => {
    fetchApplications();
  }, [fetchApplications]);

  useEffect(() => {
    fetchDashboardMetrics();
    fetchLastScan();
    fetchScanState();
  }, [fetchDashboardMetrics, fetchLastScan, fetchScanState]);

  const handleScanComplete = (result: ScanResult) => {
    setLastScan(result);
    setShowScanErrors(false);
    const createdIds = result.created_application_ids ?? [];
    const updatedIds = (result.updated_application_ids ?? []).filter((id) => !createdIds.includes(id));
    setScanHighlights({ createdIds, updatedIds });
    if (page === 1) {
      fetchApplications();
    } else {
      updateDashboardSearch({ page: 1 });
    }
    fetchDashboardMetrics();
    fetchScanState();
  };

  const handleRefresh = () => {
    fetchApplications();
    fetchDashboardMetrics();
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
      setApplications((prev) => {
        const next = [created, ...prev.filter((app) => app.id !== created.id)];
        return next.slice(0, pageSize);
      });
      setTotal((prev) => prev + (applications.some((app) => app.id === created.id) ? 0 : 1));
      updateDashboardSearch({ page: 1 });
      if (page === 1) {
        fetchApplications();
      }
      fetchDashboardMetrics();
      console.info("application_created", { id: created.id });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCreateError(message.includes("409") ? "Application already exists." : "Failed to create application.");
    } finally {
      setCreatingApplication(false);
    }
  };

  const totalPages = Math.ceil(total / pageSize);
  const showingFrom = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const showingTo = total === 0 ? 0 : Math.min(page * pageSize, total);
  const paginationItems = buildPaginationItems(page, totalPages);
  const completedScanSegments = lastScan ? buildCompletedScanSegments(lastScan) : [];
  const hasImportedEmailData = Boolean(scanState?.last_scan_at) || (stats?.total_emails_scanned ?? 0) > 0;
  const scanButtonMode =
    scanStateLoading && scanState === null && stats === null
      ? "loading"
      : hasImportedEmailData
        ? "default"
        : "initial";

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
          <ScanButton
            key={activeJourney?.id ?? "journey-none"}
            mode={scanButtonMode}
            onScanComplete={handleScanComplete}
          />
        </div>
      </div>

      {/* Scan result banner */}
	      {lastScan && (
	        <div className={`rounded-md p-3 text-sm ${
	          lastScan.cancelled
	            ? "bg-orange-50 border border-orange-200 text-orange-700"
	            : "bg-indigo-50 border border-indigo-200 text-indigo-700"
	        }`}>
	          <div className="space-y-3">
	            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
	              <div className="leading-6">
	                {lastScan.cancelled ? (
                  <>
                    Scan was cancelled · LLM cost: <span className="font-semibold">${lastScan.total_estimated_cost.toFixed(4)}</span>
                    {lastScan.errors.length > 0 && ` · ${lastScan.errors.length} error(s)`}
                  </>
                ) : (
	                  <>
	                    {completedScanSegments.join(" · ")}
	                    {" · "}LLM cost: <span className="font-semibold">${lastScan.total_estimated_cost.toFixed(4)}</span>
	                    {lastScan.errors.length > 0 && ` · ${lastScan.errors.length} error(s)`}
	                  </>
                )}
              </div>
              {lastScan.errors.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowScanErrors((current) => !current)}
                  className={`shrink-0 self-start rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                    lastScan.cancelled
                      ? "border-orange-300 bg-white/80 text-orange-700 hover:bg-white"
                      : "border-indigo-300 bg-white/80 text-indigo-700 hover:bg-white"
                  }`}
                >
                  {showScanErrors ? "Hide errors" : `View ${lastScan.errors.length} errors`}
                </button>
              )}
            </div>

            {showScanErrors && lastScan.errors.length > 0 && (
              <div className={`rounded-md border p-3 ${
                lastScan.cancelled
                  ? "border-orange-200 bg-white/70"
                  : "border-indigo-200 bg-white/70"
              }`}>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  Error details
                </div>
                <ol className="space-y-2">
                  {lastScan.errors.map((error, index) => (
                    <li
                      key={`${index}-${error}`}
                      className="rounded-md bg-gray-900 px-3 py-2 font-mono text-xs leading-5 text-gray-100 whitespace-pre-wrap break-words"
                    >
                      {index + 1}. {error}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[360px,minmax(0,1fr)] 2xl:grid-cols-[388px,minmax(0,1fr)] gap-6 items-start">
        <div className="relative isolate">
          <div className="pointer-events-none absolute inset-[-4%] overflow-hidden rounded-[40px]">
            <div className="absolute inset-0 bg-[linear-gradient(145deg,rgba(255,255,255,0.72),rgba(239,246,255,0.3)_34%,rgba(219,234,254,0.16)_100%)]" />
            <div className="absolute -left-10 top-9 h-44 w-44 rounded-full bg-sky-300/46 blur-[76px]" />
            <div className="absolute right-[-6%] top-[4%] h-36 w-36 rounded-full bg-rose-200/28 blur-[72px]" />
            <div className="absolute left-[35%] top-[14%] h-[64%] w-24 rotate-[12deg] rounded-full bg-white/32 blur-[52px]" />
            <div className="absolute left-[28%] top-[22%] h-44 w-48 rounded-full bg-cyan-300/28 blur-[88px]" />
            <div className="absolute left-[12%] bottom-[2%] h-56 w-56 rounded-full bg-blue-300/36 blur-[92px]" />
          </div>
          <div className="relative z-10">
            <PipelineProgressPanel stats={stats} loading={statsLoading} />
          </div>
        </div>
        <SankeyFlow
          flowData={flowData}
          loading={flowLoading}
          height={340}
          showSummary={false}
          title="Flow"
        />
      </div>

      {/* Filters + Table */}
      <div className="space-y-4">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900">Applications</h2>
        </div>
        <FilterBar
          statusFilter={statusFilter}
          companySearch={companySearch}
          total={total}
          onStatusChange={(status) => updateDashboardSearch({ status, page: 1 })}
          onCompanyChange={(company) => updateDashboardSearch({ company, page: 1 })}
          onAddApplication={openCreateModal}
        />
        <ApplicationTable
          applications={applications}
          loading={loading}
          onRefresh={handleRefresh}
          recentlyCreatedIds={scanHighlights.createdIds}
          recentlyUpdatedIds={scanHighlights.updatedIds}
        />

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-gray-500">
                Showing <span className="font-medium text-gray-900">{showingFrom}-{showingTo}</span> of{" "}
                <span className="font-medium text-gray-900">{total}</span> applications
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <button
                  onClick={() => updateDashboardSearch({ page: Math.max(1, page - 1) })}
                  disabled={page === 1}
                  className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  ← Previous
                </button>
                {paginationItems.map((item, index) =>
                  item === "ellipsis" ? (
                    <span
                      key={`ellipsis-${index}`}
                      className="px-2 py-1 text-sm text-gray-400"
                    >
                      …
                    </span>
                  ) : (
                    <button
                      key={item}
                      onClick={() => updateDashboardSearch({ page: item })}
                      aria-current={item === page ? "page" : undefined}
                      className={`min-w-9 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                        item === page
                          ? "bg-indigo-600 text-white shadow-sm"
                          : "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
                      }`}
                    >
                      {item}
                    </button>
                  )
                )}
                <button
                  onClick={() => updateDashboardSearch({ page: Math.min(totalPages, page + 1) })}
                  disabled={page === totalPages}
                  className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Next →
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Review Queue (shows only if there are pending emails) */}
      <ReviewQueue key={activeJourney?.id ?? "journey-none"} onResolved={handleRefresh} />

      {/* Insights */}
      <div className="space-y-6">
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
