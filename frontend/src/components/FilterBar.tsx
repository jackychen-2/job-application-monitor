import { STATUSES } from "../types";

interface Props {
  statusFilter: string;
  companySearch: string;
  onStatusChange: (status: string) => void;
  onCompanyChange: (company: string) => void;
}

export default function FilterBar({
  statusFilter,
  companySearch,
  onStatusChange,
  onCompanyChange,
}: Props) {
  return (
    <div className="flex flex-col sm:flex-row gap-3">
      {/* Status filter */}
      <div>
        <label htmlFor="status-filter" className="sr-only">
          Status
        </label>
        <select
          id="status-filter"
          value={statusFilter}
          onChange={(e) => onStatusChange(e.target.value)}
          className="block w-full sm:w-40 rounded-md border border-gray-300 bg-white py-2 px-3 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        >
          <option value="">All Statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {/* Company search */}
      <div className="flex-1">
        <label htmlFor="company-search" className="sr-only">
          Company
        </label>
        <input
          id="company-search"
          type="text"
          placeholder="Search company..."
          value={companySearch}
          onChange={(e) => onCompanyChange(e.target.value)}
          className="block w-full rounded-md border border-gray-300 py-2 px-3 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        />
      </div>
    </div>
  );
}
