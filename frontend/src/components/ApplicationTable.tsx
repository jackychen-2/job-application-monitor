import { Fragment, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Application, LinkedEmail } from "../types";
import StatusBadge from "./StatusBadge";
import {
  deleteApplication,
  getMergeCandidates,
  getApplicationEmails,
  mergeApplications,
  splitApplication,
  updateApplication,
} from "../api/client";
import { STATUSES } from "../types";

interface Props {
  applications: Application[];
  loading: boolean;
  onRefresh: () => void;
  recentlyCreatedIds?: number[];
  recentlyUpdatedIds?: number[];
}

type EmailCache = Record<number, LinkedEmail[]>;
type EditField = { id: number; field: "status"; value: string };

function mergeApplicationOptions(localApps: Application[], remoteApps: Application[]): Application[] {
  const merged = new Map<number, Application>();
  for (const app of localApps) {
    merged.set(app.id, app);
  }
  for (const app of remoteApps) {
    merged.set(app.id, app);
  }

  return Array.from(merged.values()).sort((left, right) => {
    const leftTs = Date.parse(left.email_date || left.created_at || "") || 0;
    const rightTs = Date.parse(right.email_date || right.created_at || "") || 0;
    return rightTs - leftTs;
  });
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "-";
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return "-";
  try {
    return new Date(dateStr).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

export default function ApplicationTable({
  applications,
  loading,
  onRefresh,
  recentlyCreatedIds = [],
  recentlyUpdatedIds = [],
}: Props) {
  const location = useLocation();
  const navigate = useNavigate();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const rowRefs = useRef<Record<number, HTMLTableRowElement | null>>({});
  const [editing, setEditing] = useState<EditField | null>(null);
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  const [emailCache, setEmailCache] = useState<EmailCache>({});
  const [loadingEmails, setLoadingEmails] = useState<Set<number>>(new Set());
  const [openMenuId, setOpenMenuId] = useState<number | null>(null);

  const [mergeModalApp, setMergeModalApp] = useState<Application | null>(null);
  const [mergeOptions, setMergeOptions] = useState<Application[]>([]);
  const [mergeOptionsLoading, setMergeOptionsLoading] = useState(false);
  const [selectedMergeId, setSelectedMergeId] = useState<number | null>(null);

  const [splitModalApp, setSplitModalApp] = useState<Application | null>(null);
  const [splitEmails, setSplitEmails] = useState<LinkedEmail[]>([]);
  const [splitEmailsLoading, setSplitEmailsLoading] = useState(false);
  const [selectedSplitEmailIds, setSelectedSplitEmailIds] = useState<number[]>([]);
  const [splitCompany, setSplitCompany] = useState("");
  const [splitJobTitle, setSplitJobTitle] = useState("");
  const [splitReqId, setSplitReqId] = useState("");
  const [splitStatus, setSplitStatus] = useState("已申请");
  const [splitNotes, setSplitNotes] = useState("");

  const [actionSaving, setActionSaving] = useState(false);
  const createdIdSet = new Set(recentlyCreatedIds);
  const updatedIdSet = new Set(recentlyUpdatedIds);

  useEffect(() => {
    if (openMenuId === null) return;

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;

      const menuRoot = rootRef.current?.querySelector(`[data-menu-root="${openMenuId}"]`);
      if (menuRoot?.contains(target)) {
        return;
      }

      setOpenMenuId(null);
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpenMenuId(null);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [openMenuId]);

  useEffect(() => {
    if (loading) {
      return;
    }

    const targetId = recentlyCreatedIds[0] ?? recentlyUpdatedIds[0];
    if (!targetId) {
      return;
    }

    rowRefs.current[targetId]?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }, [loading, recentlyCreatedIds, recentlyUpdatedIds]);

  const resetMutationState = () => {
    setExpandedRows(new Set());
    setEmailCache({});
    setLoadingEmails(new Set());
  };

  const openApplicationDetail = (applicationId: number) => {
    setOpenMenuId(null);
    const dashboardKey = `${location.pathname}${location.search}`;
    window.sessionStorage.setItem(`dashboard:scroll:${dashboardKey}`, String(window.scrollY));
    window.sessionStorage.setItem("dashboard:restore", dashboardKey);

    navigate(`/applications/${applicationId}`, {
      state: {
        from: dashboardKey,
      },
    });
  };

  const toggleExpand = async (appId: number, event: React.MouseEvent) => {
    event.stopPropagation();
    const nextExpanded = new Set(expandedRows);

    if (nextExpanded.has(appId)) {
      nextExpanded.delete(appId);
    } else {
      nextExpanded.add(appId);
      if (!emailCache[appId]) {
        setLoadingEmails((current) => new Set(current).add(appId));
        try {
          const emails = await getApplicationEmails(appId);
          setEmailCache((current) => ({ ...current, [appId]: emails }));
        } catch (err) {
          console.error("Failed to load emails:", err);
        } finally {
          setLoadingEmails((current) => {
            const next = new Set(current);
            next.delete(appId);
            return next;
          });
        }
      }
    }

    setExpandedRows(nextExpanded);
  };

  const handleSave = async () => {
    if (!editing) return;
    const update: Record<string, string> = {};
    update[editing.field] = editing.value;
    await updateApplication(editing.id, update);
    setEditing(null);
    onRefresh();
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter") void handleSave();
    if (event.key === "Escape") setEditing(null);
  };

  const handleDelete = async (app: Application) => {
    setOpenMenuId(null);
    if (!window.confirm(`Delete application at ${app.company}?`)) {
      return;
    }

    await deleteApplication(app.id);
    resetMutationState();
    onRefresh();
  };

  const openMergeModal = async (app: Application) => {
    setOpenMenuId(null);
    setMergeModalApp(app);
    const localOptions = applications.filter((candidate) => candidate.id !== app.id);
    setMergeOptions(localOptions);
    setSelectedMergeId(null);
    setMergeOptionsLoading(true);

    try {
      const mergeTargets = await getMergeCandidates(app.id);
      setMergeOptions(
        mergeApplicationOptions(
          localOptions,
          mergeTargets,
        ),
      );
    } catch (err) {
      console.error("Failed to load merge targets:", err);
      alert("Failed to load applications for merge");
      setMergeModalApp(null);
    } finally {
      setMergeOptionsLoading(false);
    }
  };

  const closeMergeModal = () => {
    setMergeModalApp(null);
    setMergeOptions([]);
    setSelectedMergeId(null);
    setMergeOptionsLoading(false);
    setActionSaving(false);
  };

  const handleMerge = async () => {
    if (!mergeModalApp || !selectedMergeId) return;

    setActionSaving(true);
    try {
      await mergeApplications(mergeModalApp.id, selectedMergeId);
      closeMergeModal();
      resetMutationState();
      onRefresh();
    } catch (err) {
      console.error("Failed to merge applications:", err);
      alert("Failed to merge applications");
      setActionSaving(false);
    }
  };

  const openSplitModal = async (app: Application) => {
    setOpenMenuId(null);
    if (app.email_count < 2) {
      alert("Split requires at least 2 linked emails.");
      return;
    }

    setSplitModalApp(app);
    setSplitEmails([]);
    setSelectedSplitEmailIds([]);
    setSplitCompany(app.company);
    setSplitJobTitle(app.job_title ?? "");
    setSplitReqId(app.req_id ?? "");
    setSplitStatus(app.status || "已申请");
    setSplitNotes(app.notes ?? "");
    setSplitEmailsLoading(true);

    try {
      const emails = emailCache[app.id] ?? await getApplicationEmails(app.id);
      if (!emailCache[app.id]) {
        setEmailCache((current) => ({ ...current, [app.id]: emails }));
      }
      if (emails.length < 2) {
        alert("Split requires at least 2 linked emails.");
        setSplitModalApp(null);
        return;
      }
      setSplitEmails(emails);
      setSelectedSplitEmailIds([emails[0].id]);
    } catch (err) {
      console.error("Failed to load emails for split:", err);
      alert("Failed to load emails for split");
      setSplitModalApp(null);
    } finally {
      setSplitEmailsLoading(false);
    }
  };

  const closeSplitModal = () => {
    setSplitModalApp(null);
    setSplitEmails([]);
    setSplitEmailsLoading(false);
    setSelectedSplitEmailIds([]);
    setSplitCompany("");
    setSplitJobTitle("");
    setSplitReqId("");
    setSplitStatus("已申请");
    setSplitNotes("");
    setActionSaving(false);
  };

  const handleSplit = async () => {
    if (!splitModalApp || selectedSplitEmailIds.length === 0) return;

    setActionSaving(true);
    try {
      await splitApplication(splitModalApp.id, {
        email_ids: selectedSplitEmailIds,
        company: splitCompany,
        job_title: splitJobTitle || undefined,
        req_id: splitReqId || undefined,
        status: splitStatus || undefined,
        notes: splitNotes || undefined,
      });
      closeSplitModal();
      resetMutationState();
      onRefresh();
    } catch (err) {
      console.error("Failed to split application:", err);
      const raw = err instanceof Error ? err.message : String(err);
      const parsed =
        raw.match(/"detail"\s*:\s*"([^"]+)"/)?.[1] ||
        raw.match(/detail['"]?\s*:\s*['"]([^'"]+)['"]/)?.[1] ||
        raw;
      alert(parsed || "Failed to split this application");
      setActionSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-8 text-center text-gray-400 shadow-sm">
        Loading applications...
      </div>
    );
  }

  if (applications.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-8 text-center text-gray-400 shadow-sm">
        No applications found. Scan your email to get started!
      </div>
    );
  }

  return (
    <>
      <div ref={rootRef} className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Company
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Last Activity
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Role
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Preview
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  More
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {applications.map((app) => {
                const isExpanded = expandedRows.has(app.id);
                const emails = emailCache[app.id] || [];
                const isLoadingRowEmails = loadingEmails.has(app.id);
                const highlightState = createdIdSet.has(app.id)
                  ? "created"
                  : updatedIdSet.has(app.id)
                    ? "updated"
                    : null;
                const rowClass =
                  highlightState === "created"
                    ? "bg-emerald-50/90 hover:bg-emerald-100/80"
                    : highlightState === "updated"
                      ? "bg-amber-50/90 hover:bg-amber-100/80"
                      : isExpanded
                        ? "bg-indigo-50/30 hover:bg-indigo-50/40"
                        : "hover:bg-gray-50";
                const badgeClass =
                  highlightState === "created"
                    ? "border-emerald-200 bg-emerald-100 text-emerald-800"
                    : "border-amber-200 bg-amber-100 text-amber-800";
                const accentClass =
                  highlightState === "created" ? "bg-emerald-500" : "bg-amber-500";
                const highlightLabel = highlightState === "created" ? "New" : "Updated";

                return (
                  <Fragment key={app.id}>
                    <tr
                      ref={(node) => {
                        rowRefs.current[app.id] = node;
                      }}
                      className={`transition-colors ${rowClass}`}
                    >
                      <td className="px-4 py-4 align-top">
                        <div className="flex items-start gap-3">
                          {highlightState ? (
                            <span className={`mt-1 h-10 w-1 shrink-0 rounded-full ${accentClass}`} />
                          ) : null}
                          {app.email_count > 1 ? (
                            <button
                              onClick={(event) => void toggleExpand(app.id, event)}
                              className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-1 text-xs font-medium text-gray-500 transition-colors hover:border-indigo-200 hover:text-indigo-700"
                              title={isExpanded ? "Collapse email chain" : "Expand email chain"}
                            >
                              <span>{isExpanded ? "v" : ">"}</span>
                              <span>{app.email_count}</span>
                            </button>
                          ) : (
                            <span className="block w-11 shrink-0" />
                          )}
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => openApplicationDetail(app.id)}
                                className="truncate text-left text-sm font-semibold text-gray-900 transition hover:text-indigo-700 hover:underline"
                              >
                                {app.company}
                              </button>
                              {highlightState ? (
                                <span
                                  className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${badgeClass}`}
                                >
                                  {highlightLabel}
                                </span>
                              ) : null}
                            </div>
                            {app.req_id ? (
                              <div className="mt-1 truncate text-xs text-gray-500">
                                Req ID: {app.req_id}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-4 align-top text-sm">
                        {editing?.id === app.id && editing.field === "status" ? (
                          <div className="flex items-center gap-1">
                            <select
                              value={editing.value}
                              onChange={(event) => setEditing({ ...editing, value: event.target.value })}
                              onKeyDown={handleKeyDown}
                              onBlur={() => void handleSave()}
                              className="rounded border px-1 py-0.5 text-xs"
                              autoFocus
                            >
                              {STATUSES.map((status) => (
                                <option key={status} value={status}>
                                  {status}
                                </option>
                              ))}
                            </select>
                          </div>
                        ) : (
                          <span
                            onClick={() => setEditing({ id: app.id, field: "status", value: app.status })}
                            title="Click to change status"
                            className="inline-flex cursor-pointer"
                          >
                            <StatusBadge status={app.status} />
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-4 align-top text-sm text-gray-500">
                        {formatDate(app.email_date || app.created_at)}
                      </td>
                      <td
                        className="max-w-[320px] px-4 py-4 align-top text-sm text-gray-600 xl:max-w-[380px]"
                        title={app.job_title ?? ""}
                      >
                        <div className={app.job_title ? "break-words leading-6 whitespace-normal" : "text-gray-400"}>
                          {app.job_title || "-"}
                        </div>
                      </td>
                      <td
                        className="max-w-[420px] px-4 py-4 align-top text-sm text-gray-500 xl:max-w-[520px]"
                        title={app.email_subject ?? ""}
                      >
                        <div className={app.email_subject ? "break-words leading-6 whitespace-normal" : "text-gray-400"}>
                          {app.email_subject || "-"}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-4 align-top text-right text-sm">
                        <div className="relative inline-block text-left" data-menu-root={app.id}>
                          <button
                            type="button"
                            onClick={() => setOpenMenuId((current) => (current === app.id ? null : app.id))}
                            aria-haspopup="menu"
                            aria-expanded={openMenuId === app.id}
                            className="flex h-8 w-8 items-center justify-center rounded-md border border-transparent text-gray-400 transition-colors hover:border-gray-200 hover:bg-gray-50 hover:text-gray-600"
                          >
                            ...
                          </button>
                          {openMenuId === app.id && (
                            <div
                              role="menu"
                              className="absolute right-0 z-10 mt-2 w-40 rounded-md border border-gray-200 bg-white p-1 shadow-lg"
                            >
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                openApplicationDetail(app.id);
                              }}
                              className="flex w-full items-center rounded px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-gray-50"
                            >
                              View Details
                            </button>
                            <button
                              type="button"
                              onClick={() => void openMergeModal(app)}
                              className="flex w-full items-center rounded px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-gray-50"
                            >
                              Merge
                            </button>
                            <button
                              type="button"
                              onClick={() => void openSplitModal(app)}
                              disabled={app.email_count < 2}
                              className="flex w-full items-center rounded px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-300"
                            >
                              Split
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleDelete(app)}
                              className="flex w-full items-center rounded px-3 py-2 text-left text-sm text-red-600 transition-colors hover:bg-red-50"
                            >
                              Delete
                            </button>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="bg-indigo-50/50">
                        <td colSpan={6} className="px-4 py-3">
                          <div className="ml-6 border-l-2 border-indigo-300 pl-4">
                            <div className="mb-2 text-xs font-medium text-indigo-700">
                              Application Timeline ({emails.length} emails)
                            </div>
                            {isLoadingRowEmails ? (
                              <div className="text-sm text-gray-400">Loading emails...</div>
                            ) : emails.length === 0 ? (
                              <div className="text-sm text-gray-400">No emails found</div>
                            ) : (
                              <div className="space-y-2">
                                {emails.map((email, index) => (
                                  <div key={email.id} className="flex items-start gap-3 text-sm">
                                    <div className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-indigo-200 text-xs font-medium text-indigo-700">
                                      {index + 1}
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="flex items-center gap-2">
                                        <span className="text-xs text-gray-400">
                                          {formatDateTime(email.email_date)}
                                        </span>
                                        <span className="truncate font-medium text-gray-900">
                                          {email.subject || "(No subject)"}
                                        </span>
                                      </div>
                                      <div className="text-xs text-gray-500">
                                        From: {email.sender || "-"}
                                      </div>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {mergeModalApp && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-4 text-lg font-bold">Merge Applications</h3>
            <p className="mb-4 text-sm text-gray-600">
              Select an application to merge into <strong>{mergeModalApp.company}</strong>.
              All emails and history from the selected app will be moved here.
            </p>
            {mergeOptionsLoading ? (
              <div className="mb-4 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
                Loading applications...
              </div>
            ) : (
              <select
                className="mb-4 w-full rounded border border-gray-300 px-3 py-2"
                value={selectedMergeId || ""}
                onChange={(event) => setSelectedMergeId(Number(event.target.value))}
              >
                <option value="">Select application...</option>
                {mergeOptions.map((app) => (
                  <option key={app.id} value={app.id}>
                    {app.company} - {app.job_title || "Unknown"} ({app.email_count} emails)
                  </option>
                ))}
              </select>
            )}
            <div className="flex justify-end gap-2">
              <button
                onClick={closeMergeModal}
                className="px-4 py-2 text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={() => void handleMerge()}
                disabled={!selectedMergeId || mergeOptionsLoading || actionSaving}
                className="rounded bg-indigo-600 px-4 py-2 text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {actionSaving ? "Merging..." : "Merge"}
              </button>
            </div>
          </div>
        </div>
      )}

      {splitModalApp && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <div className="w-full max-w-2xl rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-bold">Split Application</h3>
            <p className="mb-4 text-sm text-gray-600">
              Select emails to move into a new application record.
            </p>

            <div className="mb-4 max-h-48 overflow-y-auto rounded border">
              {splitEmailsLoading ? (
                <div className="px-3 py-4 text-sm text-gray-500">Loading emails...</div>
              ) : (
                splitEmails.map((email) => {
                  const checked = selectedSplitEmailIds.includes(email.id);
                  return (
                    <label
                      key={email.id}
                      className="flex cursor-pointer items-start gap-2 border-b px-3 py-2 last:border-b-0 hover:bg-gray-50"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => {
                          if (event.target.checked) {
                            setSelectedSplitEmailIds((current) => [...current, email.id]);
                          } else {
                            setSelectedSplitEmailIds((current) => current.filter((id) => id !== email.id));
                          }
                        }}
                      />
                      <div className="text-sm">
                        <div className="font-medium text-gray-900">
                          {email.subject || "(No subject)"}
                        </div>
                        <div className="text-xs text-gray-500">
                          {formatDateTime(email.email_date)} - {email.sender || "Unknown"}
                        </div>
                      </div>
                    </label>
                  );
                })
              )}
            </div>

            <div className="mb-4 grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs text-gray-500">New Company</label>
                <input
                  value={splitCompany}
                  onChange={(event) => setSplitCompany(event.target.value)}
                  className="w-full rounded border px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-gray-500">New Job Title</label>
                <input
                  value={splitJobTitle}
                  onChange={(event) => setSplitJobTitle(event.target.value)}
                  className="w-full rounded border px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-gray-500">New Req ID</label>
                <input
                  value={splitReqId}
                  onChange={(event) => setSplitReqId(event.target.value)}
                  className="w-full rounded border px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-gray-500">New Status</label>
                <select
                  value={splitStatus}
                  onChange={(event) => setSplitStatus(event.target.value)}
                  className="w-full rounded border px-3 py-2 text-sm"
                >
                  {STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  ))}
                </select>
              </div>
              <div className="col-span-2">
                <label className="mb-1 block text-xs text-gray-500">New Notes</label>
                <textarea
                  rows={2}
                  value={splitNotes}
                  onChange={(event) => setSplitNotes(event.target.value)}
                  className="w-full rounded border px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={closeSplitModal}
                className="px-4 py-2 text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={() => void handleSplit()}
                disabled={
                  actionSaving ||
                  splitEmailsLoading ||
                  selectedSplitEmailIds.length === 0 ||
                  selectedSplitEmailIds.length >= splitEmails.length
                }
                className="rounded bg-amber-600 px-4 py-2 text-white hover:bg-amber-700 disabled:opacity-50"
              >
                {actionSaving ? "Splitting..." : "Split"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
