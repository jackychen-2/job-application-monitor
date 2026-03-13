import PipelineProgressPanel from "../components/PipelineProgressPanel";
import type { HourlyCount, Stats, DailyCount } from "../types";

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function isoHour(date: Date): string {
  return date.toISOString().slice(0, 19);
}

function buildDailyApplications(anchor: Date): DailyCount[] {
  const points: DailyCount[] = [];

  for (let index = 75; index >= 0; index -= 1) {
    const day = new Date(anchor);
    day.setUTCDate(anchor.getUTCDate() - index);

    const wave = Math.round((Math.sin(index / 5) + 1.2) * 2);
    const trend = index < 18 ? 3 : index < 42 ? 2 : 1;
    const spike = index % 19 === 0 ? 5 : 0;

    points.push({
      date: isoDate(day),
      count: Math.max(0, wave + trend + spike - 1),
    });
  }

  return points;
}

function buildHourlyApplications(anchor: Date): HourlyCount[] {
  const points: HourlyCount[] = [];

  for (let index = 23; index >= 0; index -= 1) {
    const hour = new Date(anchor);
    hour.setUTCHours(anchor.getUTCHours() - index);

    const surge = index === 20 || index === 19 ? 6 : 0;
    const mid = index === 11 || index === 6 ? 2 : 0;
    const tail = index < 4 ? 1 : 0;

    points.push({
      timestamp: `${isoHour(hour)}Z`,
      count: surge + mid + tail,
    });
  }

  return points;
}

function buildPreviewStats(): Stats {
  const anchor = new Date("2026-03-12T20:00:00Z");

  return {
    total_applications: 160,
    status_breakdown: [
      { status: "Recruiter Reach-out", count: 5 },
      { status: "面试", count: 3 },
      { status: "Offer", count: 2 },
      { status: "拒绝", count: 76 },
      { status: "已申请", count: 60 },
      { status: "OA", count: 14 },
    ],
    recent_applications: [],
    total_emails_scanned: 1240,
    total_llm_cost: 3.2814,
    daily_llm_costs: [],
    daily_applications: buildDailyApplications(anchor),
    hourly_applications_24h: buildHourlyApplications(anchor),
  };
}

const PREVIEW_STATS = buildPreviewStats();

export default function ProgressPreview() {
  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,#f8fafc,transparent_28%),linear-gradient(180deg,#f3f4f6,#eef2f7)] px-4 py-10 sm:px-6">
      <div className="mx-auto max-w-3xl">
        <div className="mb-5 rounded-[20px] border border-white/70 bg-white/65 px-4 py-3 text-sm text-slate-600 shadow-[0_16px_36px_-28px_rgba(15,23,42,0.35)] backdrop-blur-md">
          Progress preview uses mock data and does not require Google login.
        </div>
        <PipelineProgressPanel stats={PREVIEW_STATS} loading={false} />
      </div>
    </div>
  );
}
