import type { FlowData } from "../types";

interface Props {
  flowData: FlowData | null;
  loading?: boolean;
}

const STAGE_ORDER: Record<string, number> = {
  "Recruiter Reach-out": -1, 已申请: 0, OA: 1, 面试: 2, Offer: 3, Onboarding: 4, 拒绝: 5, Unknown: 6,
};

const COLORS: Record<string, { bar: string; flow: string }> = {
  "Recruiter Reach-out": { bar: "#ea580c", flow: "#fed7aa" },
  已申请:      { bar: "#a8a29e", flow: "#d6d3d1" },
  OA:         { bar: "#0891b2", flow: "#a5f3fc" },
  面试:        { bar: "#4a6fa5", flow: "#b4c0db" },
  Offer:       { bar: "#3d8b6e", flow: "#a3d4c7" },
  Onboarding:  { bar: "#0f766e", flow: "#99f6e4" },
  拒绝:        { bar: "#c77d4f", flow: "#ecc9b0" },
  "No Offer":  { bar: "#d4735e", flow: "#f0bfb0" },
  Unknown:     { bar: "#b5a339", flow: "#e0d99e" },
};

function col(key: string) {
  return COLORS[key] || COLORS["Unknown"];
}

interface SNode {
  id: string;
  label: string;
  value: number;
  barColor: string;
  flowColor: string;
  children: SNode[];
}

interface LNode extends SNode {
  x: number; y: number; h: number;
  children: LNode[];
}

interface Edge {
  pX: number; pY: number; pH: number;
  cX: number; cY: number; cH: number;
  color: string;
}

/**
 * Tree-style Sankey. Each status appears ONCE.
 *
 * Applications (91)
 * ├── 面试 (3)          ← 2 still interviewing + 1 who got No Offer
 * │   └── No Offer (1)  ← interviewed then rejected
 * ├── Offer (2)
 * ├── Rejected (54)     ← directly rejected (excludes No Offer)
 * └── No Answer (32)    ← still in 已申请
 */
export default function SankeyFlow({ flowData, loading }: Props) {
  if (loading || !flowData) {
    return <Shell><Empty text={loading ? "Loading…" : "No data"} /></Shell>;
  }
  const { status_counts, transitions, total } = flowData;
  if (total === 0) {
    return <Shell><Empty text="No application data yet" /></Shell>;
  }

  // Current counts
  const cur: Record<string, number> = {};
  for (const sc of status_counts) cur[sc.status] = sc.count;

  // Forward transitions only
  const fwd: Record<string, Record<string, number>> = {};
  for (const t of transitions) {
    if ((STAGE_ORDER[t.to_status] ?? 99) <= (STAGE_ORDER[t.from_status] ?? 99)) continue;
    if (!fwd[t.from_status]) fwd[t.from_status] = {};
    fwd[t.from_status][t.to_status] = (fwd[t.from_status][t.to_status] || 0) + t.count;
  }

  // Count downstream rejections (for de-duplication)
  // e.g., 面试→拒绝 = 1 person. This 1 is part of current 拒绝 (55),
  // but we show them under 面试 as "No Offer", so subtract from root-level 拒绝.
  function countDownstreamRejections(from: string, visited: Set<string>): number {
    if (visited.has(from)) return 0;
    visited.add(from);
    let total = 0;
    const exits = fwd[from] || {};
    for (const [to, count] of Object.entries(exits)) {
      if (to === "拒绝") {
        total += count;
      } else if (to !== "Unknown") {
        total += countDownstreamRejections(to, new Set(visited));
      }
    }
    return total;
  }

  // Build tree: each intermediate stage (面试, Offer) appears once
  // with children for onward transitions. No "remaining" duplicate nodes.
  function buildBranch(stage: string, visited: Set<string>): SNode[] {
    if (visited.has(stage)) return [];
    visited.add(stage);

    const children: SNode[] = [];
    const exits = fwd[stage] || {};
    const sorted = Object.entries(exits).sort(
      (a, b) => (STAGE_ORDER[a[0]] ?? 99) - (STAGE_ORDER[b[0]] ?? 99)
    );

    for (const [to, count] of sorted) {
      if (count <= 0) continue;
      if (to === "拒绝") {
        // Rejection after this stage = "No Offer" (or "Declined" etc.)
        const label = stage === "面试" ? "No Offer" : "拒绝";
        const c = col(label);
        children.push({
          id: `${stage}->no-offer`,
          label,
          value: count,
          barColor: c.bar, flowColor: c.flow,
          children: [],
        });
      } else {
        // Onward stage (面试, Offer, etc.)
        const subChildren = buildBranch(to, new Set(visited));
        // Total for this branch = current count in 'to' + downstream
        const downstreamTotal = subChildren.reduce((s, c) => s + c.value, 0);
        const branchTotal = (cur[to] || 0) + downstreamTotal;
        if (branchTotal > 0) {
          const c = col(to);
          children.push({
            id: `${stage}->${to}`,
            label: to,
            value: branchTotal,
            barColor: c.bar, flowColor: c.flow,
            children: subChildren,
          });
        }
      }
    }
    return children;
  }

  // Build root children
  const rootChildren: SNode[] = [];

  // 1. Intermediate stages with transitions from 已申请
  const midStageChildren = buildBranch("已申请", new Set());
  for (const child of midStageChildren) {
    if (child.label !== "拒绝" && child.label !== "No Offer") {
      rootChildren.push(child);
    }
  }

  // 2. Offer (if has current count but not covered by transitions)
  const offerInTree = rootChildren.find((c) => c.label === "Offer");
  if (!offerInTree && (cur["Offer"] || 0) > 0) {
    const c = col("Offer");
    rootChildren.push({
      id: "offer-direct",
      label: "Offer",
      value: cur["Offer"] || 0,
      barColor: c.bar, flowColor: c.flow,
      children: [],
    });
  }

  // 3. OA (if has current count but not covered by transitions)
  const oaInTree = rootChildren.find((c) => c.label === "OA");
  if (!oaInTree && (cur["OA"] || 0) > 0) {
    const c = col("OA");
    rootChildren.push({
      id: "oa-direct",
      label: "OA",
      value: cur["OA"] || 0,
      barColor: c.bar, flowColor: c.flow,
      children: [],
    });
  }

  // 4. Onboarding (if has current count but not covered by transitions)
  const onboardingInTree = rootChildren.find((c) => c.label === "Onboarding");
  if (!onboardingInTree && (cur["Onboarding"] || 0) > 0) {
    const c = col("Onboarding");
    rootChildren.push({
      id: "onboarding-direct",
      label: "Onboarding",
      value: cur["Onboarding"] || 0,
      barColor: c.bar, flowColor: c.flow,
      children: [],
    });
  }

  // 5. Rejected (adjusted: total 拒绝 - those shown as "No Offer" in sub-branches)
  const downstreamRejects = countDownstreamRejections("已申请", new Set());
  const directRejects = (cur["拒绝"] || 0) - downstreamRejects;
  if (directRejects > 0) {
    const c = col("拒绝");
    rootChildren.push({
      id: "rejected",
      label: "拒绝",
      value: directRejects,
      barColor: c.bar, flowColor: c.flow,
      children: [],
    });
  }

  // 6. Still in recruiter reach-out
  if ((cur["Recruiter Reach-out"] || 0) > 0) {
    const c = col("Recruiter Reach-out");
    rootChildren.push({
      id: "recruiter-remaining",
      label: "Recruiter Reach-out",
      value: cur["Recruiter Reach-out"] || 0,
      barColor: c.bar, flowColor: c.flow,
      children: [],
    });
  }

  // 7. Still in 已申请
  if ((cur["已申请"] || 0) > 0) {
    const c = col("已申请");
    rootChildren.push({
      id: "applied-remaining",
      label: "已申请",
      value: cur["已申请"] || 0,
      barColor: c.bar, flowColor: c.flow,
      children: [],
    });
  }

  const root: SNode = {
    id: "root",
    label: "Applications",
    value: total,
    barColor: "#78716c",
    flowColor: "#d6d3d1",
    children: rootChildren,
  };

  // ── Layout ──────────────────────────────────────────────
  const svgW = 600, svgH = 340;
  const padT = 24, padB = 16;
  const availH = svgH - padT - padB;
  const barW = 8, levelGap = 170, nodeGap = 5, padL = 100;

  function layoutTree(n: SNode, x: number, y: number, h: number): LNode {
    const kids: LNode[] = [];
    if (n.children.length > 0) {
      const sum = n.children.reduce((s, c) => s + c.value, 0);
      const gaps = Math.max(0, n.children.length - 1) * nodeGap;
      const content = h - gaps;
      let cy = y;
      for (const child of n.children) {
        const ch = sum > 0 ? Math.max(3, (child.value / sum) * content) : 0;
        kids.push(layoutTree(child, x + levelGap, cy, ch));
        cy += ch + nodeGap;
      }
    }
    return { ...n, x, y, h, children: kids };
  }

  const tree = layoutTree(root, padL, padT, availH);

  // Collect edges + nodes
  const edges: Edge[] = [];
  const nodes: LNode[] = [];

  function collect(n: LNode) {
    nodes.push(n);
    let py = n.y;
    for (const child of n.children) {
      const ph = n.value > 0 ? (child.value / n.value) * n.h : 0;
      edges.push({
        pX: n.x + barW, pY: py, pH: ph,
        cX: child.x, cY: child.y, cH: child.h,
        color: child.flowColor,
      });
      py += ph;
      collect(child);
    }
  }
  collect(tree);

  return (
    <Shell>
      <svg width="100%" viewBox={`0 0 ${svgW} ${svgH}`} className="block">
        {edges.map((e, i) => {
          const cx1 = e.pX + (e.cX - e.pX) * 0.4;
          const cx2 = e.pX + (e.cX - e.pX) * 0.6;
          return (
            <path key={i}
              d={`M${e.pX} ${e.pY} C${cx1} ${e.pY},${cx2} ${e.cY},${e.cX} ${e.cY}
                  L${e.cX} ${e.cY + e.cH} C${cx2} ${e.cY + e.cH},${cx1} ${e.pY + e.pH},${e.pX} ${e.pY + e.pH}Z`}
              fill={e.color} opacity={0.5}
            />
          );
        })}
        {nodes.map((n) => {
          if (n.h < 1) return null;
          const isRoot = n.id === "root";
          const mid = n.y + n.h / 2;
          return (
            <g key={n.id}>
              <rect x={n.x} y={n.y} width={barW} height={n.h} fill={n.barColor} rx={2} />
              <text
                x={isRoot ? n.x - 8 : n.x + barW + 8}
                y={mid}
                textAnchor={isRoot ? "end" : "start"}
                dominantBaseline="central"
                fontSize={isRoot ? 13 : 12}
                fontWeight={isRoot ? 600 : 500}
                fill="#4b5563"
              >
                {n.label}
              </text>
              {!isRoot && n.h >= 12 && (
                <text
                  x={n.x + barW + 8}
                  y={mid + 15}
                  textAnchor="start"
                  fontSize={10}
                  fill="#9ca3af"
                >
                  {n.value}
                </text>
              )}
            </g>
          );
        })}
      </svg>
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
