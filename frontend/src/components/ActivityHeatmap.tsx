import { useMemo } from "react";
import type { DailyCount } from "../types";

interface Props {
  data: DailyCount[];
  totalApplications: number;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["", "Mon", "", "Wed", "", "Fri", ""];

function getColor(count: number): string {
  if (count === 0) return "#ebedf0";
  if (count === 1) return "#9be9a8";
  if (count <= 3) return "#40c463";
  if (count <= 5) return "#30a14e";
  return "#216e39";
}

function getWeeks(year: number): { date: string; count: number; day: number; week: number }[][] {
  const start = new Date(year, 0, 1);
  const end = new Date(year, 11, 31);
  
  // Adjust start to the previous Sunday
  const startDay = start.getDay();
  const adjustedStart = new Date(start);
  adjustedStart.setDate(adjustedStart.getDate() - startDay);

  const weeks: { date: string; count: number; day: number; week: number }[][] = [];
  let currentWeek: { date: string; count: number; day: number; week: number }[] = [];
  let weekIndex = 0;

  const current = new Date(adjustedStart);
  while (current <= end || currentWeek.length > 0) {
    const dayOfWeek = current.getDay();
    const dateStr = current.toISOString().split("T")[0];
    const isInYear = current.getFullYear() === year;

    if (dayOfWeek === 0 && currentWeek.length > 0) {
      weeks.push(currentWeek);
      currentWeek = [];
      weekIndex++;
    }

    currentWeek.push({
      date: dateStr,
      count: isInYear ? 0 : -1, // -1 means outside the year
      day: dayOfWeek,
      week: weekIndex,
    });

    if (current > end && dayOfWeek === 6) {
      weeks.push(currentWeek);
      break;
    }

    current.setDate(current.getDate() + 1);
  }

  if (currentWeek.length > 0 && weeks[weeks.length - 1] !== currentWeek) {
    weeks.push(currentWeek);
  }

  return weeks;
}

function getMonthLabels(weeks: { date: string }[][]): { label: string; col: number }[] {
  const labels: { label: string; col: number }[] = [];
  let lastMonth = -1;

  weeks.forEach((week, colIdx) => {
    const firstDay = week.find((d) => d.date);
    if (firstDay) {
      const month = new Date(firstDay.date).getMonth();
      if (month !== lastMonth) {
        labels.push({ label: MONTHS[month], col: colIdx });
        lastMonth = month;
      }
    }
  });

  return labels;
}

export default function ActivityHeatmap({ data, totalApplications }: Props) {
  const year = new Date().getFullYear();

  const { weeks, monthLabels, activeDays, maxStreak } = useMemo(() => {
    const countMap = new Map<string, number>();
    data.forEach((d) => countMap.set(d.date, d.count));

    const weeks = getWeeks(year);

    // Fill in counts
    weeks.forEach((week) => {
      week.forEach((day) => {
        if (day.count !== -1) {
          day.count = countMap.get(day.date) || 0;
        }
      });
    });

    const monthLabels = getMonthLabels(weeks);

    // Calculate active days and max streak
    let activeDays = 0;
    let maxStreak = 0;
    let currentStreak = 0;

    const allDays = weeks.flat().filter((d) => d.count >= 0).sort((a, b) => a.date.localeCompare(b.date));
    allDays.forEach((day) => {
      if (day.count > 0) {
        activeDays++;
        currentStreak++;
        maxStreak = Math.max(maxStreak, currentStreak);
      } else {
        currentStreak = 0;
      }
    });

    return { weeks, monthLabels, activeDays, maxStreak };
  }, [data, year]);

  const cellSize = 11;
  const cellGap = 2;
  const totalCellSize = cellSize + cellGap;
  const leftPadding = 30;
  const topPadding = 20;
  const svgWidth = leftPadding + weeks.length * totalCellSize + 10;
  const svgHeight = topPadding + 7 * totalCellSize + 10;

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <span className="text-lg font-bold text-gray-900">{totalApplications}</span>
          <span className="text-sm text-gray-500 ml-1">applications in {year}</span>
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span>Active days: <strong className="text-gray-700">{activeDays}</strong></span>
          <span>Max streak: <strong className="text-gray-700">{maxStreak}</strong></span>
        </div>
      </div>

      {/* Heatmap */}
      <div className="overflow-x-auto">
        <svg width={svgWidth} height={svgHeight} className="block">
          {/* Month labels */}
          {monthLabels.map(({ label, col }) => (
            <text
              key={`month-${col}`}
              x={leftPadding + col * totalCellSize}
              y={12}
              className="fill-gray-400"
              fontSize={10}
            >
              {label}
            </text>
          ))}

          {/* Day labels */}
          {DAYS.map((label, idx) => (
            label && (
              <text
                key={`day-${idx}`}
                x={0}
                y={topPadding + idx * totalCellSize + cellSize - 1}
                className="fill-gray-400"
                fontSize={9}
              >
                {label}
              </text>
            )
          ))}

          {/* Cells */}
          {weeks.map((week, colIdx) =>
            week.map((day) => (
              day.count >= 0 && (
                <rect
                  key={day.date}
                  x={leftPadding + colIdx * totalCellSize}
                  y={topPadding + day.day * totalCellSize}
                  width={cellSize}
                  height={cellSize}
                  rx={2}
                  fill={getColor(day.count)}
                  className="hover:stroke-gray-400 hover:stroke-1 cursor-pointer"
                >
                  <title>{`${day.date}: ${day.count} application${day.count !== 1 ? "s" : ""}`}</title>
                </rect>
              )
            ))
          )}
        </svg>
      </div>

      {/* Legend */}
      <div className="flex items-center justify-end gap-1 mt-2 text-xs text-gray-400">
        <span>Less</span>
        {[0, 1, 2, 4, 6].map((n) => (
          <div
            key={n}
            className="rounded-sm"
            style={{ width: 10, height: 10, backgroundColor: getColor(n) }}
          />
        ))}
        <span>More</span>
      </div>
    </div>
  );
}
