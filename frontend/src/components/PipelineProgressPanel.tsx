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

type ChartGrouping = "hour" | "day" | "week" | "month";

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

function formatRangeWindow(start: Date, end: Date): string {
  return `${format(start, "MMM d")} - ${format(end, "MMM d, yyyy")}`;
}

function countDaysInRange(start: Date, end: Date): number {
  return Math.floor((end.getTime() - start.getTime()) / 86_400_000) + 1;
}

function describeRange(range: RangeKey): string {
  switch (range) {
    case "24h":
      return "Last 24 hours";
    case "7d":
      return "Last 7 days";
    case "30d":
      return "Last 30 days";
    case "90d":
      return "Last 90 days";
    case "all":
      return "All time";
  }
}

function buildCountMap(data: DailyCount[]): Map<string, number> {
  const map = new Map<string, number>();
  data.forEach((entry) => {
    map.set(entry.date, entry.count);
  });
  return map;
}

function getChartGrouping(range: RangeKey, start: Date, end: Date): ChartGrouping {
  if (range === "24h") return "hour";
  if (range === "90d") return "week";
  if (range === "all") {
    const dayCount = countDaysInRange(start, end);
    if (dayCount <= 45) return "day";
    if (dayCount <= 180) return "week";
    return "month";
  }
  return "day";
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
  const chartGrouping = useMemo(
    () => getChartGrouping(selectedRange, rangeStart, today),
    [rangeStart, selectedRange, today],
  );
  const chartData = useMemo(() => {
    if (chartGrouping === "hour") {
      return buildHourlyChartData(hourlyData);
    }
    if (chartGrouping === "week") {
      return buildWeeklyChartData(rangeStart, today, countMap);
    }
    if (chartGrouping === "month") {
      return buildMonthlyChartData(rangeStart, today, countMap);
    }
    return buildDailyChartData(rangeStart, today, countMap, selectedRange === "7d" ? "EEE" : "MMM d");
  }, [chartGrouping, countMap, hourlyData, rangeStart, selectedRange, today]);
  const trendTitle = "Applications over time";
  const trendGroupingLabel =
    chartGrouping === "hour"
      ? "Grouped by hour"
      : chartGrouping === "week"
        ? "Grouped by week"
        : chartGrouping === "month"
          ? "Grouped by month"
          : "Grouped by day";
  const trendRangeLabel = useMemo(() => {
    if (selectedRange === "24h") {
      if (hourlyData.length === 0) {
        return "Last 24 hours";
      }

      const sortedData = [...hourlyData].sort((left, right) => left.timestamp.localeCompare(right.timestamp));
      const start = parseISO(sortedData[0].timestamp);
      const end = parseISO(sortedData[sortedData.length - 1].timestamp);
      return formatRangeWindow(start, end);
    }

    return formatRangeWindow(rangeStart, today);
  }, [hourlyData, rangeStart, selectedRange, today]);
  const primaryRangeLabel = describeRange(selectedRange);
  const selectedRangeIndex = RANGE_OPTIONS.findIndex((option) => option.key === selectedRange);
  const recruiterCount = statusCountMap.get("Recruiter Reach-out") ?? 0;
  const interviewCount = statusCountMap.get("面试") ?? 0;
  const offerCount = statusCountMap.get("Offer") ?? 0;
  const secondaryStats = [
    { label: "Recruiter", value: recruiterCount, dotClass: "bg-amber-400", glowClass: "from-amber-50/85 via-white/45 to-white/15" },
    { label: "Interviews", value: interviewCount, dotClass: "bg-teal-400", glowClass: "from-teal-50/85 via-white/45 to-white/15" },
    { label: "Offers", value: offerCount, dotClass: "bg-rose-400", glowClass: "from-rose-50/85 via-white/45 to-white/15" },
    { label: "Rejected", value: rejectedCount, dotClass: "bg-stone-400", glowClass: "from-stone-50/85 via-white/45 to-white/15" },
  ];

  return (
    <section className="relative overflow-hidden rounded-[28px] border border-white/75 bg-white/44 p-5 shadow-[0_26px_70px_-42px_rgba(15,23,42,0.5),0_16px_36px_-24px_rgba(45,212,191,0.42)] backdrop-blur-xl sm:p-6">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.94),transparent_34%),radial-gradient(circle_at_bottom_right,rgba(45,212,191,0.15),transparent_40%),linear-gradient(180deg,rgba(255,255,255,0.24),rgba(255,255,255,0.06))]" />
      <div className="pointer-events-none absolute inset-x-6 top-0 h-px bg-white/90" />

      <div className="relative flex flex-col gap-4">
        <div>
          <h2 className="whitespace-nowrap text-[2.35rem] font-semibold leading-[0.95] tracking-tight text-slate-950 sm:text-[2.6rem] lg:text-[2.85rem]">
            Application Progress
          </h2>
        </div>

        <div className="relative grid w-full grid-cols-5 rounded-[16px] border border-white/70 bg-white/28 p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.7),inset_0_-12px_24px_rgba(15,23,42,0.03)] backdrop-blur-md">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-1 left-1 rounded-[14px] border border-white/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(247,250,252,0.78))] shadow-[0_18px_34px_-24px_rgba(15,23,42,0.42),0_8px_16px_-12px_rgba(59,130,246,0.18),inset_0_1px_0_rgba(255,255,255,1)] transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
            style={{
              width: "calc((100% - 0.5rem) / 5)",
              transform: `translateX(${selectedRangeIndex * 100}%)`,
            }}
          >
            <div className="absolute inset-[1px] rounded-[13px] bg-[linear-gradient(180deg,rgba(255,255,255,0.55),rgba(255,255,255,0.08))]" />
          </div>
          {RANGE_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setSelectedRange(option.key)}
              aria-pressed={selectedRange === option.key}
              className={`relative z-10 rounded-[14px] px-1.5 py-2 text-sm font-semibold whitespace-nowrap transition-[color,transform] duration-200 ease-out focus-visible:outline-none sm:px-2 ${
                selectedRange === option.key
                  ? "-translate-y-[1px] text-slate-950"
                  : "text-slate-500 hover:text-slate-900"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        <div className="grid items-start gap-3 md:grid-cols-[minmax(0,1fr),176px] lg:grid-cols-[minmax(0,1fr),188px]">
          <div className="relative min-h-[220px] overflow-hidden rounded-[22px] border border-white/80 bg-[linear-gradient(180deg,rgba(255,255,255,0.82),rgba(244,252,252,0.42))] px-5 py-5 shadow-[0_16px_32px_-26px_rgba(15,23,42,0.28),inset_0_1px_0_rgba(255,255,255,0.92)] backdrop-blur-md">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(125,211,252,0.16),transparent_42%),radial-gradient(circle_at_bottom_right,rgba(45,212,191,0.10),transparent_40%)]" />
            <div className="relative">
              <div className="text-sm font-medium text-slate-500">
                New applications
              </div>
              <div className="mt-1 text-sm text-slate-400">
                {primaryRangeLabel}
              </div>
              <div className={`mt-5 text-6xl font-semibold tracking-[-0.05em] ${loading ? "animate-pulse text-slate-300" : "text-slate-950"}`}>
                {loading ? "—" : recentApplications}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 self-stretch md:grid-cols-1">
            <Metric label="Active" value={loading ? "—" : `${activeCount}`} accent="teal" />
            <Metric label="Total" value={loading ? "—" : `${totalApplications}`} />
          </div>
        </div>

        <div className="rounded-[22px] border border-white/65 bg-white/24 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.72)] backdrop-blur-md">
          <div className="mb-3 text-sm font-medium text-slate-500">
            Current stages
          </div>
          <div className="grid grid-cols-2 gap-2.5">
            {secondaryStats.map((stat) => (
              <div
                key={stat.label}
                className={`relative flex min-h-[68px] items-center overflow-hidden rounded-[16px] border border-white/65 bg-gradient-to-br ${stat.glowClass} px-3.5 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.78)] backdrop-blur-md`}
              >
                <div className="flex w-full items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full shadow-[0_0_14px_rgba(255,255,255,0.65)] ${stat.dotClass}`} />
                    <span className="truncate text-sm font-medium text-slate-600">
                      {stat.label}
                    </span>
                  </div>
                  <div className="text-sm font-semibold tabular-nums text-slate-900">
                    {loading ? "—" : stat.value}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative overflow-hidden rounded-[22px] border border-white/72 bg-[linear-gradient(180deg,rgba(255,255,255,0.58),rgba(236,254,255,0.28))] px-3 py-3 shadow-[0_14px_30px_-26px_rgba(15,23,42,0.2),inset_0_1px_0_rgba(255,255,255,0.86)] backdrop-blur-md">
          <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(255,255,255,0.28),transparent)]" />
          <div className="relative">
            {loading ? (
              <div className="h-36 animate-pulse rounded-[16px] bg-white/55" />
            ) : chartData.length === 0 ? (
              <div className="flex h-36 items-center justify-center rounded-[16px] border border-dashed border-white/70 bg-white/35 text-sm text-slate-400">
                No application activity yet
              </div>
            ) : (
              <div className="rounded-[16px] bg-[linear-gradient(180deg,rgba(255,255,255,0.7),rgba(255,255,255,0.2))] px-3 py-2">
                <div className="mb-1 flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold text-slate-600">
                      {trendTitle}
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-400">
                      {trendGroupingLabel}
                    </div>
                  </div>
                  <span className="text-right text-xs font-medium text-slate-500">
                    {trendRangeLabel}
                  </span>
                </div>
                <div className="h-36 -mx-1">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
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
      ? "border-teal-100/75 bg-[linear-gradient(180deg,rgba(204,251,241,0.72),rgba(255,255,255,0.28))] text-slate-950"
      : "border-white/75 bg-[linear-gradient(180deg,rgba(255,255,255,0.7),rgba(255,255,255,0.24))] text-slate-950";

  return (
    <div className={`relative flex min-h-[104px] flex-col justify-between overflow-hidden rounded-[22px] border px-4 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.84)] backdrop-blur-md ${accentClasses}`}>
      <div className={`absolute inset-x-4 top-0 h-px ${accent === "teal" ? "bg-teal-100/80" : "bg-white/90"}`} />
      <div className={`text-sm font-medium ${accent === "teal" ? "text-teal-800/80" : "text-slate-500"}`}>
        {label}
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight tabular-nums">
        {value}
      </div>
    </div>
  );
}
