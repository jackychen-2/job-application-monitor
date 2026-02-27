import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams, Link } from "react-router-dom";
import {
  getCachedEmail,
  getEmailPredictionRuns,
  getDropdownOptions,
  getLabel,
  getGroupMembers,
  listCachedEmails,
  listGroups,
  createGroup,
  updateGroup,
  deleteGroup,
  upsertLabel,
  replayEmailPipeline,
  streamEvalRun,
} from "../../api/eval";
import type { GroupMember } from "../../api/eval";
import type { ReplayLogEntry } from "../../api/eval";
import type {
  CachedEmailDetail,
  CorrectionEntry,
  DropdownOptions,
  EmailPredictionRun,
  EvalApplicationGroup,
  EvalLabel,
  EvalLabelInput,
  GroupingAnalysis,
} from "../../types/eval";

export default function ReviewEmail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const emailId = Number(id);

  // run_id scopes navigation and back-link to a specific eval run
  const runId = searchParams.get("run_id") ? Number(searchParams.get("run_id")) : undefined;
  const [predictionRunIdOverride, setPredictionRunIdOverride] = useState<number | undefined>(undefined);
  const predictionRunId = predictionRunIdOverride ?? runId;

  // Navigate to another email, preserving run_id context
  const navTo = (targetId: number) => {
    const qs = runId ? `?run_id=${runId}` : "";
    navigate(`/eval/review/${targetId}${qs}`);
  };

  // Back link to the queue, preserving run_id
  const queueHref = runId ? `/eval/review?run_id=${runId}` : "/eval/review";

  const [email, setEmail] = useState<CachedEmailDetail | null>(null);
  const [label, setLabel] = useState<EvalLabelInput>({});
  const [options, setOptions] = useState<DropdownOptions | null>(null);
  const [groups, setGroups] = useState<EvalApplicationGroup[]>([]);
  const [appSearch, setAppSearch] = useState("");
  const [appDropdownOpen, setAppDropdownOpen] = useState(false);
  const [showEmptyGroups, setShowEmptyGroups] = useState(false);
  const [newGroupCompany, setNewGroupCompany] = useState("");
  const [newGroupTitle, setNewGroupTitle] = useState("");
  const [showNewGroup, setShowNewGroup] = useState(false);
  const [editingGroupId, setEditingGroupId] = useState<number | null>(null);
  const [editGroupCompany, setEditGroupCompany] = useState("");
  const [editGroupTitle, setEditGroupTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [navIds, setNavIds] = useState<number[]>([]);
  const [, setTotalCount] = useState(0);
  const [labeledCount, setLabeledCount] = useState(0);
  const [savedLabelData, setSavedLabelData] = useState<EvalLabel | null>(null);
  // True once getLabel has resolved for the current email (null = not yet fetched).
  const [labelFetched, setLabelFetched] = useState(false);
  // Tracks whether we've done the initial pre-populate for the current email.
  // Prevents re-running pre-populate (and overwriting user edits) when groups
  // or savedLabelData changes after the user has started editing.
  const labelInitializedRef = useRef(false);

  // Group membership preview
  const [groupMembers, setGroupMembers] = useState<GroupMember[]>([]);
  const [loadingGroupMembers, setLoadingGroupMembers] = useState(false);

  // Decision log
  const [replayLogs, setReplayLogs] = useState<ReplayLogEntry[]>([]);
  const [replayLoading, setReplayLoading] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const [predictionRuns, setPredictionRuns] = useState<EmailPredictionRun[]>([]);
  const [predictionRunsLoading, setPredictionRunsLoading] = useState(false);
  const [rerunLoading, setRerunLoading] = useState(false);
  const [rerunStatus, setRerunStatus] = useState<string | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);
  const rerunEsRef = useRef<EventSource | null>(null);


  // Reset all per-email state when navigating to a different email/run context
  useEffect(() => {
    rerunEsRef.current?.close();
    rerunEsRef.current = null;
    setEmail(null);       // clear old email so pre-populate doesn't fire with stale data
    setPredictionRunIdOverride(undefined);
    setPredictionRuns([]);
    setPredictionRunsLoading(false);
    setRerunLoading(false);
    setRerunStatus(null);
    setLabel({});
    setSavedLabelData(null);
    setLabelFetched(false);
    labelInitializedRef.current = false;
    setReplayLogs([]);
    setReplayOpen(false);
    setGroupMembers([]);
  }, [emailId, runId]);

  useEffect(() => {
    return () => {
      rerunEsRef.current?.close();
      rerunEsRef.current = null;
    };
  }, []);

  // Fetch group members whenever the selected group changes
  useEffect(() => {
    const gid = label.correct_application_group_id;
    if (!gid) { setGroupMembers([]); return; }
    setLoadingGroupMembers(true);
    getGroupMembers(gid)
      .then(setGroupMembers)
      .catch(() => setGroupMembers([]))
      .finally(() => setLoadingGroupMembers(false));
  }, [label.correct_application_group_id]);

  const handleReplay = async () => {
    if (replayLoading) return;
    setReplayOpen(true);
    setReplayLoading(true);
    try {
      const { logs } = await replayEmailPipeline(emailId);
      setReplayLogs(logs);
      setTimeout(() => logEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch (e) {
      setReplayLogs([{ stage: "error", message: String(e), level: "error" }]);
    } finally {
      setReplayLoading(false);
    }
  };

  // Load prediction output (either review-scope run or user-selected historical run).
  useEffect(() => {
    if (!emailId) return;
    let cancelled = false;
    getCachedEmail(emailId, predictionRunId).then(e => { if (!cancelled) setEmail(e); });
    return () => { cancelled = true; };
  }, [emailId, predictionRunId]);

  // Load historical runs that contain this email's predictions.
  useEffect(() => {
    if (!emailId) return;
    let cancelled = false;
    setPredictionRunsLoading(true);
    getEmailPredictionRuns(emailId)
      .then(runs => { if (!cancelled) setPredictionRuns(runs); })
      .catch(() => { if (!cancelled) setPredictionRuns([]); })
      .finally(() => { if (!cancelled) setPredictionRunsLoading(false); });
    return () => { cancelled = true; };
  }, [emailId]);

  const handleRerunEval = useCallback(() => {
    if (rerunLoading) return;

    rerunEsRef.current?.close();
    const es = streamEvalRun(`review-email-${emailId}`, undefined, [emailId]);
    rerunEsRef.current = es;

    setRerunLoading(true);
    setRerunStatus("Repredicting this email...");

    const closeStream = () => {
      es.close();
      if (rerunEsRef.current === es) rerunEsRef.current = null;
    };

    const refreshRuns = () => {
      getEmailPredictionRuns(emailId)
        .then(setPredictionRuns)
        .catch(() => {});
    };

    es.onmessage = (event) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }

      if (msg.type === "log" && typeof msg.message === "string") {
        setRerunStatus(msg.message);
        return;
      }

      if (msg.type === "done" || msg.type === "cancelled") {
        setRerunLoading(false);
        const newRunId = Number(msg.run_id);
        if (Number.isFinite(newRunId)) {
          setPredictionRunIdOverride(newRunId);
          setRerunStatus(`Repredict complete. Showing Run #${newRunId}.`);
        } else {
          setRerunStatus(msg.type === "cancelled" ? "Repredict cancelled." : "Repredict complete.");
        }
        refreshRuns();
        closeStream();
        return;
      }

      if (msg.type === "error") {
        setRerunLoading(false);
        setRerunStatus(typeof msg.message === "string" ? `Repredict failed: ${msg.message}` : "Repredict failed.");
        closeStream();
      }
    };

    es.onerror = () => {
      setRerunLoading(false);
      setRerunStatus("Connection lost while repredicting.");
      closeStream();
    };
  }, [emailId, rerunLoading]);

  // Load labels scoped by URL run_id context.
  useEffect(() => {
    if (!emailId) return;
    let cancelled = false;
    getLabel(emailId, runId).then(l => {
      if (cancelled) return;
      setSavedLabelData(l);
      setLabelFetched(true);
    });
    return () => { cancelled = true; };
  }, [emailId, runId]);

  // Refresh navigation list scoped to the current run (or all emails if no run selected)
  const loadNav = () =>
    listCachedEmails({ page: 1, page_size: 9999, run_id: runId }).then(res => {
      setNavIds(res.items.map(e => e.id));
      setTotalCount(res.total);
      setLabeledCount(res.items.filter(e => e.review_status === "labeled").length);
    });

  // Load dropdown options, groups (scoped to current run), and nav
  // Re-runs when runId changes so the dropdown only shows current run's groups
  useEffect(() => {
    getDropdownOptions().then(setOptions);
    listGroups(runId).then(setGroups);
    loadNav();
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-populate labels from predictions, merging with any saved label.
  // Runs when email, groups, or savedLabelData changes (all three may arrive
  // asynchronously in any order). Saved values always win; prediction values
  // fill in any field the saved label left null/missing.
  // Once initialized for this email, does not re-run (prevents overwriting user edits
  // when groups reload or savedLabelData refreshes after a save).
  useEffect(() => {
    if (!email || !labelFetched) return;
    if (labelInitializedRef.current) return;

    // Try to find an EvalApplicationGroup that matches the predicted company+title
    let guessedGroupId: number | undefined;
    if (email.predicted_company && groups.length > 0) {
      const predComp = email.predicted_company.toLowerCase().trim();
      const predTitle = (email.predicted_job_title || "").toLowerCase().trim();
      const match = groups.find(
        g =>
          (g.company || "").toLowerCase().trim() === predComp &&
          (g.job_title || "").toLowerCase().trim() === predTitle
      );
      if (match) guessedGroupId = match.id;
    }

    const predCategory =
      email.predicted_is_job_related === true
        ? "job_application"
        : email.predicted_is_job_related === false
          ? "not_job_related"
          : email.predicted_email_category ?? undefined;

    const l = savedLabelData;
    setLabel({
      // Saved value wins; fall back to prediction when saved value is null/missing
      is_job_related: l?.is_job_related ?? (email.predicted_is_job_related ?? undefined),
      email_category: l?.email_category ?? predCategory ?? undefined,
      correct_company: l?.correct_company ?? (email.predicted_company ?? undefined),
      correct_job_title: l?.correct_job_title ?? (email.predicted_job_title ?? undefined),
      correct_status: l?.correct_status ?? (email.predicted_status ?? undefined),
      correct_recruiter_name: l?.correct_recruiter_name ?? undefined,
      correct_date_applied: l?.correct_date_applied ?? undefined,
      // Don't pre-assign a group for non-application emails
      correct_application_group_id:
        (l?.correct_application_group_id) ??
        (predCategory === "not_job_related"
          ? undefined
          : guessedGroupId),
      notes: l?.notes ?? undefined,
      review_status: l?.review_status ?? undefined,
    });
    labelInitializedRef.current = true;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email, groups, savedLabelData, labelFetched]);

  const currentIdx = navIds.indexOf(emailId);
  const prevId = currentIdx > 0 ? navIds[currentIdx - 1] : null;
  const nextId = currentIdx < navIds.length - 1 ? navIds[currentIdx + 1] : null;

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await upsertLabel(emailId, { ...label, review_status: "labeled", run_id: runId });
      // Reload label, nav (labeled count), and group members in parallel
      const gid = label.correct_application_group_id;
      const [refreshed] = await Promise.all([
        getLabel(emailId, runId),
        loadNav(),
        gid ? getGroupMembers(gid).then(setGroupMembers) : Promise.resolve(),
      ]);
      setSavedLabelData(refreshed);
    } finally {
      setSaving(false);
    }
  }, [emailId, label]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveAndNext = useCallback(async () => {
    if (!nextId) return;
    setSaving(true);
    try {
      // Save the label, reload nav count, but do NOT reload savedLabelData —
      // we're about to navigate away; updating savedLabelData here would race
      // with the next email's reset+fetch cycle and corrupt its pre-populate.
      await upsertLabel(emailId, { ...label, review_status: "labeled", run_id: runId });
      await loadNav();
    } finally {
      setSaving(false);
    }
    navTo(nextId);
  }, [emailId, label, nextId]); // eslint-disable-line react-hooks/exhaustive-deps

  const skip = useCallback(async () => {
    await upsertLabel(emailId, { review_status: "skipped" });
    loadNav(); // eslint-disable-line react-hooks/exhaustive-deps
    if (nextId) navTo(nextId);
  }, [emailId, nextId]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Group comparison helper.
  // pred = EvalPredictedGroup.id, gt = EvalApplicationGroup.id — different tables, IDs never match.
  // Always compare by company+title content extracted from the prediction vs the selected GT group.
  const normalizeForCompare = (v: string | null | undefined) =>
    (v || "")
      .normalize("NFKC")
      .toLowerCase()
      .replace(/[’`]/g, "'")
      .replace(/[‐‑‒–—]/g, "-")
      .replace(/\s+/g, " ")
      .trim();

  const groupDiffers = (pred: number | null | undefined, gt: number | null | undefined) => {
    if (gt === null || gt === undefined) return false;
    if (!email) return false;
    const selectedGroup = groups.find(g => g.id === gt);
    if (!selectedGroup) return true; // GT group exists but hasn't loaded yet → treat as mismatch
    const predCompany = normalizeForCompare(email.predicted_company);
    const predTitle = normalizeForCompare(email.predicted_job_title);
    const gtCompany = normalizeForCompare(selectedGroup.company);
    const gtTitle = normalizeForCompare(selectedGroup.job_title);
    // If pred is null (no prediction), any labeled group is a mismatch
    if (pred === null || pred === undefined) return true;
    return predCompany !== gtCompany || predTitle !== gtTitle;
  };

  const groupDiffClass = (pred: number | null | undefined, gt: number | null | undefined) => {
    // When GT says "not job-related", any non-null predicted group is wrong → red
    if (label.is_job_related === false) {
      return pred != null ? "border-l-4 border-red-400 bg-red-50" : "border-l-4 border-gray-200";
    }
    if (gt === null || gt === undefined) return "border-l-4 border-gray-200";
    return groupDiffers(pred, gt) ? "border-l-4 border-red-400 bg-red-50" : "border-l-4 border-green-400 bg-green-50";
  };

  if (!email) return <div className="p-8 text-gray-500">Loading...</div>;

  // Diff helpers
  const differs = (pred: string | null | undefined, gt: string | null | undefined) => {
    if (!gt || gt === undefined) return false; // no label = no diff
    const pn = normalizeForCompare(pred);
    const gn = normalizeForCompare(gt);
    return pn !== gn;
  };

  const diffClass = (pred: string | null | undefined, gt: string | null | undefined) => {
    // When GT says "not job-related", any non-null prediction is wrong → red
    if (label.is_job_related === false) {
      return pred ? "border-l-4 border-red-400 bg-red-50" : "border-l-4 border-gray-200";
    }
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
  if (label.correct_application_group_id != null) { totalFields++; if (groupDiffers(email.predicted_application_group, label.correct_application_group_id)) discrepancies++; }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to={queueHref} className="text-sm text-blue-600 hover:underline">← Queue</Link>
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
          <button onClick={() => prevId && navTo(prevId)} disabled={!prevId}
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
          <button onClick={() => nextId && navTo(nextId)} disabled={!nextId}
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
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-gray-700">Pipeline Predictions</h2>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleRerunEval}
                  disabled={rerunLoading}
                  className="px-2 py-1 text-xs rounded border border-indigo-300 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  title="Repredict this email (single-email eval run)"
                >
                  {rerunLoading ? "Repredicting..." : "Repredict (this email)"}
                </button>
                <select
                  value={predictionRunIdOverride == null ? "default" : String(predictionRunIdOverride)}
                  onChange={(e) => {
                    const v = e.target.value;
                    setPredictionRunIdOverride(v === "default" ? undefined : Number(v));
                  }}
                  className="px-2 py-1 text-xs rounded border border-gray-300 bg-white text-gray-700"
                  title="Select historical prediction run for this email"
                >
                  <option value="default">
                    {runId ? `Current review run #${runId}` : "Latest available run"}
                  </option>
                  {predictionRuns
                    .filter(r => !(runId != null && r.run_id === runId))
                    .map(r => (
                      <option key={r.run_id} value={String(r.run_id)}>
                        {`${r.run_name || `Run #${r.run_id}`} · ${new Date(r.started_at).toLocaleString()}`}
                      </option>
                    ))}
                </select>
              </div>
            </div>
            <div className="mt-1 text-xs text-gray-500">
              Prediction source: {predictionRunId ? `Run #${predictionRunId}` : "latest available run"}
            </div>
            {predictionRunsLoading && (
              <div className="mt-1 text-xs text-gray-500">Loading historical runs...</div>
            )}
            {rerunStatus && (
              <div className="mt-1 text-xs text-indigo-700">{rerunStatus}</div>
            )}
            {runId && predictionRunIdOverride && predictionRunIdOverride !== runId && (
              <div className="mt-1 text-xs text-amber-700">
                Labels are still scoped to Run #{runId}.
              </div>
            )}
          </div>
          <div className="p-4 flex-1 overflow-auto space-y-3">
            <div className={`p-2 rounded ${boolDiffClass(email.predicted_is_job_related, label.is_job_related)}`}>
              <span className="text-xs text-gray-500">Email Category</span>
              <div className="text-sm font-medium">
                {email.predicted_is_job_related === true ? (
                  <span className="text-green-700">Job Application</span>
                ) : email.predicted_is_job_related === false || email.predicted_email_category === "not_job_related" ? (
                  <span className="text-red-700">Not Related</span>
                ) : "—"}
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

            <div className={`p-2 rounded ${groupDiffClass(email.predicted_application_group, label.correct_application_group_id)}`}>
              <span className="text-xs text-gray-500">Application Group</span>
              <div className="text-sm font-medium">{email.predicted_application_group_display || email.predicted_application_group || "—"}</div>
            </div>

            {email.predicted_confidence !== null && (
              <div className="p-2 rounded border-l-4 border-gray-200">
                <span className="text-xs text-gray-500">Confidence</span>
                <div className="text-sm font-medium">{(email.predicted_confidence * 100).toFixed(0)}%</div>
              </div>
            )}

            {/* Decision Log — stored from eval run or replayed on demand */}
            <div className="mt-2 border-t pt-2">
              {email.decision_log_json ? (
                /* Stored log from the actual eval run — most accurate */
                (() => {
                  let stored: ReplayLogEntry[] = [];
                  try { stored = JSON.parse(email.decision_log_json!); } catch { return null; }
                  const levelColor = (l: string) =>
                    l === "error" ? "text-red-400" : l === "success" ? "text-green-400"
                    : l === "warn" ? "text-yellow-400" : "text-gray-300";
                  return (
                    <>
                      <div className="text-xs text-indigo-600 font-medium mb-1">
                        Pipeline Decision Log (from eval run)
                        {email.email_date && (
                          <span className="ml-2 text-gray-400 font-normal">
                            · {new Date(email.email_date).toLocaleString()}
                          </span>
                        )}
                      </div>
                      <div className="bg-gray-900 rounded p-2 max-h-72 overflow-y-auto font-mono text-xs space-y-0.5">
                        {stored.map((e, i) => (
                          <div key={i} className={levelColor(e.level)}>
                            <span className="text-gray-500 mr-1">[{e.stage}]</span>{e.message}
                          </div>
                        ))}
                      </div>
                    </>
                  );
                })()
              ) : (
                /* Fallback: fresh rule-based replay (for emails without stored log) */
                <>
                  <button
                    onClick={handleReplay}
                    disabled={replayLoading}
                    className="text-xs text-indigo-600 hover:text-indigo-800 disabled:opacity-50 font-medium"
                  >
                    {replayLoading ? "Running…" : replayOpen && replayLogs.length > 0 ? "↻ Re-run Decision Log" : "▶ Show Decision Log (rule-based replay)"}
                  </button>
                  {replayOpen && replayLogs.length > 0 && (
                    <div className="mt-2 bg-gray-900 rounded p-2 max-h-72 overflow-y-auto font-mono text-xs space-y-0.5">
                      {replayLogs.map((entry, i) => (
                        <div key={i}
                          className={entry.level === "error" ? "text-red-400" : entry.level === "success" ? "text-green-400"
                            : entry.level === "warn" ? "text-yellow-400" : "text-gray-300"}>
                          <span className="text-gray-500 mr-1">[{entry.stage}]</span>{entry.message}
                        </div>
                      ))}
                      <div ref={logEndRef} />
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT: Ground Truth Labels */}
        <div className="bg-white rounded-lg shadow overflow-hidden flex flex-col">
          <div className="bg-gray-50 px-4 py-3 border-b">
            <h2 className="text-sm font-semibold text-gray-700">Ground Truth Labels</h2>
          </div>
          <div className="p-4 flex-1 overflow-auto space-y-4">
            {/* Email Category */}
            <div>
              <label className="block text-xs text-gray-500 mb-1">Email Category</label>
              <div className="flex flex-wrap gap-2">
                {[
                  { cat: "job_application",     label: "Job Application", color: "green" },
                  { cat: "not_job_related",      label: "Not Related", color: "red" },
                  { cat: undefined,              label: "Unlabeled", color: "gray" },
                ].map(opt => {
                  const isActive = label.email_category === opt.cat;
                  return (
                    <button
                      key={String(opt.cat)}
                      onClick={() => {
                        if (opt.cat === "job_application") {
                          setLabel(p => ({ ...p, email_category: "job_application", is_job_related: true }));
                        } else if (opt.cat === "not_job_related") {
                          setLabel(p => ({
                            ...p,
                            email_category: "not_job_related",
                            is_job_related: false,
                            correct_company: undefined,
                            correct_job_title: undefined,
                            correct_status: undefined,
                            correct_recruiter_name: undefined,
                            correct_application_group_id: undefined,
                          }));
                        } else {
                          setLabel(p => ({ ...p, email_category: undefined, is_job_related: undefined }));
                        }
                      }}
                      className={`px-3 py-1.5 rounded text-sm border ${isActive
                        ? opt.color === "green"  ? "bg-green-100 border-green-500 text-green-700"
                        : opt.color === "red"    ? "bg-red-100 border-red-500 text-red-700"
                                                 : "bg-gray-100 border-gray-400 text-gray-700"
                        : "border-gray-200 text-gray-500 hover:bg-gray-50"}`}>
                      {opt.label}
                    </button>
                  );
                })}
              </div>
              {label.email_category === "not_job_related" && (
                <p className="text-xs text-red-500 mt-1">Not job-related — no field-level labels needed.</p>
              )}
              {label.correct_status === "Recruiter Reach-out" && (
                <p className="text-xs text-blue-600 mt-1">Recruiter outreach is captured as status.</p>
              )}
            </div>

            {/* Company, Title, Status, Group */}
            {(label.email_category === "job_application" ||
              (label.email_category == null && label.is_job_related !== false)) && (
              <>
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

            {/* Recruiter Name — only when status is recruiter outreach */}
            {label.correct_status === "Recruiter Reach-out" && (
              <div>
                <label className="block text-xs text-gray-500 mb-1">Recruiter Name</label>
                <input
                  value={label.correct_recruiter_name || ""}
                  onChange={e => setLabel(p => ({ ...p, correct_recruiter_name: e.target.value || undefined }))}
                  className="w-full border rounded px-3 py-2 text-sm"
                  placeholder="Recruiter or agency name..."
                />
              </div>
            )}

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

            <div className={`p-2 rounded ${groupDiffClass(email.predicted_application_group, label.correct_application_group_id)}`}>
              <label className="block text-xs text-gray-500 mb-1">
                Application Group
                {groupDiffers(email.predicted_application_group, label.correct_application_group_id) && (
                  <span className="ml-2 text-red-500">≠ predicted</span>
                )}
              </label>
              {/* Selected value display */}
              <div 
                onClick={() => { setAppDropdownOpen(!appDropdownOpen); if (appDropdownOpen) setShowEmptyGroups(false); }}
                className="w-full border rounded px-3 py-2 text-sm cursor-pointer bg-white hover:bg-gray-50 flex justify-between items-center">
                <span>
                  {label.correct_application_group_id 
                    ? (() => {
                        const app = groups.find(a => a.id === label.correct_application_group_id);
                        return app ? `${app.company} — ${app.job_title}` : `Application #${label.correct_application_group_id}`;
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
                    {/* Unselect option */}
                    {label.correct_application_group_id && !appSearch && (
                      <div
                        onClick={() => { setLabel(p => ({ ...p, correct_application_group_id: undefined })); setAppDropdownOpen(false); }}
                        className="px-3 py-2 text-sm cursor-pointer hover:bg-gray-100 text-gray-400 italic border-b">
                        — None —
                      </div>
                    )}
                    {(() => {
                      const emptyCount = groups.filter(g => g.email_count === 0 && !appSearch).length;
                      return emptyCount > 0 && !showEmptyGroups && !appSearch ? (
                        <div
                          className="px-3 py-1.5 text-xs text-gray-400 cursor-pointer hover:text-gray-600 border-b"
                          onClick={() => setShowEmptyGroups(true)}
                        >
                          + show {emptyCount} empty group{emptyCount !== 1 ? "s" : ""} (abandoned predictions)
                        </div>
                      ) : null;
                    })()}
                    {[...groups]
                      .sort((a, b) => b.email_count - a.email_count)
                      .filter(g => {
                        if (appSearch) {
                          const search = appSearch.toLowerCase();
                          return (g.company || "").toLowerCase().includes(search) ||
                                 (g.job_title || "").toLowerCase().includes(search);
                        }
                        // Hide 0-email groups unless explicitly shown
                        if (g.email_count === 0 && !showEmptyGroups) return false;
                        return true;
                      })
                      .map(g => (
                      <div
                        key={g.id}
                        onClick={() => {
                          setLabel(p => ({
                            ...p,
                            correct_application_group_id: g.id,
                            correct_company:   g.company   || p.correct_company,
                            correct_job_title: g.job_title || p.correct_job_title,
                          }));
                          setAppDropdownOpen(false);
                          setAppSearch("");
                        }}
                        className={`px-3 py-2 text-sm cursor-pointer hover:bg-gray-100 flex items-center justify-between gap-2 ${
                          label.correct_application_group_id === g.id ? "bg-blue-100" : ""
                        } ${g.email_count === 0 ? "opacity-50" : ""}`}>
                        {editingGroupId === g.id ? (
                          <div className="flex-1 flex flex-col gap-1" onClick={e => e.stopPropagation()}>
                            <input
                              value={editGroupCompany}
                              onChange={e => setEditGroupCompany(e.target.value)}
                              className="w-full border rounded px-2 py-0.5 text-xs"
                              placeholder="Company" />
                            <input
                              value={editGroupTitle}
                              onChange={e => setEditGroupTitle(e.target.value)}
                              className="w-full border rounded px-2 py-0.5 text-xs"
                              placeholder="Job Title" />
                            <div className="flex gap-1">
                              <button
                                onClick={async () => {
                                  const updated = await updateGroup(g.id, { company: editGroupCompany, job_title: editGroupTitle });
                                  setGroups(prev => prev.map(x => x.id === g.id ? { ...x, ...updated } : x));
                                  setEditingGroupId(null);
                                }}
                                className="px-2 py-0.5 bg-blue-600 text-white rounded text-xs">Save</button>
                              <button
                                onClick={() => setEditingGroupId(null)}
                                className="px-2 py-0.5 bg-gray-200 text-gray-700 rounded text-xs">Cancel</button>
                            </div>
                          </div>
                        ) : (
                          <span>
                            {g.company || "?"} — {g.job_title || "?"}
                            <span className={`ml-1.5 text-xs ${g.email_count > 0 ? "text-green-600 font-medium" : "text-gray-400"}`}>
                              ({g.email_count} email{g.email_count !== 1 ? "s" : ""})
                            </span>
                          </span>
                        )}
                        <div className="shrink-0 flex gap-1" onClick={e => e.stopPropagation()}>
                          <button
                            onClick={() => {
                              setEditingGroupId(g.id);
                              setEditGroupCompany(g.company || "");
                              setEditGroupTitle(g.job_title || "");
                            }}
                            className="text-gray-400 hover:text-blue-600 text-xs px-1"
                            title="Edit group">✎</button>
                          {g.email_count === 0 && (
                            <button
                              onClick={async () => {
                                if (!confirm(`Delete empty group "${g.company} — ${g.job_title}"?`)) return;
                                await deleteGroup(g.id);
                                setGroups(prev => prev.filter(x => x.id !== g.id));
                              }}
                              className="text-red-400 hover:text-red-600 text-xs px-1"
                              title="Delete empty group">×</button>
                          )}
                        </div>
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
                            const g = await createGroup({ company: newGroupCompany, job_title: newGroupTitle, eval_run_id: runId });
                            setGroups(prev => [g, ...prev]);
                            setLabel(p => ({
                              ...p,
                              correct_application_group_id: g.id,
                              correct_company:   g.company   || p.correct_company,
                              correct_job_title: g.job_title || p.correct_job_title,
                            }));
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


              {/* Group Member Preview */}
              {label.correct_application_group_id && (
                <div className="mt-2">
                  <div className="text-xs text-gray-500 font-medium mb-1 flex items-center gap-2">
                    Emails in this group
                    {loadingGroupMembers && <span className="text-gray-400">loading…</span>}
                    {!loadingGroupMembers && <span className="text-gray-400">({groupMembers.length})</span>}
                  </div>
                  {!loadingGroupMembers && groupMembers.length === 0 && (
                    <div className="text-xs text-gray-400 italic">No other labeled emails in this group yet.</div>
                  )}
                  {groupMembers.length > 0 && (
                    <div className="space-y-1 max-h-40 overflow-y-auto">
                      {groupMembers.map(m => (
                        <div
                          key={m.cached_email_id}
                          className={`flex items-start gap-1.5 text-xs p-1.5 rounded border ${
                            m.cached_email_id === emailId
                              ? "bg-blue-50 border-blue-200"
                              : "bg-gray-50 border-gray-100"
                          }`}
                        >
                          <div className="flex-1 min-w-0">
                            <div className="font-medium text-gray-700 truncate">{m.subject}</div>
                            <div className="text-gray-400 truncate">{m.sender}</div>
                          </div>
                          {m.cached_email_id !== emailId && (
                            <div className="shrink-0">
                              <Link to={`/eval/review/${m.cached_email_id}`}
                                className="text-[10px] text-blue-500 hover:underline">
                                review →
                              </Link>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
            </> /* end category fields */
            )}

            {/* Correction & Decision Log — inline in GT column */}
            {savedLabelData && (
              <CorrectionLog
                emailId={emailId}
                emailDate={email.email_date}
                correctionsJson={savedLabelData.corrections_json}
                groupingAnalysisJson={savedLabelData.grouping_analysis_json}
              />
            )}

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

// ── CorrectionLog ─────────────────────────────────────────
// Human-correction log displayed in the same [stage] message format as the
// Decision Log panel, below the three-column review area.

interface LogLine { stage: string; message: string; level: "info" | "success" | "warn" | "error" }

function CorrectionLog({
  emailDate,
  correctionsJson,
  groupingAnalysisJson,
}: {
  emailId: number;
  emailDate: string | null;
  correctionsJson: string | null;
  groupingAnalysisJson: string | null;
}) {
  const lines: LogLine[] = [];

  // ── Email timestamp header ────────────────────────────
  if (emailDate) {
    lines.push({
      stage: "email",
      message: `Email received: ${new Date(emailDate).toLocaleString()}`,
      level: "info",
    });
  }

  // ── Corrections (all entries — label is already run-scoped) ───
  const corrections: (CorrectionEntry & Record<string, unknown>)[] =
    correctionsJson ? (() => { try { return JSON.parse(correctionsJson); } catch { return []; } })() : [];

  for (const c of corrections) {
    const ts = new Date(c.at).toLocaleString();
    const runBadge = (c as Record<string, unknown>).run_id ? ` [Run #${(c as Record<string, unknown>).run_id}]` : "";
    if (c.field === "group_assignment") {
      const from = (c.from_group_name as string | null) ?? "(none)";
      const to = (c.to_group_name as string | null) ?? "(none)";
      lines.push({ stage: "group", message: `${ts}${runBadge}  ${from} → ${to}`, level: "info" });
    } else {
      const pred = String(c.predicted ?? "—");
      const corr = String(c.corrected ?? "—");
      lines.push({
        stage: c.field,
        message: `${ts}${runBadge}  "${pred}" → "${corr}"`,
        level: "warn",
      });
    }
  }

  // ── Grouping analysis v2 ─────────────────────────────
  let ga: GroupingAnalysis | null = null;
  try { ga = groupingAnalysisJson ? JSON.parse(groupingAnalysisJson) : null; } catch { ga = null; }

  if (ga) {
    const ts = new Date(ga.at).toLocaleString();
    lines.push({ stage: "grouping", message: `══ Grouping Analysis v2 (${ts}) ══`, level: "info" });

    // ── Decision summary (new) ──────────────────────────
    const dtColor = (dt: string | null): LogLine["level"] => {
      if (!dt) return "info";
      if (dt === "CONFIRMED") return "success";
      if (dt === "NEW_GROUP_CREATED") return "info";
      if (dt === "MARKED_NOT_JOB") return "warn";
      return "error";
    };
    lines.push({
      stage: "grouping",
      message: `  Decision:  ${ga.group_decision_type ?? "(unknown)"}${ga.grouping_failure_category ? `  →  ${ga.grouping_failure_category}` : ""}`,
      level: dtColor(ga.group_decision_type ?? null),
    });

    // ── Group-ID level (new) ────────────────────────────
    const _predName = ga.predicted_company
      ? `${ga.predicted_company} — ${ga.predicted_title ?? "Unknown"}`
      : "—";
    const _corrName = ga.correct_company
      ? `${ga.correct_company} — ${ga.correct_title ?? "Unknown"}`
      : "—";
    lines.push({ stage: "grouping", message: `  Predicted: #${ga.predicted_group_id ?? "—"} "${_predName}"  (size ${ga.predicted_group_size})`, level: "info" });
    lines.push({ stage: "grouping", message: `  Correct:   #${ga.correct_group_id ?? "—"} "${_corrName}"  (size ${ga.correct_group_size})`, level: "info" });
    lines.push({
      stage: "grouping",
      message: `  Cluster match: ${ga.group_id_match ? "✓ same cluster" : "✗ cluster mismatch"}`,
      level: ga.group_id_match ? "success" : "error",
    });

    // ── Dedup key analysis ──────────────────────────────
    lines.push({ stage: "grouping", message: `  ── Dedup Key ──`, level: "info" });
    lines.push({ stage: "grouping", message: `  Predicted  →  company: "${ga.predicted_company ?? "(none)"}"  title: "${ga.predicted_title ?? "(none)"}"`, level: ga.predicted_company ? "info" : "warn" });
    lines.push({ stage: "grouping", message: `  Correct    →  company: "${ga.correct_company ?? "(none)"}"  title: "${ga.correct_title ?? "(none)"}"`, level: ga.correct_company ? "info" : "warn" });
    lines.push({
      stage: "grouping",
      message: `  company key: ${ga.company_key_matches ? "✓ same" : `✗ "${ga.predicted_company_norm}" ≠ "${ga.correct_company_norm}"`}`,
      level: ga.company_key_matches ? "success" : "error",
    });
    lines.push({
      stage: "grouping",
      message: `  title key:   ${ga.title_key_matches ? "✓ same" : `✗ "${ga.predicted_title_norm}" ≠ "${ga.correct_title_norm}"`}`,
      level: ga.title_key_matches ? "success" : "error",
    });

    // ── Co-membership (extended) ────────────────────────
    if (ga.co_member_count > 0) {
      lines.push({ stage: "grouping", message: `  Co-members in correct group (${ga.co_member_count})  [format: email#id "subject" → pipeline's predicted group]`, level: "info" });
      // Per co-member: #id "subject" → predicted group name
      ga.co_member_email_ids.slice(0, 8).forEach((eid, i) => {
        const subj = ga.co_member_subjects?.[i] ?? "(no subject)";
        const rawDate = ga.co_member_email_dates?.[i];
        const dateLabel = rawDate ? ` (${new Date(rawDate).toLocaleDateString()})` : "";
        const predGrpName = ga.co_member_predicted_group_names?.[i];
        const issamePred = ga.co_member_predicted_group_ids?.[i] === ga.predicted_group_id;
        const grpLabel = predGrpName
          ? `→ predicted: ${predGrpName}${issamePred ? " ✓ same" : " ✗ different"}`
          : "→ predicted: (none)";
        lines.push({
          stage: "grouping",
          message: `    email #${eid}${dateLabel} "${subj}"  ${grpLabel}`,
          level: issamePred ? "success" : "error",
        });
      });
      if (ga.co_member_count > 8) {
        lines.push({ stage: "grouping", message: `    … and ${ga.co_member_count - 8} more`, level: "info" });
      }
      // Summary: are all co-members in the same predicted group?
      const uniquePredGroups = [...new Set((ga.co_member_predicted_group_ids ?? []).filter(Boolean))];
      if (uniquePredGroups.length > 0) {
        const allSame = uniquePredGroups.every(g => g === ga!.predicted_group_id);
        lines.push({
          stage: "grouping",
          message: `  ${allSame ? "✓ All co-members in same predicted group" : "✗ Co-members split across multiple predicted groups"}`,
          level: allSame ? "success" : "error",
        });
      }
    } else {
      lines.push({ stage: "grouping", message: "  No other labeled emails in this group yet (cluster size = 1)", level: "warn" });
    }
  }

  const levelColor = (l: LogLine["level"]) =>
    l === "error" ? "text-red-400"
    : l === "success" ? "text-green-400"
    : l === "warn" ? "text-yellow-400"
    : "text-gray-300";

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-2">Correction &amp; Decision Log</h3>
      {lines.length === 0 ? (
        <p className="text-xs text-gray-400 italic">
          No corrections recorded yet — save this label (assign a group or change any field) to start the log.
        </p>
      ) : (
        <div className="bg-gray-900 rounded p-3 max-h-72 overflow-y-auto font-mono text-xs space-y-0.5">
          {lines.map((ln, i) => (
            <div key={i} className={levelColor(ln.level)}>
              <span className="text-gray-500 mr-1">[{ln.stage}]</span>
              {ln.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
