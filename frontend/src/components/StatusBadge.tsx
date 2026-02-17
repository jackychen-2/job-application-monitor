import { STATUS_COLORS } from "../types";

interface Props {
  status: string;
}

export default function StatusBadge({ status }: Props) {
  const colorClass = STATUS_COLORS[status] ?? "bg-gray-100 text-gray-600";
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${colorClass}`}
    >
      {status}
    </span>
  );
}
