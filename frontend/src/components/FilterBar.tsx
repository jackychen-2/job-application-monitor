interface Props {
  statusFilter: string;
  companySearch: string;
  total: number;
  onStatusChange: (status: string) => void;
  onCompanyChange: (company: string) => void;
  onAddApplication?: () => void;
}

const QUICK_FILTERS = [
  { label: "All", value: "" },
  { label: "Active", value: "active" },
  { label: "Interview", value: "面试" },
  { label: "Offer", value: "Offer" },
  { label: "Rejected", value: "拒绝" },
] as const;

export default function FilterBar({
  statusFilter,
  companySearch,
  total,
  onStatusChange,
  onCompanyChange,
  onAddApplication,
}: Props) {
  return (
    <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
      <div className="flex flex-wrap items-center gap-2">
        {onAddApplication && (
          <button
            onClick={onAddApplication}
            className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 whitespace-nowrap"
          >
            + Add
          </button>
        )}

        {QUICK_FILTERS.map((filter) => {
          const selected = statusFilter === filter.value;
          return (
            <button
              key={filter.label}
              type="button"
              onClick={() => onStatusChange(filter.value)}
              aria-pressed={selected}
              className={`rounded-full border px-3 py-1.5 text-sm font-medium transition-colors ${
                selected
                  ? "border-indigo-200 bg-indigo-50 text-indigo-700"
                  : "border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:text-gray-900"
              }`}
            >
              {filter.label}
            </button>
          );
        })}
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="w-full sm:w-72">
          <label htmlFor="company-search" className="sr-only">
            Company
          </label>
          <input
            id="company-search"
            type="text"
            placeholder="Search company..."
            value={companySearch}
            onChange={(e) => onCompanyChange(e.target.value)}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
          />
        </div>
        <div className="text-sm text-gray-500 whitespace-nowrap">
          {total} total
        </div>
      </div>
    </div>
  );
}
