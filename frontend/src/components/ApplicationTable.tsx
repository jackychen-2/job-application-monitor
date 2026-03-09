import { useNavigate } from "react-router-dom";
import type { Application, LinkedEmail } from "../types";
import StatusBadge from "./StatusBadge";
import { deleteApplication, updateApplication, getApplicationEmails } from "../api/client";
import { useState, Fragment } from "react";
import { STATUSES } from "../types";

interface Props {
  applications: Application[];
  loading: boolean;
  onRefresh: () => void;
}

// Cache for expanded emails
type EmailCache = Record<number, LinkedEmail[]>;

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "-";
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return dateStr;
  }
}

type EditField = { id: number; field: "status"; value: string };

export default function ApplicationTable({ applications, loading, onRefresh }: Props) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState<EditField | null>(null);
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  const [emailCache, setEmailCache] = useState<EmailCache>({});
  const [loadingEmails, setLoadingEmails] = useState<Set<number>>(new Set());

  const toggleExpand = async (appId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    const newExpanded = new Set(expandedRows);
    
    if (newExpanded.has(appId)) {
      newExpanded.delete(appId);
    } else {
      newExpanded.add(appId);
      // Fetch emails if not cached
      if (!emailCache[appId]) {
        setLoadingEmails(prev => new Set(prev).add(appId));
        try {
          const emails = await getApplicationEmails(appId);
          setEmailCache(prev => ({ ...prev, [appId]: emails }));
        } catch (err) {
          console.error("Failed to load emails:", err);
        } finally {
          setLoadingEmails(prev => {
            const next = new Set(prev);
            next.delete(appId);
            return next;
          });
        }
      }
    }
    setExpandedRows(newExpanded);
  };

  const formatDateTime = (dateStr: string | null): string => {
    if (!dateStr) return "-";
    try {
      return new Date(dateStr).toLocaleString("en-US", {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
      });
    } catch { return dateStr; }
  };

  const handleSave = async () => {
    if (!editing) return;
    const update: Record<string, string> = {};
    update[editing.field] = editing.value;
    await updateApplication(editing.id, update);
    setEditing(null);
    onRefresh();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") setEditing(null);
  };

  const handleDelete = async (id: number, company: string) => {
    if (window.confirm(`Delete application at ${company}?`)) {
      await deleteApplication(id);
      onRefresh();
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-8 text-center text-gray-400">
        Loading applications...
      </div>
    );
  }

  if (applications.length === 0) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-8 text-center text-gray-400">
        No applications found. Scan your email to get started!
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Company
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Status
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Last Activity
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Role
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Preview
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                More
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {applications.map((app) => {
              const isExpanded = expandedRows.has(app.id);
              const emails = emailCache[app.id] || [];
              const isLoadingEmails = loadingEmails.has(app.id);

              return (
                <Fragment key={app.id}>
                  <tr
                    className={`hover:bg-gray-50 cursor-pointer transition-colors ${isExpanded ? 'bg-indigo-50/30' : ''}`}
                    onClick={() => navigate(`/applications/${app.id}`)}
                  >
                    <td className="px-4 py-4 align-top">
                      <div className="flex items-start gap-3">
                        {app.email_count > 1 ? (
                          <button
                            onClick={(e) => toggleExpand(app.id, e)}
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
                          <div className="truncate text-sm font-semibold text-gray-900">
                            {app.company}
                          </div>
                          {app.req_id ? (
                            <div className="mt-1 truncate text-xs text-gray-500">
                              Req ID: {app.req_id}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-4 align-top text-sm" onClick={(e) => e.stopPropagation()}>
                      {editing?.id === app.id && editing?.field === "status" ? (
                        <div className="flex items-center gap-1">
                          <select
                            value={editing.value}
                            onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                            onKeyDown={handleKeyDown}
                            onBlur={handleSave}
                            className="text-xs border rounded px-1 py-0.5"
                            autoFocus
                          >
                            {STATUSES.map((s) => (
                              <option key={s} value={s}>{s}</option>
                            ))}
                          </select>
                        </div>
                      ) : (
                        <span
                          onClick={() => setEditing({ id: app.id, field: "status", value: app.status })}
                          title="Click to change status"
                          className="inline-flex"
                        >
                          <StatusBadge status={app.status} />
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-4 align-top text-sm text-gray-500 whitespace-nowrap">
                      {formatDate(app.email_date || app.created_at)}
                    </td>
                    <td className="px-4 py-4 align-top text-sm text-gray-600 max-w-[240px]" title={app.job_title ?? ""}>
                      <div className={app.job_title ? "truncate" : "text-gray-400"}>
                        {app.job_title || "-"}
                      </div>
                    </td>
                    <td className="px-4 py-4 align-top text-sm text-gray-500 max-w-[280px]" title={app.email_subject ?? ""}>
                      <div className={app.email_subject ? "truncate" : "text-gray-400"}>
                        {app.email_subject || "-"}
                      </div>
                    </td>
                    <td className="px-4 py-4 align-top text-right text-sm whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      <details
                        className="relative inline-block text-left"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <summary className="flex h-8 w-8 cursor-pointer list-none items-center justify-center rounded-md border border-transparent text-gray-400 transition-colors hover:border-gray-200 hover:bg-gray-50 hover:text-gray-600 [&::-webkit-details-marker]:hidden">
                          ...
                        </summary>
                        <div className="absolute right-0 z-10 mt-2 w-32 rounded-md border border-gray-200 bg-white p-1 shadow-lg">
                          <button
                            onClick={() => handleDelete(app.id, app.company)}
                            className="flex w-full items-center rounded px-3 py-2 text-left text-sm text-red-600 transition-colors hover:bg-red-50"
                          >
                            Delete
                          </button>
                        </div>
                      </details>
                    </td>
                  </tr>
                  {/* Expanded email chain row */}
                  {isExpanded && (
                    <tr key={`${app.id}-expanded`} className="bg-indigo-50/50">
                      <td colSpan={6} className="px-4 py-3">
                        <div className="ml-6 border-l-2 border-indigo-300 pl-4">
                          <div className="text-xs font-medium text-indigo-700 mb-2">
                            Application Timeline ({emails.length} emails)
                          </div>
                          {isLoadingEmails ? (
                            <div className="text-sm text-gray-400">Loading emails...</div>
                          ) : emails.length === 0 ? (
                            <div className="text-sm text-gray-400">No emails found</div>
                          ) : (
                            <div className="space-y-2">
                              {emails.map((email, idx) => (
                                <div key={email.id} className="flex items-start gap-3 text-sm">
                                  <div className="flex-shrink-0 w-5 h-5 rounded-full bg-indigo-200 text-indigo-700 text-xs flex items-center justify-center font-medium">
                                    {idx + 1}
                                  </div>
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                      <span className="text-gray-400 text-xs">
                                        {formatDateTime(email.email_date)}
                                      </span>
                                      <span className="font-medium text-gray-900 truncate">
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
  );
}
