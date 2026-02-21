import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listEvalRuns, deleteEvalRun } from "../../api/eval";
import type { EvalRun } from "../../types/eval";

export default function EvalRuns() {
  const [runs, setRuns] = useState<EvalRun[]>([]);

  const load = () => listEvalRuns().then(setRuns);
  useEffect(() => { load(); }, []);

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this run?")) return;
    await deleteEvalRun(id);
    load();
  };

  const pct = (v: number | null) => v !== null ? `${(v * 100).toFixed(1)}%` : "—";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Evaluation Runs</h1>
        <Link to="/eval" className="text-sm text-blue-600 hover:underline">← Dashboard</Link>
      </div>

      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Run</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Emails</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Class. F1</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Field Acc</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Status Acc</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Group ARI</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Cost</th>
              <th className="px-4 py-3 w-24"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {runs.map(run => (
              <tr key={run.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 text-sm font-medium">
                  <Link to={`/eval/runs/${run.id}`} className="text-blue-600 hover:underline">
                    {run.run_name || `Run #${run.id}`}
                  </Link>
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">
                  {new Date(run.started_at).toLocaleString()}
                </td>
                <td className="px-4 py-3 text-sm text-center">
                  {run.labeled_emails}/{run.total_emails}
                </td>
                <td className="px-4 py-3 text-sm text-center font-mono">{pct(run.classification_f1)}</td>
                <td className="px-4 py-3 text-sm text-center font-mono">{pct(run.field_extraction_accuracy)}</td>
                <td className="px-4 py-3 text-sm text-center font-mono">{pct(run.status_detection_accuracy)}</td>
                <td className="px-4 py-3 text-sm text-center font-mono">{pct(run.grouping_ari)}</td>
                <td className="px-4 py-3 text-sm text-center text-gray-500">
                  ${run.total_estimated_cost.toFixed(4)}
                </td>
                <td className="px-4 py-3 text-sm">
                  <button onClick={() => handleDelete(run.id)} className="text-red-500 hover:text-red-700 text-xs">
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {runs.length === 0 && (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-gray-400">No runs yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
