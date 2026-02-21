import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import {
  getCachedEmail,
  getDropdownOptions,
  getLabel,
  listCachedEmails,
  listGroups,
  createGroup,
  upsertLabel,
} from "../../api/eval";
import type {
  CachedEmailDetail,
  DropdownOptions,
  EvalApplicationGroup,
  EvalLabelInput,
} from "../../types/eval";

export default function ReviewEmail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const emailId = Number(id);

  const [email, setEmail] = useState<CachedEmailDetail | null>(null);
  const [label, setLabel] = useState<EvalLabelInput>({});
  const [options, setOptions] = useState<DropdownOptions | null>(null);
  const [groups, setGroups] = useState<EvalApplicationGroup[]>([]);
  const [appSearch, setAppSearch] = useState("");
  const [appDropdownOpen, setAppDropdownOpen] = useState(false);
  const [newGroupCompany, setNewGroupCompany] = useState("");
  const [newGroupTitle, setNewGroupTitle] = useState("");
  const [showNewGroup, setShowNewGroup] = useState(false);
  const [saving, setSaving] = useState(false);
  const [navIds, setNavIds] = useState<number[]>([]);
  const [, setTotalCount] = useState(0);
  const [labeledCount, setLabeledCount] = useState(0);


  // Load email data
  useEffect(() => {
    if (!emailId) return;
    getCachedEmail(emailId).then(setEmail);
    getLabel(emailId).then(l => {
      if (l) {
        setLabel({
          is_job_related: l.is_job_related,
          correct_company: l.correct_company,
          correct_job_title: l.correct_job_title,
          correct_status: l.correct_status,
          correct_recruiter_name: l.correct_recruiter_name,
          correct_date_applied: l.correct_date_applied,
          correct_application_group_id: l.correct_application_group_id,
          notes: l.notes,
          review_status: l.review_status,
        });
      } else {
        setLabel({});
      }
    });
  }, [emailId]);

  // Load dropdown options and groups
  useEffect(() => {
    getDropdownOptions().then(setOptions);
    listGroups().then(setGroups);
    // Load all email IDs for navigation
    listCachedEmails({ page: 1, page_size: 9999 }).then(res => {
      setNavIds(res.items.map(e => e.id));
      setTotalCount(res.total);
      setLabeledCount(res.items.filter(e => e.review_status === "labeled").length);
    });
  }, []);

  // Pre-populate labels from predictions if empty
  useEffect(() => {
    if (email && Object.keys(label).length === 0) {
      setLabel({
        is_job_related: email.predicted_is_job_related ?? undefined,
        correct_company: email.predicted_company ?? undefined,
        correct_job_title: email.predicted_job_title ?? undefined,
        correct_status: email.predicted_status ?? undefined,
        correct_application_group_id: email.predicted_application_group ?? undefined,
      });
    }
  }, [email]);

  const currentIdx = navIds.indexOf(emailId);
  const prevId = currentIdx > 0 ? navIds[currentIdx - 1] : null;
  const nextId = currentIdx < navIds.length - 1 ? navIds[currentIdx + 1] : null;

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await upsertLabel(emailId, { ...label, review_status: label.review_status || "labeled" });
    } finally {
      setSaving(false);
    }
  }, [emailId, label]);

  const saveAndNext = useCallback(async () => {
    await save();
    if (nextId) navigate(`/eval/review/${nextId}`);
  }, [save, nextId, navigate]);

  const skip = useCallback(async () => {
    await upsertLabel(emailId, { review_status: "skipped" });
    if (nextId) navigate(`/eval/review/${nextId}`);
  }, [emailId, nextId, navigate]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey) {
        if (e.key === "s") { e.preventDefault(); save(); }
        if (e.key === "Enter") { e.preventDefault(); saveAndNext(); }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [save, saveAndNext]);

  // Group comparison helper
  // Compare application groups - if no predicted ID, compare by company+title
  const groupDiffers = (pred: number | null | undefined, gt: number | null | undefined) => {
    if (gt === null || gt === undefined) return false;
    if (pred !== null && pred !== undefined) return pred !== gt;
    // No predicted ID - compare by content
    const selectedGroup = groups.find(g => g.id === gt);
    if (!selectedGroup || !email) return true;
    const predCompany = (email.predicted_company || "").toLowerCase().trim();
    const predTitle = (email.predicted_job_title || "").toLowerCase().trim();
    const gtCompany = (selectedGroup.company || "").toLowerCase().trim();
    const gtTitle = (selectedGroup.job_title || "").toLowerCase().trim();
    return predCompany !== gtCompany || predTitle !== gtTitle;
  };

  const groupDiffClass = (pred: number | null | undefined, gt: number | null | undefined) => {
    if (gt === null || gt === undefined) return "border-l-4 border-gray-200";
    return groupDiffers(pred, gt) ? "border-l-4 border-red-400 bg-red-50" : "border-l-4 border-green-400 bg-green-50";
  };

  if (!email) return <div className="p-8 text-gray-500">Loading...</div>;

  // Diff helpers
  const differs = (pred: string | null | undefined, gt: string | null | undefined) => {
    if (!gt || gt === undefined) return false; // no label = no diff
    const pn = (pred || "").trim().toLowerCase();
    const gn = (gt || "").trim().toLowerCase();
    return pn !== gn;
  };

  const diffClass = (pred: string | null | undefined, gt: string | null | undefined) => {
    if (gt === null || gt === undefined) return "border-l-4 border-gray-200"; // unlabeled
    return differs(pred, gt) ? "border-l-4 border-red-400 bg-red-50" : "border-l-4 border-green-400 bg-green-50";
  };

  const boolDiffers = (pred: boolean | null | undefined, gt: boolean | null | undefined) => {
    if (gt === null || gt === undefined) return false;
    return pred !== gt;
  };

  const boolDiffClass = (pred: boolean | null | undefined, gt: boolean | null | undefined) => {
    if (gt === null || gt === undefined) return "border-l-4 border-gray-200";
    return boolDiffers(pred, gt) ? "border-l-4 border-red-400 bg-red-50" : "border-l-4 border-green-400 bg-green-50";
  };

  // Count discrepancies
  let discrepancies = 0;
  let totalFields = 0;
  if (label.is_job_related !== null && label.is_job_related !== undefined) {
    totalFields++;
    if (boolDiffers(email.predicted_is_job_related, label.is_job_related)) discrepancies++;
  }
  if (label.correct_company) { totalFields++; if (differs(email.predicted_company, label.correct_company)) discrepancies++; }
  if (label.correct_job_title) { totalFields++; if (differs(email.predicted_job_title, label.correct_job_title)) discrepancies++; }
  if (label.correct_status) { totalFields++; if (differs(email.predicted_status, label.correct_status)) discrepancies++; }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/eval/review" className="text-sm text-blue-600 hover:underline">← Queue</Link>
          <span className="text-sm text-gray-500">
            Email {currentIdx + 1} of {navIds.length} ({labeledCount} labeled)
          </span>
          {totalFields > 0 && (
            <span className={`text-xs px-2 py-0.5 rounded-full ${discrepancies > 0 ? "bg-red-100 text-red-700" : "bg-green-100 text-green-700"}`}>
              {discrepancies} of {totalFields} fields differ
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={() => prevId && navigate(`/eval/review/${prevId}`)} disabled={!prevId}
            className="px-3 py-1.5 border rounded text-sm disabled:opacity-30">← Prev</button>
          <button onClick={skip} className="px-3 py-1.5 border rounded text-sm text-yellow-700 border-yellow-300 hover:bg-yellow-50">
            Skip
          </button>
          <button onClick={save} disabled={saving}
            className="px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50">
            {saving ? "Saving..." : "Save"} <span className="text-xs opacity-70">⌘S</span>
          </button>
          <button onClick={saveAndNext} disabled={saving || !nextId}
            className="px-3 py-1.5 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50">
            Save & Next <span className="text-xs opacity-70">⌘↵</span>
          </button>
          <button onClick={() => nextId && navigate(`/eval/review/${nextId}`)} disabled={!nextId}
            className="px-3 py-1.5 border rounded text-sm disabled:opacity-30">Next →</button>
        </div>
      </div>

      {/* Three-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4" style={{ minHeight: "70vh" }}>
        {/* LEFT: Source Email */}
        <div className="bg-white rounded-lg shadow overflow-hidden flex flex-col">
          <div className="bg-gray-50 px-4 py-3 border-b">
            <h2 className="text-sm font-semibold text-gray-700">Source Email</h2>
          </div>
          <div className="p-4 flex-1 overflow-auto space-y-3">
            <div>
              <span className="text-xs text-gray-500">Subject</span>
              <div className="text-sm font-medium">{email.subject || "(no subject)"}</div>
            </div>
            <div className="flex gap-4">
              <div>
                <span className="text-xs text-gray-500">From</span>
                <div className="text-sm">{email.sender}</div>
              </div>
              <div>
                <span className="text-xs text-gray-500">Date</span>
                <div className="text-sm">{email.email_date ? new Date(email.email_date).toLocaleString() : "—"}</div>
              </div>
            </div>
            <div>
              <span className="text-xs text-gray-500">Body</span>
              <pre className="text-xs text-gray-800 whitespace-pre-wrap mt-1 bg-gray-50 p-3 rounded max-h-96 overflow-auto font-mono">
                {email.body_text || "(empty)"}
              </pre>
            </div>
            <details className="text-xs text-gray-400">
              <summary>Metadata</summary>
              <div className="mt-1 space-y-1">
                <div>UID: {email.uid}</div>
                <div>Message-ID: {email.gmail_message_id}</div>
                <div>Thread-ID: {email.gmail_thread_id}</div>
              </div>
            </details>
          </div>
        </div>

        {/* MIDDLE: Pipeline Predictions */}
        <div className="bg-white rounded-lg shadow overflow-hidden flex flex-col">
          <div className="bg-gray-50 px-4 py-3 border-b">
            <h2 className="text-sm font-semibold text-gray-700">Pipeline Predictions</h2>
          </div>
          <div className="p-4 flex-1 overflow-auto space-y-3">
            <div className={`p-2 rounded ${boolDiffClass(email.predicted_is_job_related, label.is_job_related)}`}>
              <span className="text-xs text-gray-500">Is Job Related</span>
              <div className="text-sm font-medium">
                {email.predicted_is_job_related === null ? "—" :
                  <span className={email.predicted_is_job_related ? "text-green-700" : "text-red-700"}>
                    {email.predicted_is_job_related ? "Yes" : "No"}
                  </span>
                }
              </div>
            </div>

            <div className={`p-2 rounded ${diffClass(email.predicted_company, label.correct_company)}`}>
              <span className="text-xs text-gray-500">Company</span>
              <div className="text-sm font-medium">{email.predicted_company || "—"}</div>
            </div>

            <div className={`p-2 rounded ${diffClass(email.predicted_job_title, label.correct_job_title)}`}>
              <span className="text-xs text-gray-500">Job Title</span>
              <div className="text-sm font-medium">{email.predicted_job_title || "—"}</div>
            </div>

            <div className={`p-2 rounded ${diffClass(email.predicted_status, label.correct_status)}`}>
              <span className="text-xs text-gray-500">Status</span>
              <div className="text-sm font-medium">{email.predicted_status || "—"}</div>
            </div>

            <div className="p-2 rounded border-l-4 border-gray-200">
              <span className="text-xs text-gray-500">Application Group</span>
              <div className="text-sm font-medium">{email.predicted_application_group_display || email.predicted_application_group || "—"}</div>
            </div>

            {email.predicted_confidence !== null && (
              <div className="p-2 rounded border-l-4 border-gray-200">
                <span className="text-xs text-gray-500">Confidence</span>
                <div className="text-sm font-medium">{(email.predicted_confidence * 100).toFixed(0)}%</div>
              </div>
            )}
          </div>
        </div>

        {/* RIGHT: Ground Truth Labels */}
        <div className="bg-white rounded-lg shadow overflow-hidden flex flex-col">
          <div className="bg-gray-50 px-4 py-3 border-b">
            <h2 className="text-sm font-semibold text-gray-700">Ground Truth Labels</h2>
          </div>
          <div className="p-4 flex-1 overflow-auto space-y-4">
            {/* Is Job Related */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">Is Job Related</label>
              <div className="flex gap-2">
                {[
                  { val: true, label: "Yes", color: "green" },
                  { val: false, label: "No", color: "red" },
                  { val: undefined, label: "Unlabeled", color: "gray" },
                ].map(opt => (
                  <button key={String(opt.val)} onClick={() => setLabel(p => ({ ...p, is_job_related: opt.val as boolean | undefined }))}
                    className={`px-3 py-1.5 rounded text-sm border ${label.is_job_related === opt.val
                      ? opt.color === "green" ? "bg-green-100 border-green-500 text-green-700"
                        : opt.color === "red" ? "bg-red-100 border-red-500 text-red-700"
                          : "bg-gray-100 border-gray-400 text-gray-700"
                      : "border-gray-200 text-gray-500 hover:bg-gray-50"}`}>
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Company */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">Correct Company</label>
              <input
                list="company-options"
                value={label.correct_company || ""}
                onChange={e => setLabel(p => ({ ...p, correct_company: e.target.value || undefined }))}
                className="w-full border rounded px-3 py-2 text-sm"
                placeholder="Select or type company..."
              />
              <datalist id="company-options">
                {options?.companies.map(c => <option key={c} value={c} />)}
              </datalist>
            </div>

            {/* Job Title */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">Correct Job Title</label>
              <input
                list="title-options"
                value={label.correct_job_title || ""}
                onChange={e => setLabel(p => ({ ...p, correct_job_title: e.target.value || undefined }))}
                className="w-full border rounded px-3 py-2 text-sm"
                placeholder="Select or type title..."
              />
              <datalist id="title-options">
                {options?.job_titles.map(t => <option key={t} value={t} />)}
              </datalist>
            </div>

            {/* Status */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">Correct Status</label>
              <select
                value={label.correct_status || ""}
                onChange={e => setLabel(p => ({ ...p, correct_status: e.target.value || undefined }))}
                className="w-full border rounded px-3 py-2 text-sm">
                <option value="">— Select —</option>
                {options?.statuses.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            {/* Application Group */}
            <div className={`p-2 rounded ${groupDiffClass(email.predicted_application_group, label.correct_application_group_id)}`}>
              <label className="block text-xs text-gray-500 mb-1">
                Application Group
                {groupDiffers(email.predicted_application_group, label.correct_application_group_id) && (
                  <span className="ml-2 text-red-500">≠ predicted</span>
                )}
              </label>
              {/* Selected value display */}
              <div 
                onClick={() => setAppDropdownOpen(!appDropdownOpen)}
                className="w-full border rounded px-3 py-2 text-sm cursor-pointer bg-white hover:bg-gray-50 flex justify-between items-center">
                <span>
                  {label.correct_application_group_id 
                    ? (() => {
                        const app = groups.find(a => a.id === label.correct_application_group_id);
                        return app ? `[${app.id}] ${app.company} — ${app.job_title}` : `Application #${label.correct_application_group_id}`;
                      })()
                    : "— Select Application —"}
                </span>
                <span className="text-gray-400">{appDropdownOpen ? "▲" : "▼"}</span>
              </div>
              
              {/* Dropdown panel */}
              {appDropdownOpen && (
                <div className="border rounded mt-1 bg-white shadow-lg">
                  <input
                    type="text"
                    placeholder="Search company or job title..."
                    value={appSearch}
                    onChange={e => setAppSearch(e.target.value)}
                    className="w-full border-b px-3 py-2 text-sm focus:outline-none"
                    autoFocus
                  />
                  <div className="max-h-48 overflow-y-auto">
                    {groups
                      .filter(g => {
                        if (!appSearch) return true;
                        const search = appSearch.toLowerCase();
                        return (g.company || "").toLowerCase().includes(search) || 
                               (g.job_title || "").toLowerCase().includes(search);
                      })
                      .map(g => (
                      <div
                        key={g.id}
                        onClick={() => { setLabel(p => ({ ...p, correct_application_group_id: g.id })); setAppDropdownOpen(false); setAppSearch(""); }}
                        className={`px-3 py-2 text-sm cursor-pointer hover:bg-gray-100 ${
                          label.correct_application_group_id === g.id ? "bg-blue-100" : ""
                        }`}>
                        [{g.id}] {g.company || "?"} — {g.job_title || "?"} ({g.email_count} emails)
                      </div>
                    ))}
                    {groups.length === 0 && (
                      <div className="px-3 py-2 text-sm text-gray-400">No groups found. Create one below.</div>
                    )}
                  </div>
                  {/* Create new group */}
                  <div className="border-t p-2">
                    <button 
                      onClick={() => setShowNewGroup(!showNewGroup)}
                      className="text-xs text-blue-600 hover:underline">
                      ＋ Create New Group
                    </button>
                    {showNewGroup && (
                      <div className="mt-2 space-y-2">
                        <input 
                          value={newGroupCompany} 
                          onChange={e => setNewGroupCompany(e.target.value)}
                          className="w-full border rounded px-2 py-1 text-xs" 
                          placeholder="Company" />
                        <input 
                          value={newGroupTitle} 
                          onChange={e => setNewGroupTitle(e.target.value)}
                          className="w-full border rounded px-2 py-1 text-xs" 
                          placeholder="Job Title" />
                        <button 
                          onClick={async () => {
                            const g = await createGroup({ company: newGroupCompany, job_title: newGroupTitle });
                            setGroups(prev => [g, ...prev]);
                            setLabel(p => ({ ...p, correct_application_group_id: g.id }));
                            setShowNewGroup(false);
                            setAppDropdownOpen(false);
                            setNewGroupCompany("");
                            setNewGroupTitle("");
                          }}
                          className="px-3 py-1 bg-blue-600 text-white rounded text-xs">
                          Create
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Notes */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">Notes</label>
              <textarea
                value={label.notes || ""}
                onChange={e => setLabel(p => ({ ...p, notes: e.target.value || undefined }))}
                className="w-full border rounded px-3 py-2 text-sm" rows={3}
                placeholder="Optional notes..." />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
