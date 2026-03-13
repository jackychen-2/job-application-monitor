import SankeyFlow from "../components/SankeyFlow";
import type { FlowData } from "../types";

const PREVIEW_FLOW: FlowData = {
  total: 160,
  status_counts: [
    { status: "Recruiter Reach-out", count: 5 },
    { status: "已申请", count: 60 },
    { status: "OA", count: 14 },
    { status: "面试", count: 3 },
    { status: "Offer", count: 2 },
    { status: "拒绝", count: 76 },
  ],
  transitions: [
    { from_status: "已申请", to_status: "OA", count: 18 },
    { from_status: "已申请", to_status: "拒绝", count: 32 },
    { from_status: "OA", to_status: "面试", count: 7 },
    { from_status: "OA", to_status: "拒绝", count: 9 },
    { from_status: "Recruiter Reach-out", to_status: "面试", count: 2 },
    { from_status: "Recruiter Reach-out", to_status: "拒绝", count: 4 },
    { from_status: "面试", to_status: "Offer", count: 2 },
    { from_status: "面试", to_status: "拒绝", count: 6 },
    { from_status: "Offer", to_status: "Onboarding", count: 1 },
  ],
};

export default function FlowPreview() {
  return (
    <div className="min-h-screen bg-gray-50 px-6 py-10">
      <div className="mx-auto max-w-4xl">
        <p className="mb-4 text-sm text-gray-400">Flow preview — mock data, no login required.</p>
        <SankeyFlow flowData={PREVIEW_FLOW} height={400} />
      </div>
    </div>
  );
}
