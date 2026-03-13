import { useMemo, useState, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  YAxis,
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
type LiquidGlassCardProps = {
  children: ReactNode;
  className?: string;
  contentClassName?: string;
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
  const chartYAxisMax = useMemo(
    () => Math.max(chartData.reduce((maxCount, point) => Math.max(maxCount, point.count), 0), 1),
    [chartData],
  );
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
  const selectedRangeGlowStyle = {
    width: "calc(20% - 0.155rem)",
    left: `calc(${selectedRangeIndex * 20}% + ${(0.2275 - selectedRangeIndex * 0.075).toFixed(4)}rem)`,
  } as const;
  const selectedRangeSliderStyle = {
    width: "calc(20% - 0.295rem)",
    left: `calc(${selectedRangeIndex * 20}% + ${(0.2975 - selectedRangeIndex * 0.075).toFixed(4)}rem)`,
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
    <section className="relative overflow-hidden rounded-[28px] border border-white/30 bg-[linear-gradient(180deg,rgba(255,255,255,0.18),rgba(255,255,255,0.07)_42%,rgba(224,242,254,0.1)_100%)] p-4 shadow-[0_68px_110px_-74px_rgba(15,23,42,0.58),0_26px_52px_-42px_rgba(56,189,248,0.16),inset_0_1px_0_rgba(255,255,255,0.36),inset_0_-24px_36px_rgba(15,23,42,0.08)] backdrop-blur-[28px] sm:p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_14%,rgba(255,255,255,0.82),transparent_28%),radial-gradient(circle_at_86%_84%,rgba(125,211,252,0.22),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.08),rgba(255,255,255,0.01)_62%)]" />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.08),transparent_34%,rgba(224,242,254,0.1)_76%,transparent_100%)]" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_118%,rgba(15,23,42,0.15),transparent_42%)]" />
      <div className="pointer-events-none absolute inset-x-6 top-0 h-px bg-white/54" />
      <div className="pointer-events-none absolute inset-x-8 bottom-0 h-[1px] bg-slate-300/14" />

      <div className="relative flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <div>
            <h2 className="whitespace-nowrap text-[1.55rem] font-semibold leading-[0.98] tracking-tight text-slate-950 sm:text-[1.72rem] lg:text-[1.82rem]">
              Application Progress
            </h2>
          </div>

          <div className="relative grid w-full grid-cols-5 rounded-[15px] border border-white/18 bg-[linear-gradient(180deg,rgba(255,255,255,0.15),rgba(255,255,255,0.05)_40%,rgba(236,254,255,0.05)_100%)] p-[3px] shadow-[0_16px_30px_-28px_rgba(15,23,42,0.18),inset_0_1px_0_rgba(255,255,255,0.26),inset_0_-14px_22px_rgba(15,23,42,0.05)] backdrop-blur-[20px]">
            <div className="pointer-events-none absolute inset-0 rounded-[15px] bg-[radial-gradient(circle_at_18%_14%,rgba(255,255,255,0.46),transparent_34%),radial-gradient(circle_at_86%_84%,rgba(125,211,252,0.1),transparent_32%)]" />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-[1px] rounded-[14px] bg-[radial-gradient(circle_at_28%_22%,rgba(255,255,255,0.86),rgba(219,234,254,0.54)_42%,rgba(125,211,252,0.24)_76%,transparent_100%)] blur-[20px] opacity-95 transition-[left,width] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
              style={selectedRangeGlowStyle}
            />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-[4px] rounded-[12px] border border-white/86 bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(255,255,255,0.76)_44%,rgba(240,249,255,0.5)_100%)] shadow-[0_18px_28px_-22px_rgba(15,23,42,0.26),0_8px_16px_-14px_rgba(125,211,252,0.16),inset_0_1px_0_rgba(255,255,255,0.98)] transition-[left,width] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
              style={selectedRangeSliderStyle}
            >
              <div className="absolute inset-[1px] rounded-[11px] bg-[linear-gradient(180deg,rgba(255,255,255,0.56),rgba(255,255,255,0.16)_54%,rgba(224,242,254,0.24)_100%)]" />
              <div className="absolute inset-x-3 top-[2px] h-[46%] rounded-full bg-white/84 blur-[2px]" />
              <div className="absolute bottom-[4px] right-[14%] h-[26%] w-[58%] rounded-full bg-sky-100/52 blur-[10px]" />
            </div>
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setSelectedRange(option.key)}
                aria-pressed={selectedRange === option.key}
                className={`relative z-10 rounded-[12px] px-1 py-[0.7rem] text-[0.92rem] leading-none whitespace-nowrap tracking-[0.01em] transition-[color,transform,font-weight] duration-300 ease-out focus-visible:outline-none sm:px-2 ${
                  selectedRange === option.key
                    ? "-translate-y-[1px] font-semibold text-slate-950"
                    : "font-medium text-slate-400/88 hover:text-slate-700/92"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="grid items-stretch gap-3 md:grid-cols-[minmax(0,1fr),132px] md:min-h-[196px] lg:grid-cols-[minmax(0,1fr),142px]">
          <LiquidGlassCard className="min-h-[196px]" contentClassName="px-4 py-3.5">
            <div>
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
          </LiquidGlassCard>

          <LiquidGlassCard className="md:h-full md:min-h-[196px]" contentClassName="px-3 py-3">
            <div className="grid grid-cols-2 gap-0 md:h-full md:grid-cols-1 md:grid-rows-2">
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
          </LiquidGlassCard>
        </div>

        <LiquidGlassCard contentClassName="p-2.5">
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
        </LiquidGlassCard>

        <LiquidGlassCard contentClassName="px-2.5 py-2.5">
          <div className="px-3 py-2">
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
                    <AreaChart data={chartData} margin={{ top: 4, right: -14, left: -10, bottom: 6 }}>
                      <defs>
                        <linearGradient id="progress-fill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#14b8a6" stopOpacity={0.36} />
                          <stop offset="60%" stopColor="#22c55e" stopOpacity={0.16} />
                          <stop offset="100%" stopColor="#ffffff" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <YAxis
                        hide
                        allowDecimals={false}
                        domain={[0, chartYAxisMax]}
                        padding={{ top: 6, bottom: 10 }}
                      />
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
                        baseValue={0}
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
        </LiquidGlassCard>
      </div>
    </section>
  );
}

function LiquidGlassCard({
  children,
  className = "",
  contentClassName = "",
}: LiquidGlassCardProps) {
  return (
    <div
      className={`relative overflow-hidden rounded-[22px] border border-white/18 bg-[linear-gradient(180deg,rgba(255,255,255,0.14),rgba(255,255,255,0.05)_42%,rgba(236,254,255,0.08)_100%)] shadow-[0_26px_44px_-32px_rgba(15,23,42,0.28),0_12px_24px_-20px_rgba(56,189,248,0.14),inset_0_1px_0_rgba(255,255,255,0.32),inset_0_-18px_30px_rgba(15,23,42,0.08)] backdrop-blur-[18px] ${className}`}
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_14%,rgba(255,255,255,0.54),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.1),rgba(255,255,255,0.03)_42%,rgba(236,254,255,0.04)_100%)]" />
      <div className="pointer-events-none absolute inset-[1px] rounded-[inherit] bg-[linear-gradient(135deg,rgba(255,255,255,0.14),transparent_34%,rgba(224,242,254,0.12)_76%,transparent_100%)]" />
      <div className="pointer-events-none absolute inset-x-5 top-[1px] h-[18%] rounded-full bg-white/34 blur-[8px]" />
      <div className="pointer-events-none absolute -bottom-4 right-[8%] h-20 w-[46%] rounded-full bg-sky-100/28 blur-[28px]" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_118%,rgba(15,23,42,0.14),transparent_46%)]" />
      <div className={`relative ${contentClassName}`}>{children}</div>
    </div>
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
      ? "bg-[radial-gradient(circle_at_18%_18%,rgba(255,255,255,0.2),transparent_36%),radial-gradient(circle_at_86%_88%,rgba(45,212,191,0.12),transparent_34%)]"
      : "bg-[radial-gradient(circle_at_18%_18%,rgba(255,255,255,0.18),transparent_36%),radial-gradient(circle_at_86%_88%,rgba(125,211,252,0.1),transparent_34%)]";

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
