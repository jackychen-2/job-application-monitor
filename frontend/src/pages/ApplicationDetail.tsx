import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  deleteApplication,
  getApplication,
  getApplicationMergeEvents,
  listApplications,
  mergeApplications,
  splitApplication,
  unmergeApplication,
  updateApplication,
} from "../api/client";
import type {
  Application,
  ApplicationDetail as AppDetail,
  ApplicationMergeEvent,
} from "../types";
import { STATUSES } from "../types";
import StatusBadge from "../components/StatusBadge";
import { useJourney } from "../journey/JourneyContext";

export default function ApplicationDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { activeJourney } = useJourney();
  const [app, setApp] = useState<AppDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingStatus, setEditingStatus] = useState(false);
  const [newStatus, setNewStatus] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [showMergeModal, setShowMergeModal] = useState(false);
  const [showSplitModal, setShowSplitModal] = useState(false);
  const [applications, setApplications] = useState<Application[]>([]);
  const [mergeEvents, setMergeEvents] = useState<ApplicationMergeEvent[]>([]);
  const [selectedMergeId, setSelectedMergeId] = useState<number | null>(null);
  const [selectedSplitEmailIds, setSelectedSplitEmailIds] = useState<number[]>([]);
  const [splitCompany, setSplitCompany] = useState("");
  const [splitJobTitle, setSplitJobTitle] = useState("");
  const [splitReqId, setSplitReqId] = useState("");
  const [splitStatus, setSplitStatus] = useState("已申请");
  const [splitNotes, setSplitNotes] = useState("");

  const loadApplicationDetail = async (applicationId: number): Promise<void> => {
    setLoading(true);
    try {
      const [data, events] = await Promise.all([
        getApplication(applicationId),
        getApplicationMergeEvents(applicationId),
      ]);
      setApp(data);
      setNotes(data.notes ?? "");
      setMergeEvents(events);
    } catch {
      navigate("/");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!id) return;
    loadApplicationDetail(Number(id));
  }, [id, navigate, activeJourney?.id]);

  const handleStatusChange = async () => {
    if (!app || !newStatus) return;
    setSaving(true);
    try {
      await updateApplication(app.id, { status: newStatus });
      await loadApplicationDetail(app.id);
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
      await loadApplicationDetail(app.id);
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
      await loadApplicationDetail(app.id);
      setShowMergeModal(false);
      setSelectedMergeId(null);
    } catch (err) {
      console.error("Failed to merge:", err);
      alert("Failed to merge applications");
    } finally {
      setSaving(false);
    }
  };

  const handleUnmerge = async (event: ApplicationMergeEvent) => {
    if (!app || event.undone_at) return;
    const sourceName = `${event.source_company || "Unknown"} — ${event.source_job_title || "Unknown"}`;
    if (!window.confirm(`Unmerge ${sourceName}? This will restore moved emails/history to a new application record.`)) {
      return;
    }

    setSaving(true);
    try {
      await unmergeApplication(app.id, event.id);
      await loadApplicationDetail(app.id);
    } catch (err) {
      console.error("Failed to unmerge:", err);
      alert("Failed to unmerge this application");
    } finally {
      setSaving(false);
    }
  };

  const openMergeModal = async () => {
    const appsData = await listApplications({ page_size: 100 });
    setApplications(appsData.items.filter(a => a.id !== app?.id));
    setShowMergeModal(true);
  };

  const openSplitModal = () => {
    if (!app || !app.linked_emails || app.linked_emails.length < 2) return;
    setSplitCompany(app.company);
    setSplitJobTitle(app.job_title || "");
    setSplitReqId(app.req_id || "");
    setSplitStatus(app.status || "已申请");
    setSplitNotes(app.notes || "");
    setSelectedSplitEmailIds([app.linked_emails[0].id]);
    setShowSplitModal(true);
  };

  const handleSplit = async () => {
    if (!app || selectedSplitEmailIds.length === 0) return;
    setSaving(true);
    try {
      const result = await splitApplication(app.id, {
        email_ids: selectedSplitEmailIds,
        company: splitCompany,
        job_title: splitJobTitle || undefined,
        req_id: splitReqId || undefined,
        status: splitStatus || undefined,
        notes: splitNotes || undefined,
      });
      setShowSplitModal(false);
      await loadApplicationDetail(app.id);
      navigate(`/applications/${result.new_application_id}`);
    } catch (err) {
      console.error("Failed to split:", err);
      const raw = err instanceof Error ? err.message : String(err);
      const parsed =
        raw.match(/"detail"\s*:\s*"([^"]+)"/)?.[1] ||
        raw.match(/detail['"]?\s*:\s*['"]([^'"]+)['"]/)?.[1] ||
        raw;
      alert(parsed || "Failed to split this application");
    } finally {
      setSaving(false);
    }
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

  function mergeSourceLabel(source: string): string {
    if (source === "system_dedupe") return "System Auto-Merge";
    if (source === "manual") return "Manual Merge";
    return source;
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
            <p className="text-gray-500 text-sm mt-0.5">Req ID: {app.req_id || "—"}</p>
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

      {/* Merge History */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <h2 className="text-sm font-medium text-gray-700 mb-3">Merge History</h2>
        {mergeEvents.length === 0 ? (
          <div className="space-y-2">
            <p className="text-sm text-gray-400">No merges yet</p>
            {app.linked_emails.length >= 2 && (
              <button
                onClick={openSplitModal}
                className="rounded-md border border-amber-300 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-50"
              >
                Unmerge By Splitting Emails
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {mergeEvents.map((event) => (
              <div key={event.id} className="rounded border border-gray-200 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-gray-900">
                      {event.source_company || "Unknown"} — {event.source_job_title || "Unknown"}
                    </div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {mergeSourceLabel(event.merge_source)} ·
                      {" "}
                      Req ID: {event.source_req_id || "—"} ·
                      {" "}Merged at {formatDateTime(event.merged_at)}
                    </div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      Moved {event.moved_email_count} email{event.moved_email_count !== 1 ? "s" : ""}
                      {" "}and {event.moved_history_count} history record{event.moved_history_count !== 1 ? "s" : ""}
                    </div>
                    {event.undone_at && (
                      <div className="text-xs text-green-700 mt-1">
                        Unmerged at {formatDateTime(event.undone_at)}
                      </div>
                    )}
                  </div>
                  {!event.undone_at && (
                    <button
                      onClick={() => handleUnmerge(event)}
                      disabled={saving}
                      className="rounded-md border border-amber-300 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50"
                    >
                      Unmerge
                    </button>
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

      {/* Split Modal (manual unmerge correction when no merge event exists) */}
      {showSplitModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl p-6 max-w-2xl w-full mx-4">
            <h3 className="text-lg font-bold mb-2">Unmerge By Splitting Emails</h3>
            <p className="text-sm text-gray-600 mb-4">
              Select emails to move into a new application record.
            </p>

            <div className="border rounded mb-4 max-h-48 overflow-y-auto">
              {app.linked_emails.map((email) => {
                const checked = selectedSplitEmailIds.includes(email.id);
                return (
                  <label key={email.id} className="flex items-start gap-2 px-3 py-2 border-b last:border-b-0 cursor-pointer hover:bg-gray-50">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedSplitEmailIds((prev) => [...prev, email.id]);
                        } else {
                          setSelectedSplitEmailIds((prev) => prev.filter((id) => id !== email.id));
                        }
                      }}
                    />
                    <div className="text-sm">
                      <div className="font-medium text-gray-900">{email.subject || "(No subject)"}</div>
                      <div className="text-xs text-gray-500">{formatDateTime(email.email_date)} · {email.sender || "Unknown"}</div>
                    </div>
                  </label>
                );
              })}
            </div>

            <div className="grid grid-cols-2 gap-3 mb-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1">New Company</label>
                <input
                  value={splitCompany}
                  onChange={(e) => setSplitCompany(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">New Job Title</label>
                <input
                  value={splitJobTitle}
                  onChange={(e) => setSplitJobTitle(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">New Req ID</label>
                <input
                  value={splitReqId}
                  onChange={(e) => setSplitReqId(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">New Status</label>
                <select
                  value={splitStatus}
                  onChange={(e) => setSplitStatus(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm"
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
              <div className="col-span-2">
                <label className="block text-xs text-gray-500 mb-1">New Notes</label>
                <textarea
                  rows={2}
                  value={splitNotes}
                  onChange={(e) => setSplitNotes(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setShowSplitModal(false);
                  setSelectedSplitEmailIds([]);
                }}
                className="px-4 py-2 text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={handleSplit}
                disabled={
                  saving ||
                  selectedSplitEmailIds.length === 0 ||
                  selectedSplitEmailIds.length >= app.linked_emails.length
                }
                className="px-4 py-2 bg-amber-600 text-white rounded hover:bg-amber-700 disabled:opacity-50"
              >
                {saving ? "Splitting..." : "Split"}
              </button>
            </div>
          </div>
        </div>
      )}

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
