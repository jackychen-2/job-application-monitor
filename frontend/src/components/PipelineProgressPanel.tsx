import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import {
  addWeeks,
  differenceInCalendarDays,
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
  const daySpan =
    selectedRange === "24h"
      ? 1
      : Math.max(differenceInCalendarDays(today, rangeStart) + 1, 1);
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
    { label: "Recruiter", value: recruiterCount, dotClass: "bg-orange-400" },
    { label: "Interviews", value: interviewCount, dotClass: "bg-teal-500" },
    { label: "Offers", value: offerCount, dotClass: "bg-red-500" },
    { label: "Rejected", value: rejectedCount, dotClass: "bg-stone-500" },
  ];

  return (
    <div className="flex flex-col rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-gray-900">Progress</h2>
        </div>
        <div className="grid w-full grid-cols-5 rounded-xl bg-gray-100 p-1">
          {RANGE_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setSelectedRange(option.key)}
              className={`rounded-lg px-1.5 py-2 text-xs font-semibold whitespace-nowrap transition-colors sm:px-2 sm:text-sm ${
                selectedRange === option.key
                  ? "bg-gray-900 text-white shadow-sm"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4">
        <div className="flex items-end justify-between gap-4">
          <div>
            <div className={`text-5xl font-semibold tracking-tight ${loading ? "animate-pulse text-gray-300" : "text-gray-900"}`}>
              {loading ? "—" : recentApplications}
            </div>
            <div className="mt-1 text-sm text-gray-500">
              {rangeLabel} applications
            </div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3">
          <Metric label="Active" value={loading ? "—" : `${activeCount}`} />
          <Metric label="Total" value={loading ? "—" : `${totalApplications}`} />
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        {secondaryStats.map((stat) => (
          <div
            key={stat.label}
            className="inline-flex items-center justify-between gap-3 rounded-full border border-gray-200 bg-white px-3 py-2"
          >
            <div className="flex items-center gap-2 min-w-0">
              <span className={`h-2 w-2 shrink-0 rounded-full ${stat.dotClass}`} />
              <span className="truncate text-xs font-medium text-gray-500">
                {stat.label}
              </span>
            </div>
            <div className="text-sm font-semibold text-gray-900">
              {loading ? "—" : stat.value}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4">
        {loading ? (
          <div className="h-28 animate-pulse rounded-2xl bg-gray-100" />
        ) : chartData.length === 0 ? (
          <div className="flex h-28 items-center justify-center rounded-2xl border border-dashed border-gray-200 text-sm text-gray-400">
            No application activity yet
          </div>
        ) : (
          <div className="rounded-2xl bg-slate-50 px-3 py-2">
            <div className="h-28">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 8, right: 4, left: 4, bottom: 0 }}>
                  <defs>
                    <linearGradient id="progress-fill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#0f766e" stopOpacity={0.28} />
                      <stop offset="100%" stopColor="#0f766e" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <Tooltip
                    cursor={false}
                    formatter={(value: number) => [`${value}`, "Applications"]}
                    labelFormatter={(_, payload) => payload?.[0]?.payload?.fullLabel ?? ""}
                  />
                  <Area
                    type="monotone"
                    dataKey="count"
                    stroke="#0f766e"
                    strokeWidth={2}
                    fill="url(#progress-fill)"
                    dot={false}
                    activeDot={{ r: 3, fill: "#0f766e" }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  accent = "gray",
  mobileOnly = false,
}: {
  label: string;
  value: string;
  accent?: "gray" | "teal";
  mobileOnly?: boolean;
}) {
  const accentClasses =
    accent === "teal"
      ? "bg-teal-50 text-teal-900 border-teal-100"
      : "bg-gray-50 text-gray-900 border-gray-200";

  return (
    <div className={`${mobileOnly ? "sm:hidden " : ""}rounded-xl border px-3 py-3 ${accentClasses}`}>
      <div className={`text-[11px] uppercase tracking-wide ${accent === "teal" ? "text-teal-700" : "text-gray-500"}`}>
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold">
        {value}
      </div>
    </div>
  );
}
