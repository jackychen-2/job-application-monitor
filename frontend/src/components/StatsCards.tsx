import type { Stats } from "../types";

interface Props {
  stats: Stats | null;
  loading: boolean;
}

const CARDS = [
  { key: "total", label: "Total", color: "bg-indigo-500" },
  { key: "Recruiter Reach-out", label: "Recruiter", color: "bg-orange-500" },
  { key: "已申请", label: "已申请", color: "bg-gray-500" },
  { key: "OA", label: "OA", color: "bg-cyan-500" },
  { key: "面试", label: "面试", color: "bg-blue-500" },
  { key: "Offer", label: "Offer", color: "bg-green-500" },
  { key: "Onboarding", label: "Onboarding", color: "bg-teal-500" },
  { key: "拒绝", label: "拒绝", color: "bg-red-500" },
] as const;

export default function StatsCards({ stats, loading }: Props) {
  const getCount = (key: string): number => {
    if (!stats) return 0;
    if (key === "total") return stats.total_applications;
    const found = stats.status_breakdown.find((s) => s.status === key);
    return found?.count ?? 0;
  };

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-8 gap-4">
      {CARDS.map(({ key, label, color }) => (
        <div
          key={key}
          className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 flex flex-col items-center"
        >
          <div className={`text-3xl font-bold ${loading ? "animate-pulse text-gray-300" : "text-gray-900"}`}>
            {loading ? "—" : getCount(key)}
          </div>
          <div className="mt-1 text-sm text-gray-500 flex items-center gap-1.5">
            <span className={`inline-block w-2 h-2 rounded-full ${color}`} />
            {label}
          </div>
        </div>
      ))}
    </div>
  );
}
