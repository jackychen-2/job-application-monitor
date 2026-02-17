import type { StatusCount } from "../types";

interface Props {
  data: StatusCount[];
  total: number;
}

interface SankeyNode {
  label: string;
  value: number;
  color: string;
  x: number;
  y: number;
  height: number;
}

const STATUS_CONFIG: Record<string, { color: string; order: number }> = {
  已申请: { color: "#94a3b8", order: 0 },
  面试: { color: "#3b82f6", order: 1 },
  Offer: { color: "#22c55e", order: 2 },
  拒绝: { color: "#ef4444", order: 3 },
  Unknown: { color: "#eab308", order: 4 },
};

export default function SankeyFlow({ data, total }: Props) {
  if (total === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-sm text-gray-400">
        No application data yet
      </div>
    );
  }

  const sorted = [...data].sort(
    (a, b) => (STATUS_CONFIG[a.status]?.order ?? 99) - (STATUS_CONFIG[b.status]?.order ?? 99)
  );

  const svgWidth = 500;
  const svgHeight = 280;
  const leftX = 40;
  const rightX = 360;
  const barWidth = 100;
  const padding = 8;

  // Left bar: total applications
  const leftBarHeight = svgHeight - 40;
  const leftBarY = 20;

  // Right bars: each status
  const totalRight = sorted.reduce((s, d) => s + d.count, 0);
  let currentY = leftBarY;
  const rightNodes: SankeyNode[] = sorted.map((d) => {
    const height = Math.max(20, (d.count / totalRight) * leftBarHeight);
    const node: SankeyNode = {
      label: d.status,
      value: d.count,
      color: STATUS_CONFIG[d.status]?.color ?? "#9ca3af",
      x: rightX,
      y: currentY,
      height,
    };
    currentY += height + padding;
    return node;
  });

  // Adjust if overflow
  const totalRightHeight = rightNodes.reduce((s, n) => s + n.height + padding, -padding);
  if (totalRightHeight > leftBarHeight) {
    const scale = leftBarHeight / totalRightHeight;
    let y = leftBarY;
    rightNodes.forEach((n) => {
      n.y = y;
      n.height *= scale;
      y += n.height + padding * scale;
    });
  }

  // Flow paths from left bar to right bars
  let leftCurrentY = leftBarY;

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      <h2 className="text-sm font-medium text-gray-700 mb-3">Application Flow</h2>
      <svg width="100%" viewBox={`0 0 ${svgWidth} ${svgHeight}`} className="block">
        {/* Left bar - Total Applications */}
        <rect
          x={leftX}
          y={leftBarY}
          width={barWidth}
          height={leftBarHeight}
          fill="#6366f1"
          rx={4}
          opacity={0.8}
        />
        <text x={leftX + barWidth / 2} y={leftBarY - 6} textAnchor="middle" fontSize={11} className="fill-gray-600 font-medium">
          Applications
        </text>
        <text x={leftX + barWidth / 2} y={leftBarY + leftBarHeight / 2 + 5} textAnchor="middle" fontSize={18} className="fill-white font-bold">
          {total}
        </text>

        {/* Flow paths and right bars */}
        {rightNodes.map((node) => {
          const flowHeight = (node.value / totalRight) * leftBarHeight;
          const leftY = leftCurrentY;
          leftCurrentY += flowHeight;

          return (
            <g key={node.label}>
              {/* Flow path */}
              <path
                d={`
                  M ${leftX + barWidth} ${leftY}
                  C ${leftX + barWidth + 80} ${leftY}, ${node.x - 80} ${node.y}, ${node.x} ${node.y}
                  L ${node.x} ${node.y + node.height}
                  C ${node.x - 80} ${node.y + node.height}, ${leftX + barWidth + 80} ${leftY + flowHeight}, ${leftX + barWidth} ${leftY + flowHeight}
                  Z
                `}
                fill={node.color}
                opacity={0.25}
              />

              {/* Right bar */}
              <rect
                x={node.x}
                y={node.y}
                width={barWidth}
                height={node.height}
                fill={node.color}
                rx={4}
                opacity={0.8}
              />

              {/* Label */}
              <text
                x={node.x + barWidth + 8}
                y={node.y + node.height / 2 + 4}
                fontSize={11}
                className="fill-gray-600"
              >
                {node.label}
              </text>

              {/* Count */}
              <text
                x={node.x + barWidth / 2}
                y={node.y + node.height / 2 + 4}
                textAnchor="middle"
                fontSize={node.height > 25 ? 13 : 10}
                className="fill-white font-semibold"
              >
                {node.value}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
