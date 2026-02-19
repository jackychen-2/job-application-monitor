import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getApplication, updateApplication, deleteApplication, mergeApplications, listApplications } from "../api/client";
import type { ApplicationDetail as AppDetail, Application } from "../types";
import { STATUSES } from "../types";
import StatusBadge from "../components/StatusBadge";

export default function ApplicationDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [app, setApp] = useState<AppDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingStatus, setEditingStatus] = useState(false);
  const [newStatus, setNewStatus] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [showMergeModal, setShowMergeModal] = useState(false);
  const [applications, setApplications] = useState<Application[]>([]);
  const [selectedMergeId, setSelectedMergeId] = useState<number | null>(null);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    getApplication(Number(id))
      .then((data) => {
        setApp(data);
        setNotes(data.notes ?? "");
      })
      .catch(() => navigate("/"))
      .finally(() => setLoading(false));
  }, [id, navigate]);

  const handleStatusChange = async () => {
    if (!app || !newStatus) return;
    setSaving(true);
    try {
      await updateApplication(app.id, { status: newStatus });
      const updated = await getApplication(app.id);
      setApp(updated);
      setEditingStatus(false);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveNotes = async () => {
    if (!app) return;
    setSaving(true);
    try {
      await updateApplication(app.id, { notes });
      const updated = await getApplication(app.id);
      setApp(updated);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!app) return;
    if (window.confirm(`Delete application at ${app.company}?`)) {
      await deleteApplication(app.id);
      navigate("/");
    }
  };

  const handleMerge = async () => {
    if (!app || !selectedMergeId) return;
    setSaving(true);
    try {
      await mergeApplications(app.id, selectedMergeId);
      const updated = await getApplication(app.id);
      setApp(updated);
      setShowMergeModal(false);
      setSelectedMergeId(null);
    } catch (err) {
      console.error("Failed to merge:", err);
      alert("Failed to merge applications");
    } finally {
      setSaving(false);
    }
  };

  const openMergeModal = async () => {
    const appsData = await listApplications({ page_size: 100 });
    setApplications(appsData.items.filter(a => a.id !== app?.id));
    setShowMergeModal(true);
  };

  function formatDateTime(dateStr: string | null): string {
    if (!dateStr) return "—";
    try {
      return new Date(dateStr).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return dateStr;
    }
  }

  if (loading) {
    return <div className="text-center py-12 text-gray-400">Loading...</div>;
  }
  if (!app) {
    return <div className="text-center py-12 text-gray-400">Application not found</div>;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Back link */}
      <button
        onClick={() => navigate("/")}
        className="text-sm text-indigo-600 hover:text-indigo-800 flex items-center gap-1"
      >
        ← Back to Dashboard
      </button>

      {/* Header */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{app.company}</h1>
            <p className="text-gray-600 mt-1">{app.job_title || "No job title"}</p>
          </div>
          <div className="flex items-center gap-3">
            {editingStatus ? (
              <div className="flex items-center gap-2">
                <select
                  value={newStatus}
                  onChange={(e) => setNewStatus(e.target.value)}
                  className="text-sm border rounded px-2 py-1"
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                <button
                  onClick={handleStatusChange}
                  disabled={saving}
                  className="text-sm text-green-600 hover:text-green-800"
                >
                  Save
                </button>
                <button
                  onClick={() => setEditingStatus(false)}
                  className="text-sm text-gray-400 hover:text-gray-600"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <span
                onClick={() => { setEditingStatus(true); setNewStatus(app.status); }}
                className="cursor-pointer"
                title="Click to change status"
              >
                <StatusBadge status={app.status} />
              </span>
            )}
            <button
              onClick={openMergeModal}
              className="text-blue-400 hover:text-blue-600 text-sm px-2 py-1"
              title="Merge with another application"
            >
              🔗 Merge
            </button>
            <button
              onClick={handleDelete}
              className="text-red-400 hover:text-red-600 text-lg"
              title="Delete"
            >
              🗑️
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 mt-4 text-sm">
          <div>
            <span className="text-gray-500">Email Date:</span>{" "}
            <span className="text-gray-900">{formatDateTime(app.email_date)}</span>
          </div>
          <div>
            <span className="text-gray-500">Source:</span>{" "}
            <span className="text-gray-900">{app.source}</span>
          </div>
          <div className="col-span-2">
            <span className="text-gray-500">Email Subject:</span>{" "}
            <span className="text-gray-900">{app.email_subject || "—"}</span>
          </div>
          <div className="col-span-2">
            <span className="text-gray-500">Sender:</span>{" "}
            <span className="text-gray-900">{app.email_sender || "—"}</span>
          </div>
        </div>
      </div>

      {/* Notes */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <h2 className="text-sm font-medium text-gray-700 mb-2">Notes</h2>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:border-indigo-500 focus:ring-indigo-500"
          placeholder="Add notes about this application..."
        />
        <button
          onClick={handleSaveNotes}
          disabled={saving || notes === (app.notes ?? "")}
          className="mt-2 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 hover:bg-indigo-500"
        >
          Save Notes
        </button>
      </div>

      {/* Linked Emails (Thread) */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <h2 className="text-sm font-medium text-gray-700 mb-3">
          Linked Emails
          {app.email_count > 0 && (
            <span className="ml-2 inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
              {app.email_count} email{app.email_count !== 1 ? "s" : ""} in thread
            </span>
          )}
        </h2>
        {!app.linked_emails || app.linked_emails.length === 0 ? (
          <p className="text-sm text-gray-400">No linked emails</p>
        ) : (
          <div className="space-y-3">
            {app.linked_emails.map((email) => (
              <div key={email.id} className="border-l-2 border-indigo-200 pl-3 py-1">
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-gray-400 text-xs w-32 flex-shrink-0">
                    {formatDateTime(email.email_date)}
                  </span>
                  <span className="text-gray-900 font-medium truncate flex-1">
                    {email.subject || "(No subject)"}
                  </span>
                </div>
                <div className="text-xs text-gray-500 mt-0.5 ml-32">
                  From: {email.sender || "Unknown"}
                  {email.gmail_thread_id && (
                    <span className="ml-2 text-gray-400">
                      Thread: {email.gmail_thread_id.slice(0, 12)}...
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Status History */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <h2 className="text-sm font-medium text-gray-700 mb-3">Status History</h2>
        {app.status_history.length === 0 ? (
          <p className="text-sm text-gray-400">No history yet</p>
        ) : (
          <div className="space-y-3">
            {app.status_history.map((h) => (
              <div key={h.id} className="flex items-center gap-3 text-sm">
                <span className="text-gray-400 w-36 flex-shrink-0">
                  {formatDateTime(h.changed_at)}
                </span>
                <div className="flex items-center gap-2">
                  {h.old_status && (
                    <>
                      <StatusBadge status={h.old_status} />
                      <span className="text-gray-400">→</span>
                    </>
                  )}
                  <StatusBadge status={h.new_status} />
                </div>
                {h.change_source && (
                  <span className="text-xs text-gray-400">({h.change_source})</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Merge Modal */}
      {showMergeModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-bold mb-4">Merge Applications</h3>
            <p className="text-sm text-gray-600 mb-4">
              Select an application to merge into <strong>{app.company}</strong>.
              All emails and history from the selected app will be moved here.
            </p>
            <select
              className="w-full border border-gray-300 rounded px-3 py-2 mb-4"
              value={selectedMergeId || ""}
              onChange={(e) => setSelectedMergeId(Number(e.target.value))}
            >
              <option value="">Select application...</option>
              {applications.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.company} — {a.job_title || "Unknown"} ({a.email_count} emails)
                </option>
              ))}
            </select>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setShowMergeModal(false); setSelectedMergeId(null); }}
                className="px-4 py-2 text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={handleMerge}
                disabled={!selectedMergeId || saving}
                className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
              >
                {saving ? "Merging..." : "Merge"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
