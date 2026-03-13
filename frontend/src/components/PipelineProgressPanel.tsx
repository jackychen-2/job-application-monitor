import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import {
  addWeeks,
  eachDayOfInterval,
  eachMonthOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  parseISO,
  startOfDay,
  startOfMonth,
  startOfWeek,
  subDays,
} from "date-fns";
import type { DailyCount, HourlyCount, Stats } from "../types";

interface Props {
  stats: Stats | null;
  loading: boolean;
}

type RangeKey = "24h" | "7d" | "30d" | "90d" | "all";
type RangeOption = {
  key: RangeKey;
  label: string;
  summaryLabel: string;
};

type ChartPoint = {
  label: string;
  fullLabel: string;
  count: number;
};

const RANGE_OPTIONS: RangeOption[] = [
  { key: "24h", label: "24H", summaryLabel: "24H" },
  { key: "7d", label: "7D", summaryLabel: "7D" },
  { key: "30d", label: "30D", summaryLabel: "30D" },
  { key: "90d", label: "90D", summaryLabel: "90D" },
  { key: "all", label: "All", summaryLabel: "All Time" },
];

function toDateKey(date: Date): string {
  return format(date, "yyyy-MM-dd");
}

function sumRange(start: Date, end: Date, countMap: Map<string, number>): number {
  return eachDayOfInterval({ start, end }).reduce((total, day) => total + (countMap.get(toDateKey(day)) ?? 0), 0);
}

function buildDailyChartData(start: Date, end: Date, countMap: Map<string, number>, shortLabel: string): ChartPoint[] {
  return eachDayOfInterval({ start, end }).map((day) => ({
    label: format(day, shortLabel),
    fullLabel: format(day, "MMM d, yyyy"),
    count: countMap.get(toDateKey(day)) ?? 0,
  }));
}

function buildHourlyChartData(data: HourlyCount[]): ChartPoint[] {
  return [...data]
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    .map((entry) => {
      const hour = parseISO(entry.timestamp);
      return {
        label: format(hour, "ha"),
        fullLabel: format(hour, "MMM d, yyyy h:mm a"),
        count: entry.count,
      };
    });
}

function buildWeeklyChartData(start: Date, end: Date, countMap: Map<string, number>): ChartPoint[] {
  const points: ChartPoint[] = [];
  let cursor = startOfWeek(start, { weekStartsOn: 1 });

  while (cursor <= end) {
    const bucketEnd = endOfWeek(cursor, { weekStartsOn: 1 });
    let total = 0;
    for (const day of eachDayOfInterval({ start: cursor, end: bucketEnd })) {
      if (day < start || day > end) continue;
      total += countMap.get(toDateKey(day)) ?? 0;
    }
    points.push({
      label: format(cursor, "MMM d"),
      fullLabel: `${format(cursor, "MMM d")} - ${format(bucketEnd < end ? bucketEnd : end, "MMM d, yyyy")}`,
      count: total,
    });
    cursor = addWeeks(cursor, 1);
  }

  return points;
}

function buildMonthlyChartData(start: Date, end: Date, countMap: Map<string, number>): ChartPoint[] {
  return eachMonthOfInterval({ start: startOfMonth(start), end: endOfMonth(end) }).map((monthStart) => {
    const monthEnd = endOfMonth(monthStart);
    let total = 0;
    for (const day of eachDayOfInterval({ start: monthStart, end: monthEnd })) {
      if (day < start || day > end) continue;
      total += countMap.get(toDateKey(day)) ?? 0;
    }
    return {
      label: format(monthStart, "MMM yy"),
      fullLabel: format(monthStart, "MMMM yyyy"),
      count: total,
    };
  });
}

function buildCountMap(data: DailyCount[]): Map<string, number> {
  const map = new Map<string, number>();
  data.forEach((entry) => {
    map.set(entry.date, entry.count);
  });
  return map;
}

export default function PipelineProgressPanel({ stats, loading }: Props) {
  const [selectedRange, setSelectedRange] = useState<RangeKey>("30d");

  const statusCountMap = useMemo(() => {
    const map = new Map<string, number>();
    stats?.status_breakdown.forEach((entry) => {
      map.set(entry.status, entry.count);
    });
    return map;
  }, [stats]);

  const dailyData = stats?.daily_applications ?? [];
  const hourlyData = stats?.hourly_applications_24h ?? [];
  const countMap = useMemo(() => buildCountMap(dailyData), [dailyData]);
  const today = startOfDay(new Date());
  const earliestDate = useMemo(() => {
    if (dailyData.length === 0) return today;
    return parseISO([...dailyData].sort((a, b) => a.date.localeCompare(b.date))[0].date);
  }, [dailyData, today]);

  const rangeStart = useMemo(() => {
    if (selectedRange === "24h") return today;
    if (selectedRange === "7d") return subDays(today, 6);
    if (selectedRange === "30d") return subDays(today, 29);
    if (selectedRange === "90d") return subDays(today, 89);
    return earliestDate;
  }, [earliestDate, selectedRange, today]);

  const totalApplications = stats?.total_applications ?? 0;
  const rejectedCount = statusCountMap.get("拒绝") ?? 0;
  const activeCount = Math.max(totalApplications - rejectedCount, 0);
  const recentApplications =
    selectedRange === "24h"
      ? hourlyData.reduce((total, entry) => total + entry.count, 0)
      : sumRange(rangeStart, today, countMap);
  const chartData = useMemo(() => {
    if (selectedRange === "24h") {
      return buildHourlyChartData(hourlyData);
    }
    if (selectedRange === "90d") {
      return buildWeeklyChartData(rangeStart, today, countMap);
    }
    if (selectedRange === "all") {
      return buildMonthlyChartData(rangeStart, today, countMap);
    }
    return buildDailyChartData(rangeStart, today, countMap, selectedRange === "7d" ? "EEE" : "MMM d");
  }, [countMap, hourlyData, rangeStart, selectedRange, today]);

  const selectedOption = RANGE_OPTIONS.find((option) => option.key === selectedRange);
  const rangeLabel = selectedOption?.summaryLabel ?? "30D";
  const recruiterCount = statusCountMap.get("Recruiter Reach-out") ?? 0;
  const interviewCount = statusCountMap.get("面试") ?? 0;
  const offerCount = statusCountMap.get("Offer") ?? 0;
  const secondaryStats = [
    { label: "Recruiter", value: recruiterCount, dotClass: "bg-amber-400", glowClass: "from-amber-100/80 via-white/40 to-white/10" },
    { label: "Interviews", value: interviewCount, dotClass: "bg-teal-400", glowClass: "from-teal-100/80 via-white/40 to-white/10" },
    { label: "Offers", value: offerCount, dotClass: "bg-rose-400", glowClass: "from-rose-100/80 via-white/40 to-white/10" },
    { label: "Rejected", value: rejectedCount, dotClass: "bg-stone-400", glowClass: "from-stone-100/80 via-white/40 to-white/10" },
  ];

  return (
    <section className="relative overflow-hidden rounded-[30px] border border-white/70 bg-white/45 p-5 shadow-[0_28px_70px_-42px_rgba(15,23,42,0.5),0_16px_36px_-24px_rgba(45,212,191,0.45)] backdrop-blur-xl sm:p-6">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.92),transparent_34%),radial-gradient(circle_at_bottom_right,rgba(45,212,191,0.18),transparent_36%),linear-gradient(180deg,rgba(255,255,255,0.26),rgba(255,255,255,0.08))]" />
      <div className="pointer-events-none absolute inset-x-6 top-0 h-px bg-white/90" />

      <div className="relative flex flex-col gap-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500/90">
              Pipeline Activity
            </p>
            <h2 className="mt-2 text-xl font-semibold tracking-tight text-slate-900">
              Progress
            </h2>
          </div>
          <div className="rounded-full border border-white/70 bg-white/45 px-3 py-1 text-xs font-medium text-slate-500 shadow-[inset_0_1px_0_rgba(255,255,255,0.8)] backdrop-blur-md">
            Live snapshot
          </div>
        </div>

        <div className="grid w-full grid-cols-5 rounded-[22px] border border-white/70 bg-white/35 p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.72)] backdrop-blur-md">
          {RANGE_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setSelectedRange(option.key)}
              className={`rounded-[18px] px-1.5 py-2 text-xs font-semibold whitespace-nowrap transition-all duration-200 sm:px-2 sm:text-sm ${
                selectedRange === option.key
                  ? "bg-white/78 text-slate-900 shadow-[0_14px_32px_-22px_rgba(15,23,42,0.4),inset_0_1px_0_rgba(255,255,255,0.92)]"
                  : "text-slate-500 hover:bg-white/35 hover:text-slate-900"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        <div className="grid gap-3 lg:grid-cols-[minmax(0,1.1fr),minmax(0,0.9fr)]">
          <div className="relative overflow-hidden rounded-[26px] border border-white/70 bg-[linear-gradient(180deg,rgba(255,255,255,0.72),rgba(255,255,255,0.28))] px-5 py-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.9)] backdrop-blur-md">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(125,211,252,0.18),transparent_42%),radial-gradient(circle_at_bottom_right,rgba(45,212,191,0.12),transparent_40%)]" />
            <div className="relative">
              <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">
                {rangeLabel} applications
              </div>
              <div className={`mt-4 text-6xl font-semibold tracking-[-0.04em] ${loading ? "animate-pulse text-slate-300" : "text-slate-950"}`}>
                {loading ? "—" : recentApplications}
              </div>
              <p className="mt-3 max-w-xs text-sm leading-6 text-slate-600">
                New application activity across the selected window.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Metric label="Active" value={loading ? "—" : `${activeCount}`} accent="teal" />
            <Metric label="Total" value={loading ? "—" : `${totalApplications}`} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2.5">
          {secondaryStats.map((stat) => (
            <div
              key={stat.label}
              className={`relative overflow-hidden rounded-[18px] border border-white/70 bg-gradient-to-br ${stat.glowClass} px-3.5 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.82)] backdrop-blur-md`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <span className={`h-2.5 w-2.5 shrink-0 rounded-full shadow-[0_0_14px_rgba(255,255,255,0.65)] ${stat.dotClass}`} />
                  <span className="truncate text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                    {stat.label}
                  </span>
                </div>
                <div className="text-sm font-semibold text-slate-900">
                  {loading ? "—" : stat.value}
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="relative overflow-hidden rounded-[26px] border border-white/70 bg-[linear-gradient(180deg,rgba(255,255,255,0.56),rgba(236,254,255,0.32))] px-3 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.88)] backdrop-blur-md">
          <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(255,255,255,0.28),transparent)]" />
          <div className="relative">
            {loading ? (
              <div className="h-32 animate-pulse rounded-[20px] bg-white/55" />
            ) : chartData.length === 0 ? (
              <div className="flex h-32 items-center justify-center rounded-[20px] border border-dashed border-white/70 bg-white/35 text-sm text-slate-400">
                No application activity yet
              </div>
            ) : (
              <div className="rounded-[20px] bg-[linear-gradient(180deg,rgba(255,255,255,0.68),rgba(255,255,255,0.24))] px-3 py-2">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">
                    Trend
                  </span>
                  <span className="text-xs font-medium text-slate-500">
                    {rangeLabel}
                  </span>
                </div>
                <div className="h-28">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{ top: 8, right: 4, left: 4, bottom: 0 }}>
                      <defs>
                        <linearGradient id="progress-fill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#14b8a6" stopOpacity={0.36} />
                          <stop offset="60%" stopColor="#22c55e" stopOpacity={0.16} />
                          <stop offset="100%" stopColor="#ffffff" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <Tooltip
                        cursor={{ stroke: "rgba(71, 85, 105, 0.18)", strokeDasharray: "3 3" }}
                        formatter={(value: number) => [`${value}`, "Applications"]}
                        labelFormatter={(_, payload) => payload?.[0]?.payload?.fullLabel ?? ""}
                        contentStyle={{
                          background: "rgba(255, 255, 255, 0.78)",
                          border: "1px solid rgba(255, 255, 255, 0.72)",
                          borderRadius: "18px",
                          boxShadow: "0 18px 48px -28px rgba(15, 23, 42, 0.35)",
                          backdropFilter: "blur(18px)",
                        }}
                        labelStyle={{
                          color: "#475569",
                          fontSize: 12,
                          fontWeight: 600,
                        }}
                        itemStyle={{
                          color: "#0f172a",
                          fontSize: 12,
                          fontWeight: 600,
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="count"
                        stroke="#0f766e"
                        strokeWidth={2.25}
                        fill="url(#progress-fill)"
                        dot={false}
                        activeDot={{ r: 4, fill: "#0f766e", stroke: "#ecfeff", strokeWidth: 2 }}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  accent = "gray",
}: {
  label: string;
  value: string;
  accent?: "gray" | "teal";
}) {
  const accentClasses =
    accent === "teal"
      ? "border-teal-100/80 bg-[linear-gradient(180deg,rgba(204,251,241,0.82),rgba(255,255,255,0.34))] text-slate-950"
      : "border-white/75 bg-[linear-gradient(180deg,rgba(255,255,255,0.84),rgba(255,255,255,0.32))] text-slate-950";

  return (
    <div className={`relative overflow-hidden rounded-[22px] border px-4 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.88)] backdrop-blur-md ${accentClasses}`}>
      <div className={`absolute inset-x-4 top-0 h-px ${accent === "teal" ? "bg-teal-100/80" : "bg-white/90"}`} />
      <div className={`text-[11px] font-semibold uppercase tracking-[0.24em] ${accent === "teal" ? "text-teal-800/75" : "text-slate-500"}`}>
        {label}
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight">
        {value}
      </div>
    </div>
  );
}
