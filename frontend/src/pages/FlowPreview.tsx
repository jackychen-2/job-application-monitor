import SankeyFlow from "../components/SankeyFlow";
import type { FlowData } from "../types";

// Matches production data to reproduce real-world layout issues
const PREVIEW_FLOW: FlowData = {
  total: 160,
  status_counts: [
    { status: "Recruiter Reach-out", count: 5 },
    { status: "已申请", count: 72 },
    { status: "OA", count: 1 },
    { status: "面试", count: 4 },
    { status: "Offer", count: 2 },
    { status: "拒绝", count: 76 },
  ],
  transitions: [
    { from_status: "Applications", to_status: "Recruiter Reach-out", count: 5 },
    { from_status: "Applications", to_status: "已申请", count: 72 },
    { from_status: "已申请", to_status: "OA", count: 1 },
    { from_status: "已申请", to_status: "拒绝", count: 50 },
    { from_status: "OA", to_status: "面试", count: 1 },
    { from_status: "Recruiter Reach-out", to_status: "面试", count: 3 },
    { from_status: "Recruiter Reach-out", to_status: "拒绝", count: 2 },
    { from_status: "面试", to_status: "Offer", count: 2 },
    { from_status: "面试", to_status: "拒绝", count: 4 },
    { from_status: "Offer", to_status: "Onboarding", count: 1 },
  ],
};

// Snapshot from the current backend data (owner_user_id=1, journey_id=1).
// Keeping this in preview makes the "mock looks good, dashboard looks different"
// issue reproducible without login.
const DASHBOARD_SNAPSHOT_FLOW: FlowData = {
  total: 141,
  status_counts: [
    { status: "OA", count: 1 },
    { status: "Offer", count: 2 },
    { status: "Recruiter Reach-out", count: 1 },
    { status: "已申请", count: 63 },
    { status: "拒绝", count: 72 },
    { status: "面试", count: 2 },
  ],
  transitions: [
    { from_status: "Applications", to_status: "Offer", count: 1 },
    { from_status: "Applications", to_status: "Recruiter Reach-out", count: 2 },
    { from_status: "Applications", to_status: "Unknown", count: 1 },
    { from_status: "Applications", to_status: "已申请", count: 84 },
    { from_status: "Applications", to_status: "拒绝", count: 55 },
    { from_status: "Applications", to_status: "面试", count: 2 },
    { from_status: "Recruiter Reach-out", to_status: "拒绝", count: 1 },
    { from_status: "已申请", to_status: "OA", count: 1 },
    { from_status: "已申请", to_status: "Offer", count: 1 },
    { from_status: "已申请", to_status: "拒绝", count: 20 },
    { from_status: "已申请", to_status: "面试", count: 3 },
    { from_status: "面试", to_status: "拒绝", count: 2 },
  ],
};

const DIFF_NOTES = [
  "Mock preview has 160 total apps; current dashboard snapshot has 141.",
  "Mock preview routes Recruiter Reach-out into 面试, but the dashboard snapshot routes it into 拒绝.",
  "Dashboard snapshot also includes Applications -> Unknown, which changes the node ordering and spacing.",
];

export default function FlowPreview() {
  return (
    <div className="min-h-screen bg-gray-50 px-6 py-10">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="rounded-3xl border border-gray-200 bg-white px-6 py-5 shadow-sm">
          <p className="text-sm font-medium text-gray-900">Flow preview</p>
          <p className="mt-1 text-sm text-gray-500">
            Comparing the hand-tuned mock sample against a snapshot of the current dashboard data.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {DIFF_NOTES.map((note) => (
              <span
                key={note}
                className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-700"
              >
                {note}
              </span>
            ))}
          </div>
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <SankeyFlow
            flowData={PREVIEW_FLOW}
            height={340}
            showSummary={false}
            title="Mock Sample"
          />
          <SankeyFlow
            flowData={DASHBOARD_SNAPSHOT_FLOW}
            height={340}
            showSummary={false}
            title="Dashboard Snapshot"
          />
        </div>
      </div>
    </div>
  );
}
