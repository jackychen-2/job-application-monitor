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
  if (!dateStr) return "—";
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

type EditField = { id: number; field: "status" | "job_title" | "req_id" | "company"; value: string };

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
    if (!dateStr) return "—";
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

  const startEdit = (id: number, field: EditField["field"], currentValue: string) => {
    setEditing({ id, field, value: currentValue });
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

  const renderEditableCell = (
    app: Application,
    field: "job_title" | "req_id" | "company",
    displayValue: string,
    placeholder: string
  ) => {
    const isEditing = editing?.id === app.id && editing?.field === field;

    if (isEditing) {
      return (
        <input
          type="text"
          value={editing.value}
          onChange={(e) => setEditing({ ...editing, value: e.target.value })}
          onKeyDown={handleKeyDown}
          onBlur={handleSave}
          autoFocus
          className="w-full text-sm border border-indigo-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          placeholder={placeholder}
        />
      );
    }

    return (
        <span
          onClick={(e) => {
            e.stopPropagation();
            const current =
              field === "job_title"
                ? app.job_title
                : field === "req_id"
                  ? app.req_id
                  : app.company;
            startEdit(app.id, field, current || "");
          }}
        className="cursor-pointer hover:bg-indigo-50 hover:text-indigo-700 px-1 py-0.5 rounded transition-colors"
        title="Click to edit"
      >
        {displayValue || <span className="text-gray-300 italic">Unknown</span>}
      </span>
    );
  };

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-2 py-3 w-8"></th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Company
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Job Title
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Req ID
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Status
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Date
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Email Subject
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                Actions
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
                    {/* Expand button */}
                    <td className="px-2 py-3 text-center" onClick={(e) => e.stopPropagation()}>
                      {app.email_count > 1 ? (
                        <button
                          onClick={(e) => toggleExpand(app.id, e)}
                          className="text-gray-400 hover:text-indigo-600 transition-colors p-1"
                          title={isExpanded ? "Collapse email chain" : "Expand email chain"}
                        >
                          {isExpanded ? "▼" : "▶"}
                          <span className="ml-0.5 text-xs bg-indigo-100 text-indigo-700 rounded-full px-1.5">
                            {app.email_count}
                          </span>
                        </button>
                      ) : (
                        <span className="text-gray-300 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm font-medium text-gray-900 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      {renderEditableCell(app, "company", app.company, "Enter company")}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600 max-w-[200px]" onClick={(e) => e.stopPropagation()}>
                      {renderEditableCell(app, "job_title", app.job_title || "", "Enter job title")}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      {renderEditableCell(app, "req_id", app.req_id || "", "Enter req ID")}
                    </td>
                    <td className="px-4 py-3 text-sm" onClick={(e) => e.stopPropagation()}>
                      {editing?.id === app.id && editing?.field === "status" ? (
                        <div className="flex items-center gap-1">
                          <select
                            value={editing.value}
                            onChange={(e) => setEditing({ ...editing, value: e.target.value })}
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
                          onClick={() => startEdit(app.id, "status", app.status)}
                          title="Click to change status"
                        >
                          <StatusBadge status={app.status} />
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">
                      {formatDate(app.email_date || app.created_at)}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500 max-w-[250px] truncate" title={app.email_subject ?? ""}>
                      {app.email_subject || "—"}
                    </td>
                    <td className="px-4 py-3 text-right text-sm whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => handleDelete(app.id, app.company)}
                        className="text-red-400 hover:text-red-600 transition-colors"
                        title="Delete"
                      >
                        🗑️
                      </button>
                    </td>
                  </tr>
                  {/* Expanded email chain row */}
                  {isExpanded && (
                    <tr key={`${app.id}-expanded`} className="bg-indigo-50/50">
                      <td colSpan={8} className="px-4 py-3">
                        <div className="ml-6 border-l-2 border-indigo-300 pl-4">
                          <div className="text-xs font-medium text-indigo-700 mb-2">
                            📧 Application Timeline ({emails.length} emails)
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
                                      From: {email.sender || "Unknown"}
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
