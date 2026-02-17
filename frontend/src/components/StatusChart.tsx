import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from "recharts";
import type { StatusCount } from "../types";

interface Props {
  data: StatusCount[];
}

const COLORS: Record<string, string> = {
  已申请: "#6b7280",
  面试: "#3b82f6",
  Offer: "#22c55e",
  拒绝: "#ef4444",
  Unknown: "#eab308",
};

export default function StatusChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-sm text-gray-400">
        No data yet
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie
          data={data}
          dataKey="count"
          nameKey="status"
          cx="50%"
          cy="50%"
          outerRadius={80}
          label={({ status, count }) => `${status} (${count})`}
          labelLine={false}
        >
          {data.map((entry) => (
            <Cell key={entry.status} fill={COLORS[entry.status] ?? "#9ca3af"} />
          ))}
        </Pie>
        <Tooltip />
        <Legend />
      </PieChart>
    </ResponsiveContainer>
  );
}
