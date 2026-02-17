import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import type { DailyCost } from "../types";

interface Props {
  data: DailyCost[];
  totalCost: number;
}

export default function CostChart({ data, totalCost }: Props) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-sm text-gray-400">
        No LLM cost data yet
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-gray-700">LLM Cost History</span>
        <span className="text-sm font-semibold text-indigo-600">
          Total: ${totalCost.toFixed(4)}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11 }}
            tickFormatter={(v: string) => {
              const d = new Date(v);
              return `${d.getMonth() + 1}/${d.getDate()}`;
            }}
          />
          <YAxis
            tick={{ fontSize: 11 }}
            tickFormatter={(v: number) => `$${v.toFixed(4)}`}
            width={70}
          />
          <Tooltip
            formatter={(value: number) => [`$${value.toFixed(6)}`, "Cost"]}
            labelFormatter={(label: string) => `Date: ${label}`}
          />
          <Line
            type="monotone"
            dataKey="cost"
            stroke="#6366f1"
            strokeWidth={2}
            dot={{ r: 3, fill: "#6366f1" }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
