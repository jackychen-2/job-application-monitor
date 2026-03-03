import { ResponsiveContainer, Sankey, Tooltip } from "recharts";
import type { FlowData } from "../types";

interface Props {
  flowData: FlowData | null;
  loading?: boolean;
  height?: number;
}

const STAGE_ORDER: Record<string, number> = {
  Applications: -2,
  "Recruiter Reach-out": -1,
  已申请: 0,
  OA: 1,
  面试: 2,
  Offer: 3,
  Onboarding: 4,
  拒绝: 5,
  Unknown: 6,
};

const COLORS: Record<string, { node: string; link: string }> = {
  Applications: { node: "#d16ba5", link: "#f5c5e3" },
  "Recruiter Reach-out": { node: "#f97316", link: "#fed7aa" },
  已申请: { node: "#d16ba5", link: "#f4c6e3" },
  OA: { node: "#c8c95a", link: "#ececb0" },
  面试: { node: "#14b8a6", link: "#a7f3d0" },
  Offer: { node: "#dc2626", link: "#fecaca" },
  Onboarding: { node: "#22c55e", link: "#bbf7d0" },
  拒绝: { node: "#9a5d4d", link: "#d9c0b7" },
  Unknown: { node: "#94a3b8", link: "#dbe2ef" },
};

type SankeyNodeDatum = {
  name: string;
  color: string;
  linkColor: string;
  rawCount?: number;
  depth?: number;
  value?: number;
};

type SankeyLinkDatum = {
  source: number;
  target: number;
  value: number;
  rawValue?: number;
  visualWidth?: number;
  isHidden?: boolean;
};

type SankeyData = {
  nodes: SankeyNodeDatum[];
  links: SankeyLinkDatum[];
};

const HIDDEN_SINK_PREFIX = "__sink__";

type SankeyNodeRenderProps = {
  x: number;
  y: number;
  width: number;
  height: number;
  payload: SankeyNodeDatum;
};

type SankeyLinkRenderProps = {
  sourceX: number;
  sourceY: number;
  sourceControlX: number;
  targetX: number;
  targetY: number;
  targetControlX: number;
  linkWidth: number;
  payload?: {
    value?: number;
    visualWidth?: number;
    isHidden?: boolean;
    source?: SankeyNodeDatum;
    target?: SankeyNodeDatum;
  };
};

type TooltipContentProps = {
  active?: boolean;
  payload?: Array<any>;
};

function paletteFor(status: string) {
  return COLORS[status] || COLORS.Unknown;
}

function isHiddenNodeName(name: string): boolean {
  return name.startsWith(HIDDEN_SINK_PREFIX);
}

function normalizeStatus(status: string): string {
  const s = (status || "").trim();
  if (!s) return "Unknown";
  if (s === "Rejected") return "拒绝";
  if (s.toLowerCase() === "reject") return "拒绝";
  return s;
}

function stageRank(status: string): number {
  return STAGE_ORDER[status] ?? 99;
}

function isForwardTransition(fromStatus: string, toStatus: string): boolean {
  if (fromStatus === "Applications") return true;
  const fromRank = stageRank(fromStatus);
  const toRank = stageRank(toStatus);
  if (fromRank === 99 || toRank === 99) return true;
  return toRank > fromRank;
}

function estimateTextWidth(text: string, fontSize: number): number {
  return text.length * (fontSize * 0.58);
}

function toLayoutValue(raw: number): number {
  // Compress large values for layout so dominant columns don't span the full height.
  return Math.max(1, Math.pow(raw, 0.72));
}

function edgeLayoutWeight(from: string, to: string): number {
  // Make root and reject pillars shorter without changing displayed counts.
  let w = 1;
  if (from === "Applications") w *= 0.68;
  if (to === "拒绝") w *= 0.74;
  return w;
}

function buildSankeyData(flowData: FlowData): SankeyData | null {
  const edgeCounts = new Map<string, number>();
  const currentCountByStatus = new Map<string, number>();
  for (const sc of flowData.status_counts) {
    const normalized = normalizeStatus(sc.status);
    currentCountByStatus.set(normalized, (currentCountByStatus.get(normalized) || 0) + sc.count);
  }

  const addEdge = (from: string, to: string, count: number) => {
    if (count <= 0 || from === to) return;
    const key = `${from}→${to}`;
    edgeCounts.set(key, (edgeCounts.get(key) || 0) + count);
  };

  const addCanonicalEdge = (from: string, to: string, count: number) => {
    if (!isForwardTransition(from, to)) return;
    addEdge(from, to, count);
  };

  for (const transition of flowData.transitions) {
    const from = normalizeStatus(transition.from_status);
    const to = normalizeStatus(transition.to_status);
    if (transition.count <= 0 || from === to) continue;
    // Root stage should represent current snapshot distribution, not historical first status.
    if (from === "Applications") continue;
    addCanonicalEdge(from, to, transition.count);
  }

  // Always build root edges from current status snapshot.
  for (const sc of flowData.status_counts) {
    const to = normalizeStatus(sc.status);
    if (sc.count <= 0) continue;
    addCanonicalEdge("Applications", to, sc.count);
  }

  if (edgeCounts.size === 0) return null;
  const filteredCounts = new Map<string, number>(edgeCounts);

  const recruiterRootCount = filteredCounts.get("Applications→Recruiter Reach-out") || 0;
  const recruiterHasOutgoing = Array.from(filteredCounts.keys()).some((k) => k.startsWith("Recruiter Reach-out→"));
  if (recruiterRootCount > 0 && !recruiterHasOutgoing) {
    filteredCounts.set(
      `Recruiter Reach-out→${HIDDEN_SINK_PREFIX}Recruiter Reach-out`,
      recruiterRootCount
    );
  }

  const nodeNames = new Set<string>(["Applications"]);
  for (const key of filteredCounts.keys()) {
    const [from, to] = key.split("→");
    if (from) nodeNames.add(from);
    if (to) nodeNames.add(to);
  }

  const orderedNames = Array.from(nodeNames).sort((a, b) => {
    const rankDiff = stageRank(a) - stageRank(b);
    if (rankDiff !== 0) return rankDiff;
    return a.localeCompare(b);
  });

  const indexByName = new Map<string, number>();
  const nodes: SankeyNodeDatum[] = orderedNames.map((name, index) => {
    indexByName.set(name, index);
    const p = paletteFor(name);
    return {
      name,
      color: p.node,
      linkColor: p.link,
      rawCount: name === "Applications" ? flowData.total : currentCountByStatus.get(name),
    };
  });

  const links: SankeyLinkDatum[] = [];
  for (const [key, count] of filteredCounts.entries()) {
    const [from, to] = key.split("→");
    if (!from || !to) continue;
    const source = indexByName.get(from);
    const target = indexByName.get(to);
    if (source === undefined || target === undefined) continue;
    const hidden = isHiddenNodeName(from) || isHiddenNodeName(to);
    const layoutValue = Math.max(0.2, toLayoutValue(count) * edgeLayoutWeight(from, to));
    links.push({
      source,
      target,
      value: layoutValue,
      rawValue: count,
      isHidden: hidden,
    });
  }

  if (links.length === 0) return null;

  const visibleLinks = links.filter((l) => !l.isHidden);
  const rawValues = visibleLinks.map((l) => l.rawValue ?? l.value);
  const minValue = rawValues.length ? Math.min(...rawValues) : 1;
  const maxValue = rawValues.length ? Math.max(...rawValues) : 1;
  const span = Math.max(1, maxValue - minValue);

  for (const link of links) {
    if (link.isHidden) {
      link.visualWidth = 0.1;
      continue;
    }
    const raw = link.rawValue ?? link.value;
    if (maxValue === minValue) {
      link.visualWidth = 12;
      continue;
    }
    const norm = (raw - minValue) / span;
    link.visualWidth = 8 + Math.pow(norm, 0.82) * 16; // 8..24, auto-adjusted per chart
  }

  return { nodes, links };
}

function LinkShape(props: SankeyLinkRenderProps) {
  const {
    sourceX,
    sourceY,
    sourceControlX,
    targetX,
    targetY,
    targetControlX,
    linkWidth,
    payload,
  } = props;

  if (linkWidth < 0.5) {
    return <path d="" fill="none" stroke="none" />;
  }

  const stroke = payload?.target?.linkColor ?? "#d1d5db";
  const hiddenLink =
    Boolean(payload?.isHidden) ||
    Boolean(payload?.source?.name && isHiddenNodeName(payload.source.name)) ||
    Boolean(payload?.target?.name && isHiddenNodeName(payload.target.name));
  if (hiddenLink) {
    return <path d="" fill="none" stroke="none" />;
  }
  const d = `M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`;
  const visualWidth = Math.max(8, Math.min(24, payload?.visualWidth ?? linkWidth));

  return (
    <path
      d={d}
      fill="none"
      stroke={stroke}
      strokeWidth={visualWidth}
      strokeOpacity={0.62}
      strokeLinecap="butt"
      strokeLinejoin="miter"
    />
  );
}

function NodeShape(props: SankeyNodeRenderProps) {
  const { x, y, width, height, payload } = props;
  if (isHiddenNodeName(payload.name)) {
    return <g />;
  }
  if (height <= 0.8) {
    return <g />;
  }

  const isRoot = payload.name === "Applications";
  const nodeColor = payload.color || "#94a3b8";
  const value = Math.round(payload.rawCount ?? payload.value ?? 0);
  const showLabelCard = !isRoot;
  const compact = height < 18;

  const labelFont = 11;
  const valueFont = 9.5;
  const labelWidth = estimateTextWidth(payload.name, labelFont);
  const valueWidth = estimateTextWidth(String(value), valueFont);
  const compactLabel = `${payload.name} ${value}`;
  const compactW = estimateTextWidth(compactLabel, labelFont);
  const cardW = Math.ceil(Math.max(labelWidth, valueWidth, compactW) + 22);
  const cardH = compact ? 19 : 31;
  const depth = payload.depth || 0;
  const placeOnLeft = depth >= 3;
  const cardX = placeOnLeft ? x - cardW - 8 : x + width + 8;
  const cardY = y + height / 2 - cardH / 2;

  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={nodeColor} rx={2} />

      {isRoot && (
        <text
          x={x - 10}
          y={y + height / 2}
          textAnchor="end"
          dominantBaseline="central"
          fontSize={13}
          fontWeight={650}
          fill="#4b5563"
        >
          Applications
        </text>
      )}

      {showLabelCard && (
        <g>
          <rect
            x={cardX}
            y={cardY}
            width={cardW}
            height={cardH}
            rx={4}
            fill="#ffffff"
            stroke="#e5e7eb"
            strokeWidth={0.8}
            opacity={0.97}
          />
          <rect
            x={cardX + 6}
            y={cardY + (compact ? 7 : 8)}
            width={4}
            height={4}
            rx={1}
            fill={nodeColor}
          />
          <text
            x={cardX + 13}
            y={cardY + 11}
            textAnchor="start"
            fontSize={labelFont}
            fontWeight={550}
            fill="#111827"
          >
            {compact ? compactLabel : payload.name}
          </text>
          {!compact && (
            <text
              x={cardX + 13}
              y={cardY + 24}
              textAnchor="start"
              fontSize={valueFont}
              fontWeight={500}
              fill="#334155"
            >
              {value}
            </text>
          )}
        </g>
      )}
    </g>
  );
}

function SankeyTooltipContent({ active, payload }: TooltipContentProps) {
  if (!active || !payload || payload.length === 0) return null;
  const entry = payload[0];
  const outer = entry?.payload ?? {};
  const inner = outer?.payload ?? {};

  const source = inner?.source?.name ?? outer?.source?.name;
  const target = inner?.target?.name ?? outer?.target?.name;
  const isLink = Boolean(source && target);
  const raw = isLink
    ? (inner?.rawValue ?? entry?.value)
    : (inner?.rawCount ?? entry?.value ?? inner?.value);
  const count = typeof raw === "number" ? Math.round(raw) : 0;
  if (!Number.isFinite(count) || count <= 0) return null;
  const nodeName = inner?.name ?? outer?.name;

  return (
    <div className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm">
      <div className="font-medium text-gray-900">
        {isLink ? `${source} -> ${target}` : (nodeName ?? "Status")}
      </div>
      <div className="text-gray-600">Count: {count}</div>
    </div>
  );
}

export default function SankeyFlow({ flowData, loading, height = 340 }: Props) {
  if (loading || !flowData) {
    return (
      <Shell>
        <Empty text={loading ? "Loading..." : "No data"} />
      </Shell>
    );
  }

  if (flowData.total === 0) {
    return (
      <Shell>
        <Empty text="No application data yet" />
      </Shell>
    );
  }

  const sankeyData = buildSankeyData(flowData);
  if (!sankeyData) {
    return (
      <Shell>
        <Empty text="No flow transitions yet" />
      </Shell>
    );
  }

  const statusSummary = (() => {
    const m = new Map<string, number>();
    for (const row of flowData.status_counts) {
      const name = normalizeStatus(row.status);
      m.set(name, (m.get(name) || 0) + row.count);
    }
    return Array.from(m.entries()).sort((a, b) => {
      const rankDiff = stageRank(a[0]) - stageRank(b[0]);
      if (rankDiff !== 0) return rankDiff;
      return a[0].localeCompare(b[0]);
    });
  })();

  return (
    <Shell>
      <div className="w-full rounded-md bg-slate-50/70" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <Sankey
            data={sankeyData}
            node={NodeShape}
            link={LinkShape}
            nodePadding={16}
            nodeWidth={7}
            linkCurvature={0.52}
            iterations={64}
            sort={true}
            margin={{ top: 26, right: 100, bottom: 26, left: 90 }}
          >
            <Tooltip cursor={false} content={<SankeyTooltipContent />} />
          </Sankey>
        </ResponsiveContainer>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {statusSummary.map(([status, count]) => (
          <span
            key={status}
            className="inline-flex items-center rounded-md border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-700"
          >
            <span className="font-medium">{status}</span>
            <span className="mx-1 text-gray-300">:</span>
            <span className="font-semibold">{count}</span>
          </span>
        ))}
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      <h2 className="text-sm font-medium text-gray-700 mb-3">Application Flow</h2>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="flex items-center justify-center h-56 text-sm text-gray-400">{text}</div>;
}
