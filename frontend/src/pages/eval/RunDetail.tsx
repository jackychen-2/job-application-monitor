import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getEvalRun, getEvalRunResults } from "../../api/eval";
import type { EvalRunDetail, EvalRunResult, EvalReport } from "../../types/eval";

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<EvalRunDetail | null>(null);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [errors, setErrors] = useState<EvalRunResult[]>([]);
  const [showErrors, setShowErrors] = useState(false);

  useEffect(() => {
    if (!id) return;
    getEvalRun(Number(id)).then(r => {
      setRun(r);
      if (r.report_json) {
        try { setReport(JSON.parse(r.report_json)); } catch {}
      }
    });
  }, [id]);

  const loadErrors = () => {
    if (!id) return;
    getEvalRunResults(Number(id), true).then(setErrors);
    setShowErrors(true);
  };

  if (!run) return <div className="p-8 text-gray-500">Loading...</div>;

  const pct = (v: number | null | undefined) => v != null ? `${(v * 100).toFixed(1)}%` : "—";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/eval/runs" className="text-sm text-blue-600 hover:underline">← All Runs</Link>
          <h1 className="text-2xl font-bold text-gray-900 mt-1">{run.run_name || `Run #${run.id}`}</h1>
          <p className="text-sm text-gray-500">
            {new Date(run.started_at).toLocaleString()} — {run.total_emails} emails, {run.labeled_emails} labeled
            — Cost: ${run.total_estimated_cost.toFixed(4)}
          </p>
        </div>
      </div>

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard label="Classification F1" value={run.classification_f1} />
        <MetricCard label="Field Accuracy" value={run.field_extraction_accuracy} />
        <MetricCard label="Status Accuracy" value={run.status_detection_accuracy} />
        <MetricCard label="Grouping ARI" value={run.grouping_ari} />
      </div>

      {/* Classification Detail */}
      {report && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Classification</h2>
          <div className="grid grid-cols-4 gap-4 mb-4">
            <MiniStat label="Accuracy" value={pct(report.classification.accuracy)} />
            <MiniStat label="Precision" value={pct(report.classification.precision)} />
            <MiniStat label="Recall" value={pct(report.classification.recall)} />
            <MiniStat label="F1" value={pct(report.classification.f1)} />
          </div>
          {/* Confusion Matrix */}
          <div className="grid grid-cols-3 gap-1 max-w-xs text-center text-sm">
            <div></div>
            <div className="font-medium text-gray-500">Pred +</div>
            <div className="font-medium text-gray-500">Pred −</div>
            <div className="font-medium text-gray-500">Actual +</div>
            <div className="bg-green-100 p-2 rounded">TP: {report.classification.tp}</div>
            <div className="bg-red-100 p-2 rounded">FN: {report.classification.fn}</div>
            <div className="font-medium text-gray-500">Actual −</div>
            <div className="bg-orange-100 p-2 rounded">FP: {report.classification.fp}</div>
            <div className="bg-green-50 p-2 rounded">TN: {report.classification.tn}</div>
          </div>

          {/* FP/FN examples */}
          {report.classification_fp_examples.length > 0 && (
            <details className="mt-4">
              <summary className="text-sm text-red-600 cursor-pointer">
                {report.classification_fp_examples.length} False Positives
              </summary>
              <ul className="mt-2 text-xs space-y-1">
                {report.classification_fp_examples.map(e => (
                  <li key={e.email_id} className="text-gray-600">
                    <Link to={`/eval/review/${e.email_id}`} className="text-blue-600 hover:underline">#{e.email_id}</Link>: {e.subject}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {report.classification_fn_examples.length > 0 && (
            <details className="mt-2">
              <summary className="text-sm text-orange-600 cursor-pointer">
                {report.classification_fn_examples.length} False Negatives
              </summary>
              <ul className="mt-2 text-xs space-y-1">
                {report.classification_fn_examples.map(e => (
                  <li key={e.email_id} className="text-gray-600">
                    <Link to={`/eval/review/${e.email_id}`} className="text-blue-600 hover:underline">#{e.email_id}</Link>: {e.subject}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {/* Field Extraction Detail */}
      {report && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Field Extraction</h2>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs uppercase">
                  <th className="text-left py-2">Field</th>
                  <th className="text-center py-2">Exact</th>
                  <th className="text-center py-2">Partial</th>
                  <th className="text-center py-2">Wrong</th>
                  <th className="text-center py-2">Missing</th>
                  <th className="text-center py-2">Exact Acc</th>
                  <th className="text-center py-2">Partial Acc</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { name: "Company", m: report.field_company },
                  { name: "Job Title", m: report.field_job_title },
                ].map(({ name, m }) => (
                  <tr key={name} className="border-t">
                    <td className="py-2 font-medium">{name}</td>
                    <td className="text-center text-green-600">{m.exact_match}</td>
                    <td className="text-center text-yellow-600">{m.partial_match}</td>
                    <td className="text-center text-red-600">{m.wrong}</td>
                    <td className="text-center text-gray-400">{m.missing_pred}</td>
                    <td className="text-center font-mono">{pct(m.exact_accuracy)}</td>
                    <td className="text-center font-mono">{pct(m.partial_accuracy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {report.field_error_examples.length > 0 && (
            <details className="mt-4">
              <summary className="text-sm text-red-600 cursor-pointer">
                {report.field_error_examples.length} Field Errors
              </summary>
              <ul className="mt-2 text-xs space-y-2">
                {report.field_error_examples.slice(0, 20).map(e => (
                  <li key={e.email_id} className="bg-gray-50 p-2 rounded">
                    <Link to={`/eval/review/${e.email_id}`} className="text-blue-600 hover:underline">#{e.email_id}</Link>: {e.subject}
                    <div className="mt-1 space-y-0.5">
                      {e.errors.map((err, i) => (
                        <div key={i} className="text-gray-600">
                          <span className="font-medium">{err.field}</span>: "{err.predicted}" → "{err.expected}"
                        </div>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {/* Status Detection */}
      {report && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Status Detection</h2>
          <MiniStat label="Overall Accuracy" value={pct(report.field_status.overall_accuracy)} />

          {/* Confusion Matrix */}
          {Object.keys(report.field_status.confusion_matrix).length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="text-xs">
                <thead>
                  <tr>
                    <th className="p-1 text-gray-500">Actual \ Pred</th>
                    {Object.keys(report.field_status.per_class).map(cls => (
                      <th key={cls} className="p-1 text-gray-500">{cls}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(report.field_status.confusion_matrix).map(([actual, preds]) => (
                    <tr key={actual}>
                      <td className="p-1 font-medium">{actual}</td>
                      {Object.keys(report.field_status.per_class).map(pred => (
                        <td key={pred} className={`p-1 text-center ${actual === pred ? "bg-green-100" : (preds[pred] || 0) > 0 ? "bg-red-50" : ""}`}>
                          {preds[pred] || 0}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Per-class metrics */}
          {Object.keys(report.field_status.per_class).length > 0 && (
            <div className="mt-4">
              <table className="text-sm min-w-full">
                <thead>
                  <tr className="text-xs text-gray-500 uppercase">
                    <th className="text-left py-1">Status</th>
                    <th className="text-center py-1">Precision</th>
                    <th className="text-center py-1">Recall</th>
                    <th className="text-center py-1">F1</th>
                    <th className="text-center py-1">Support</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(report.field_status.per_class).map(([cls, m]) => (
                    <tr key={cls} className="border-t">
                      <td className="py-1 font-medium">{cls}</td>
                      <td className="text-center font-mono">{pct(m.precision)}</td>
                      <td className="text-center font-mono">{pct(m.recall)}</td>
                      <td className="text-center font-mono">{pct(m.f1)}</td>
                      <td className="text-center">{m.support}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Grouping */}
      {report && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Grouping / Deduplication</h2>
          <div className="grid grid-cols-4 gap-4 mb-4">
            <MiniStat label="ARI" value={pct(report.grouping.ari)} />
            <MiniStat label="Homogeneity" value={pct(report.grouping.homogeneity)} />
            <MiniStat label="Completeness" value={pct(report.grouping.completeness)} />
            <MiniStat label="V-measure" value={pct(report.grouping.v_measure)} />
          </div>
          <div className="flex gap-4 text-sm">
            <span className="text-orange-600">{report.grouping.split_error_count} split errors</span>
            <span className="text-red-600">{report.grouping.merge_error_count} merge errors</span>
          </div>
          {report.grouping.split_errors.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-orange-600 cursor-pointer">Split errors</summary>
              <pre className="text-xs mt-1 bg-gray-50 p-2 rounded overflow-auto max-h-48">
                {JSON.stringify(report.grouping.split_errors, null, 2)}
              </pre>
            </details>
          )}
          {report.grouping.merge_errors.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-red-600 cursor-pointer">Merge errors</summary>
              <pre className="text-xs mt-1 bg-gray-50 p-2 rounded overflow-auto max-h-48">
                {JSON.stringify(report.grouping.merge_errors, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}

      {/* All Errors */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-4">Error Details</h2>
        {!showErrors ? (
          <button onClick={loadErrors} className="px-4 py-2 bg-gray-100 rounded text-sm hover:bg-gray-200">
            Load all error results
          </button>
        ) : (
          <div className="overflow-x-auto">
            <p className="text-sm text-gray-500 mb-2">{errors.length} results with errors</p>
            <table className="min-w-full text-xs divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-2 py-2 text-left">Email</th>
                  <th className="px-2 py-2">Class</th>
                  <th className="px-2 py-2">Company</th>
                  <th className="px-2 py-2">Title</th>
                  <th className="px-2 py-2">Status</th>
                  <th className="px-2 py-2">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {errors.slice(0, 100).map(r => (
                  <tr key={r.id}>
                    <td className="px-2 py-1 max-w-xs truncate">{r.email_subject}</td>
                    <td className="px-2 py-1 text-center">
                      <Dot correct={r.classification_correct} />
                    </td>
                    <td className="px-2 py-1 text-center">
                      <Dot correct={r.company_correct} />
                    </td>
                    <td className="px-2 py-1 text-center">
                      <Dot correct={r.job_title_correct} />
                    </td>
                    <td className="px-2 py-1 text-center">
                      <Dot correct={r.status_correct} />
                    </td>
                    <td className="px-2 py-1">
                      <Link to={`/eval/review/${r.cached_email_id}`} className="text-blue-600 hover:underline">Review</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: number | null }) {
  const pct = value !== null ? `${(value * 100).toFixed(1)}%` : "—";
  const color = value === null ? "text-gray-400" : value >= 0.8 ? "text-green-600" : value >= 0.5 ? "text-yellow-600" : "text-red-600";
  return (
    <div className="bg-white rounded-lg shadow p-4 text-center">
      <div className={`text-2xl font-bold ${color}`}>{pct}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-xs text-gray-500">{label}</span>
      <div className="text-lg font-bold font-mono">{value}</div>
    </div>
  );
}

function Dot({ correct }: { correct: boolean | null }) {
  if (correct === null) return <span className="text-gray-300">—</span>;
  return correct
    ? <span className="text-green-500">✓</span>
    : <span className="text-red-500">✗</span>;
}
