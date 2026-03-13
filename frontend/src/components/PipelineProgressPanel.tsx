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
  const selectedRangeSliderStyle = {
    width: "calc((100% - 0.5rem) / 5)",
    transform: `translateX(${selectedRangeIndex * 100}%)`,
  } as const;
  const recruiterCount = statusCountMap.get("Recruiter Reach-out") ?? 0;
  const interviewCount = statusCountMap.get("面试") ?? 0;
  const offerCount = statusCountMap.get("Offer") ?? 0;
  const secondaryStats = [
    { label: "Recruiter", value: recruiterCount, dotClass: "bg-amber-400" },
    { label: "Interviews", value: interviewCount, dotClass: "bg-teal-400" },
    { label: "Offers", value: offerCount, dotClass: "bg-rose-400" },
    { label: "Rejected", value: rejectedCount, dotClass: "bg-stone-400" },
  ];

  return (
    <section className="relative overflow-hidden rounded-[28px] border border-white/28 bg-[linear-gradient(180deg,rgba(255,255,255,0.16),rgba(255,255,255,0.05)_38%,rgba(224,242,254,0.08)_100%)] p-4 shadow-[0_68px_110px_-74px_rgba(15,23,42,0.62),0_24px_48px_-40px_rgba(45,212,191,0.12),inset_0_1px_0_rgba(255,255,255,0.3)] backdrop-blur-[30px] sm:p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.78),transparent_26%),radial-gradient(circle_at_78%_80%,rgba(125,211,252,0.22),transparent_32%),radial-gradient(circle_at_92%_92%,rgba(187,247,208,0.16),transparent_24%),linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0.01))]" />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.06),transparent_30%,rgba(103,232,249,0.06)_72%,transparent_100%)] opacity-80" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_120%,rgba(15,23,42,0.14),transparent_40%)]" />
      <div className="pointer-events-none absolute inset-x-6 top-0 h-px bg-white/54" />
      <div className="pointer-events-none absolute inset-x-8 bottom-0 h-[1px] bg-slate-300/14" />

      <div className="relative flex flex-col gap-3">
        <div>
          <h2 className="whitespace-nowrap text-[1.55rem] font-semibold leading-[0.98] tracking-tight text-slate-950 sm:text-[1.72rem] lg:text-[1.82rem]">
            Application Progress
          </h2>
        </div>

        <div className="relative grid w-full grid-cols-5 rounded-[16px] border border-white/22 bg-[linear-gradient(180deg,rgba(255,255,255,0.1),rgba(255,255,255,0.04))] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.26),inset_0_14px_20px_rgba(255,255,255,0.03)] backdrop-blur-[16px]">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-1 rounded-[16px] bg-[radial-gradient(circle_at_30%_30%,rgba(255,255,255,1),rgba(191,219,254,0.66)_38%,rgba(125,211,252,0.28)_68%,transparent_100%)] blur-[26px] opacity-100 transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
            style={selectedRangeSliderStyle}
          />
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-[3px] left-1 rounded-[14px] border border-white/94 bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(255,255,255,0.78)_48%,rgba(240,249,255,0.54))] shadow-[0_24px_38px_-24px_rgba(15,23,42,0.34),0_14px_22px_-18px_rgba(125,211,252,0.22),inset_0_1px_0_rgba(255,255,255,1)] transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
            style={selectedRangeSliderStyle}
          >
            <div className="absolute inset-[1px] rounded-[13px] bg-[linear-gradient(180deg,rgba(255,255,255,0.88),rgba(255,255,255,0.2)_58%,rgba(255,255,255,0.04))]" />
            <div className="absolute inset-x-4 top-[2px] h-[44%] rounded-full bg-white/76 blur-[2px]" />
            <div className="absolute bottom-[3px] left-[18%] h-[30%] w-[64%] rounded-full bg-sky-100/56 blur-[12px]" />
          </div>
          {RANGE_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setSelectedRange(option.key)}
              aria-pressed={selectedRange === option.key}
              className={`relative z-10 rounded-[14px] px-1 py-1.5 text-[0.95rem] font-semibold whitespace-nowrap transition-[color,transform] duration-300 ease-out focus-visible:outline-none sm:px-2 ${
                selectedRange === option.key
                  ? "-translate-y-[1px] scale-[1.01] text-slate-950"
                  : "text-slate-500/95 hover:text-slate-900"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        <div className="grid items-stretch gap-3 md:grid-cols-[minmax(0,1fr),132px] md:min-h-[196px] lg:grid-cols-[minmax(0,1fr),142px]">
          <div className="relative min-h-[196px] overflow-hidden rounded-[22px] border border-white/12 bg-[linear-gradient(180deg,rgba(255,255,255,0.08),rgba(255,255,255,0.03)_42%,rgba(236,254,255,0.06)_100%)] px-4 py-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.22),inset_0_16px_24px_rgba(255,255,255,0.02),inset_0_-18px_28px_rgba(15,23,42,0.06),inset_0_-26px_38px_rgba(34,211,238,0.05)] backdrop-blur-[12px]">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.42),transparent_34%),radial-gradient(circle_at_74%_80%,rgba(103,232,249,0.16),transparent_30%),linear-gradient(135deg,rgba(255,255,255,0.06),transparent_34%,rgba(255,255,255,0.12)_72%,transparent_100%)]" />
            <div className="pointer-events-none absolute inset-[1px] rounded-[21px] bg-[linear-gradient(180deg,rgba(255,255,255,0.12),transparent_22%,rgba(255,255,255,0.02)_64%,rgba(236,254,255,0.1))]" />
            <div className="pointer-events-none absolute inset-x-4 top-[2px] h-[20%] rounded-full bg-white/22 blur-[7px]" />
            <div className="pointer-events-none absolute bottom-3 right-[10%] h-16 w-[40%] rounded-full bg-cyan-100/24 blur-[24px]" />
            <div className="relative">
              <div className="text-[0.95rem] font-medium text-slate-500">
                New applications
              </div>
              <div className="mt-1 text-[0.95rem] text-slate-400/95">
                {primaryRangeLabel}
              </div>
              <div className={`mt-5 text-[3.1rem] font-semibold tracking-[-0.055em] ${loading ? "animate-pulse text-slate-300" : "text-slate-950"}`}>
                {loading ? "—" : recentApplications}
              </div>
            </div>
          </div>

          <div className="relative overflow-hidden rounded-[22px] border border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02)_42%,rgba(236,254,255,0.05)_100%)] px-3 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.18),inset_0_16px_22px_rgba(255,255,255,0.015),inset_0_-18px_28px_rgba(15,23,42,0.06),inset_0_-24px_36px_rgba(34,211,238,0.04)] backdrop-blur-[10px] md:h-full md:min-h-[196px]">
            <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(255,255,255,0.06),transparent_44%,rgba(236,254,255,0.04))]" />
            <div className="relative grid grid-cols-2 gap-0 md:h-full md:grid-cols-1 md:grid-rows-2">
              <SummaryMetric
                label="Active"
                value={loading ? "—" : `${activeCount}`}
                accent="teal"
                className="pr-3 md:border-b md:border-white/12 md:pb-3 md:pr-0"
              />
              <SummaryMetric
                label="Total"
                value={loading ? "—" : `${totalApplications}`}
                className="border-l border-white/12 pl-3 md:border-l-0 md:pl-0 md:pt-3"
              />
            </div>
          </div>
        </div>

        <div className="rounded-[22px] border border-white/12 bg-[linear-gradient(180deg,rgba(255,255,255,0.08),rgba(255,255,255,0.03)_42%,rgba(236,254,255,0.06)_100%)] p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.26),inset_0_18px_26px_rgba(255,255,255,0.02),inset_0_-22px_34px_rgba(15,23,42,0.07),inset_0_-26px_38px_rgba(34,211,238,0.05)] backdrop-blur-[10px]">
          <div className="mb-2 text-[0.95rem] font-medium text-slate-600">
            Current stages
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 px-3 py-1">
            {secondaryStats.map((stat, index) => (
              <div
                key={stat.label}
                className={`flex min-h-[56px] items-center py-3 ${index % 2 === 1 ? "pl-3" : "pr-3"}`}
              >
                <div className="flex w-full items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full shadow-[0_0_14px_rgba(255,255,255,0.65)] ${stat.dotClass}`} />
                    <span className="truncate text-[0.95rem] font-medium text-slate-600">
                      {stat.label}
                    </span>
                  </div>
                  <div className="text-[0.95rem] font-semibold tabular-nums text-slate-900">
                    {loading ? "—" : stat.value}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative overflow-hidden rounded-[22px] border border-white/12 bg-[linear-gradient(180deg,rgba(255,255,255,0.08),rgba(255,255,255,0.03)_42%,rgba(236,254,255,0.05)_100%)] px-2.5 py-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.22),inset_0_14px_22px_rgba(255,255,255,0.02),inset_0_-22px_32px_rgba(34,211,238,0.03)] backdrop-blur-[10px]">
          <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(255,255,255,0.08),transparent)]" />
          <div className="relative px-3 py-2">
            {loading ? (
              <div className="h-32 animate-pulse rounded-[12px] bg-white/28" />
            ) : chartData.length === 0 ? (
              <div className="flex h-32 items-center justify-center rounded-[12px] border border-dashed border-white/45 bg-white/10 text-sm text-slate-400">
                No application activity yet
              </div>
            ) : (
              <>
                <div className="mb-1 flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[0.9rem] font-semibold text-slate-700">
                      {trendTitle}
                    </div>
                    <div className="mt-0.5 text-[0.8rem] text-slate-500/75">
                      {trendGroupingLabel}
                    </div>
                  </div>
                  <span className="text-right text-[0.9rem] font-medium text-slate-600/85">
                    {trendRangeLabel}
                  </span>
                </div>
                <div className="h-32 -mx-3">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{ top: 4, right: -14, left: -10, bottom: -2 }}>
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
              </>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  accent = "gray",
  className = "",
}: {
  label: string;
  value: string;
  accent?: "gray" | "teal";
  className?: string;
}) {
  const glowClasses =
    accent === "teal"
      ? "bg-[radial-gradient(circle_at_18%_28%,rgba(255,255,255,0.14),transparent_42%),radial-gradient(circle_at_78%_82%,rgba(110,231,183,0.1),transparent_36%)]"
      : "bg-[radial-gradient(circle_at_18%_28%,rgba(255,255,255,0.12),transparent_42%),radial-gradient(circle_at_78%_82%,rgba(191,219,254,0.08),transparent_36%)]";

  return (
    <div className={`relative flex min-h-[80px] flex-col justify-between overflow-hidden ${className}`}>
      <div className={`pointer-events-none absolute inset-0 ${glowClasses}`} />
      <div className="relative flex min-h-[80px] flex-col justify-between px-1 py-0.5">
        <div className={`text-sm font-medium ${accent === "teal" ? "text-teal-800/80" : "text-slate-500"}`}>
          {label}
        </div>
        <div className="mt-2 text-[1.75rem] font-semibold tracking-tight tabular-nums text-slate-950">
          {value}
        </div>
      </div>
    </div>
  );
}
