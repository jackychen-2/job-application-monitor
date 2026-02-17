import { useNavigate } from "react-router-dom";
import type { Application } from "../types";
import StatusBadge from "./StatusBadge";
import { deleteApplication, updateApplication } from "../api/client";
import { useState } from "react";
import { STATUSES } from "../types";

interface Props {
  applications: Application[];
  loading: boolean;
  onRefresh: () => void;
}

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

type EditField = { id: number; field: "status" | "job_title" | "company"; value: string };

export default function ApplicationTable({ applications, loading, onRefresh }: Props) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState<EditField | null>(null);

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
    field: "job_title" | "company",
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
          startEdit(app.id, field, (field === "job_title" ? app.job_title : app.company) || "");
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
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Company
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Job Title
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
            {applications.map((app) => (
              <tr
                key={app.id}
                className="hover:bg-gray-50 cursor-pointer transition-colors"
                onClick={() => navigate(`/applications/${app.id}`)}
              >
                <td className="px-4 py-3 text-sm font-medium text-gray-900 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                  {renderEditableCell(app, "company", app.company, "Enter company")}
                </td>
                <td className="px-4 py-3 text-sm text-gray-600 max-w-[200px]" onClick={(e) => e.stopPropagation()}>
                  {renderEditableCell(app, "job_title", app.job_title || "", "Enter job title")}
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
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
