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
    width: "calc((100% - 0.25rem) / 5)",
    transform: `translateX(${selectedRangeIndex * 100}%)`,
  } as const;
  const selectedRangeSliderStyle = {
    width: "calc((100% - 0.25rem) / 5)",
    transform: `translateX(${selectedRangeIndex * 100}%)`,
  } as const;

  return (
    <section className="relative overflow-hidden rounded-[30px] border border-white/18 bg-[linear-gradient(180deg,rgba(255,255,255,0.12),rgba(248,250,252,0.05)_24%,rgba(239,246,255,0.09)_70%,rgba(219,234,254,0.14)_100%)] p-4 shadow-[0_44px_92px_-58px_rgba(15,23,42,0.42),0_18px_40px_-26px_rgba(14,165,233,0.14),inset_0_1px_0_rgba(255,255,255,0.42)] backdrop-blur-[46px] backdrop-saturate-[210%] sm:p-5">
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(118deg,rgba(255,255,255,0.22)_0%,rgba(255,255,255,0.08)_16%,rgba(255,255,255,0.02)_28%,rgba(125,211,252,0.12)_44%,rgba(255,255,255,0.02)_57%,rgba(251,191,36,0.03)_70%,rgba(191,219,254,0.14)_100%)]" />
      <div className="pointer-events-none absolute left-[18%] top-[-10%] h-[128%] w-16 rotate-[11deg] rounded-full bg-[linear-gradient(180deg,rgba(255,255,255,0.48),rgba(255,255,255,0.08)_26%,rgba(186,230,253,0.24)_52%,rgba(255,255,255,0.06)_74%,rgba(255,255,255,0.34)_100%)] opacity-80 blur-[18px]" />
      <div className="pointer-events-none absolute right-[14%] top-[8%] h-[74%] w-12 rotate-[9deg] rounded-full bg-[linear-gradient(180deg,rgba(255,255,255,0.3),rgba(224,242,254,0.14)_38%,rgba(251,207,232,0.14)_72%,rgba(255,255,255,0.18)_100%)] opacity-70 blur-[20px]" />
      <div className="pointer-events-none absolute bottom-[10%] right-[12%] h-12 w-[30%] rounded-full bg-[linear-gradient(90deg,rgba(255,255,255,0.02),rgba(255,255,255,0.16)_36%,rgba(125,211,252,0.24)_58%,rgba(255,255,255,0.03)_100%)] blur-[22px]" />
      <div className="pointer-events-none absolute inset-[1px] rounded-[29px] border border-white/20" />
      <div className="pointer-events-none absolute left-[4%] right-[18%] top-0 h-px bg-[linear-gradient(90deg,rgba(255,255,255,0.96),rgba(255,255,255,0.42)_48%,rgba(255,255,255,0.08)_100%)]" />
      <div className="pointer-events-none absolute left-0 top-[10%] bottom-[18%] w-px bg-[linear-gradient(180deg,rgba(255,255,255,0.44),rgba(255,255,255,0.06)_52%,rgba(255,255,255,0.02)_100%)]" />
      <div className="pointer-events-none absolute right-0 top-[24%] bottom-[10%] w-px bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.16)_34%,rgba(255,255,255,0.03)_100%)]" />
      <div className="pointer-events-none absolute left-[8%] top-[1.5%] h-7 w-[36%] rounded-full bg-white/20 blur-xl" />

      <div className="relative flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <div>
            <h2 className="whitespace-nowrap text-[1.55rem] font-semibold leading-[0.98] tracking-tight text-slate-950 sm:text-[1.72rem] lg:text-[1.82rem]">
              Application Progress
            </h2>
          </div>

          <div className="relative grid w-full grid-cols-5 rounded-[17px] border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.035),rgba(255,255,255,0.015)_100%)] p-[2px] shadow-[inset_0_1px_0_rgba(255,255,255,0.1),inset_0_-8px_14px_rgba(15,23,42,0.04)]">
            <div className="pointer-events-none absolute inset-[1px] rounded-[16px] bg-[linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0.012)_100%)]" />
            <div className="pointer-events-none absolute left-[6%] right-[22%] top-0 h-px bg-[linear-gradient(90deg,rgba(255,255,255,0.44),rgba(255,255,255,0.12)_54%,rgba(255,255,255,0.02)_100%)]" />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute left-[2px] inset-y-[4px] rounded-[13px] bg-[linear-gradient(135deg,rgba(255,255,255,0.26),rgba(191,219,254,0.08)_54%,rgba(255,255,255,0.03)_100%)] opacity-55 blur-[10px] transition-[transform,width] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
              style={selectedRangeGlowStyle}
            />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute left-[2px] inset-y-[2px] rounded-[13px] border border-white/22 bg-[linear-gradient(180deg,rgba(255,255,255,0.18),rgba(255,255,255,0.06)_56%,rgba(224,242,254,0.03)_100%)] shadow-[0_6px_14px_-12px_rgba(15,23,42,0.18),inset_0_1px_0_rgba(255,255,255,0.3)] backdrop-blur-[12px] transition-[transform,width] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
              style={selectedRangeSliderStyle}
            >
              <div className="absolute inset-[1px] rounded-[12px] bg-[linear-gradient(180deg,rgba(255,255,255,0.12),rgba(255,255,255,0.03)_100%)]" />
              <div className="absolute inset-x-4 top-[2px] h-[28%] rounded-full bg-white/18 blur-[4px]" />
            </div>
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setSelectedRange(option.key)}
                aria-pressed={selectedRange === option.key}
                className={`relative z-10 rounded-[13px] px-1 py-[0.74rem] text-[0.94rem] leading-none whitespace-nowrap tracking-[0.01em] transition-[color,font-weight] duration-300 ease-out focus-visible:outline-none sm:px-2 ${
                  selectedRange === option.key
                    ? "font-semibold text-slate-950"
                    : "font-medium text-slate-700/54 hover:text-slate-800/78"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="grid items-stretch gap-3 md:grid-cols-[minmax(0,1fr),144px] md:min-h-[196px]">
          <LiquidGlassCard className="min-h-[196px]" contentClassName="px-4 py-3.5">
            <div>
              <div className="text-[0.95rem] font-medium text-slate-700/78">
                New applications
              </div>
              <div className="mt-1 text-[0.95rem] text-slate-500/78">
                {primaryRangeLabel}
              </div>
              <div className={`mt-5 text-[3.1rem] font-semibold tracking-[-0.055em] ${loading ? "animate-pulse text-slate-300" : "text-slate-950"}`}>
                {loading ? "—" : recentApplications}
              </div>
            </div>
          </LiquidGlassCard>

          <div className="relative overflow-hidden rounded-[24px] border border-white/34 bg-[linear-gradient(180deg,rgba(255,255,255,0.14),rgba(255,255,255,0.05)_46%,rgba(224,242,254,0.08)_100%)] shadow-[0_20px_36px_-28px_rgba(15,23,42,0.24),inset_0_1px_0_rgba(255,255,255,0.52)] backdrop-blur-[24px] md:h-full md:min-h-[196px]">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_12%,rgba(255,255,255,0.58),transparent_30%),radial-gradient(circle_at_84%_84%,rgba(125,211,252,0.16),transparent_30%)]" />
            <div className="pointer-events-none absolute inset-x-5 top-0 h-px bg-white/70" />
            <div className="pointer-events-none absolute left-[10%] top-[-8%] h-16 w-[46%] rotate-[8deg] rounded-full bg-white/24 blur-xl" />
            <div className="relative flex h-full min-h-[164px] flex-col justify-between px-4 py-4">
              <div>
                <div className="text-[0.95rem] font-medium text-slate-800/82">Active</div>
                <div className="mt-3 text-[2.3rem] font-semibold tracking-[-0.055em] tabular-nums text-slate-950">
                  {loading ? "—" : activeCount}
                </div>
              </div>

              <div className="pt-7">
                <div className="text-[0.95rem] font-medium text-slate-700/72">Total</div>
                <div className="mt-3 text-[2.3rem] font-semibold tracking-[-0.055em] tabular-nums text-slate-950">
                  {loading ? "—" : totalApplications}
                </div>
              </div>
            </div>
          </div>
        </div>

        <LiquidGlassCard contentClassName="relative px-2.5 py-2.5">
          <div className="pointer-events-none absolute left-[24%] top-[18%] h-28 w-[28%] rounded-full bg-cyan-200/10 blur-3xl" />
          <div className="pointer-events-none absolute right-[10%] bottom-[8%] h-28 w-[34%] rounded-full bg-blue-200/12 blur-3xl" />
          <div className="pointer-events-none absolute right-[16%] top-[10%] h-24 w-[24%] rounded-full bg-rose-100/8 blur-3xl" />
          <div className="relative px-3 py-2">
            {loading ? (
              <div className="h-32 animate-pulse rounded-[12px] bg-white/18" />
            ) : chartData.length === 0 ? (
              <div className="flex h-32 items-center justify-center rounded-[12px] bg-white/6 text-sm text-slate-400">
                No application activity yet
              </div>
            ) : (
              <>
                <div className="mb-1 flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[0.9rem] font-semibold text-slate-800/88">
                      {trendTitle}
                    </div>
                    <div className="mt-0.5 text-[0.8rem] text-slate-600/72">
                      {trendGroupingLabel}
                    </div>
                  </div>
                  <span className="text-right text-[0.9rem] font-medium text-slate-700/76">
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
      className={`relative overflow-hidden rounded-[24px] border border-white/34 bg-[linear-gradient(180deg,rgba(255,255,255,0.14),rgba(255,255,255,0.05)_46%,rgba(224,242,254,0.08)_100%)] shadow-[0_20px_36px_-28px_rgba(15,23,42,0.24),inset_0_1px_0_rgba(255,255,255,0.52)] backdrop-blur-[24px] ${className}`}
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_12%,rgba(255,255,255,0.58),transparent_30%),radial-gradient(circle_at_84%_84%,rgba(125,211,252,0.16),transparent_30%)]" />
      <div className="pointer-events-none absolute inset-x-5 top-0 h-px bg-white/70" />
      <div className="pointer-events-none absolute left-[10%] top-[-8%] h-16 w-[46%] rotate-[8deg] rounded-full bg-white/24 blur-xl" />
      <div className={`relative ${contentClassName}`}>{children}</div>
    </div>
  );
}
