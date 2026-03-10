import { useEffect, useRef, useState } from "react";
import { useJourney } from "../journey/JourneyContext";

function BriefcaseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4 text-gray-500">
      <path
        d="M9 7V6a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <rect
        x="4"
        y="7"
        width="16"
        height="12"
        rx="2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      />
      <path
        d="M4 12h16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="h-4 w-4">
      <path
        d="m5 10 3 3 7-7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ChevronDownIcon({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 20 20"
      aria-hidden="true"
      className={`h-4 w-4 text-gray-500 transition-transform ${open ? "rotate-180" : ""}`}
    >
      <path
        d="m5 7.5 5 5 5-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function JourneySwitcher() {
  const { loading, journeys, activeJourney, createJourney, activateJourney, renameJourney } = useJourney();
  const [menuOpen, setMenuOpen] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [pendingJourneyId, setPendingJourneyId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const skipBlurCommitRef = useRef(false);

  useEffect(() => {
    if (!isRenaming) {
      setDraftName(activeJourney?.name ?? "");
    }
  }, [activeJourney?.id, activeJourney?.name, isRenaming]);

  useEffect(() => {
    if (!menuOpen) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [menuOpen]);

  useEffect(() => {
    if (!isRenaming || !inputRef.current) return;
    inputRef.current.focus();
    inputRef.current.select();
  }, [isRenaming]);

  const displayName = loading ? "Loading journeys..." : activeJourney?.name ?? "Create a journey";

  const startRenaming = () => {
    if (!activeJourney || loading) return;
    skipBlurCommitRef.current = false;
    setMenuOpen(false);
    setDraftName(activeJourney.name);
    setIsRenaming(true);
  };

  const cancelRenaming = () => {
    setDraftName(activeJourney?.name ?? "");
    setIsRenaming(false);
  };

  const commitRename = async () => {
    if (!activeJourney || isSaving) {
      setIsRenaming(false);
      return;
    }

    const cleaned = draftName.trim();
    if (!cleaned || cleaned === activeJourney.name) {
      setDraftName(activeJourney.name);
      setIsRenaming(false);
      return;
    }

    setIsSaving(true);
    try {
      await renameJourney(activeJourney.id, cleaned);
      setIsRenaming(false);
    } catch (err) {
      console.error("Failed to rename journey:", err);
      setDraftName(activeJourney.name);
      setIsRenaming(false);
      alert("Failed to rename journey");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCreateJourney = async () => {
    const defaultName = `Journey ${new Date().toISOString().slice(0, 10)}`;
    const name = window.prompt("Create a new journey", defaultName);
    if (name === null) return;

    setCreating(true);
    try {
      await createJourney(name.trim() || defaultName);
      setMenuOpen(false);
    } catch (err) {
      console.error("Failed to create journey:", err);
      alert("Failed to create journey");
    } finally {
      setCreating(false);
    }
  };

  const handleActivateJourney = async (journeyId: number) => {
    if (journeyId === activeJourney?.id) {
      setMenuOpen(false);
      return;
    }

    setPendingJourneyId(journeyId);
    try {
      await activateJourney(journeyId);
      setMenuOpen(false);
    } catch (err) {
      console.error("Failed to activate journey:", err);
      alert("Failed to switch journey");
    } finally {
      setPendingJourneyId(null);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <div className="inline-flex items-center rounded-xl border border-gray-300 bg-white shadow-sm">
        <div className="flex items-center gap-2 pl-3 text-sm text-gray-500">
          <BriefcaseIcon />
        </div>
        {isRenaming ? (
          <input
            ref={inputRef}
            type="text"
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
            onBlur={() => {
              if (skipBlurCommitRef.current) {
                skipBlurCommitRef.current = false;
                return;
              }
              void commitRename();
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                skipBlurCommitRef.current = true;
                void commitRename();
              }
              if (event.key === "Escape") {
                event.preventDefault();
                skipBlurCommitRef.current = true;
                cancelRenaming();
              }
            }}
            maxLength={40}
            className="min-w-[11rem] bg-transparent px-3 py-2 text-sm font-medium text-gray-900 outline-none placeholder:text-gray-400"
            aria-label="Rename current journey"
            disabled={isSaving}
          />
        ) : (
          <button
            type="button"
            onDoubleClick={startRenaming}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === "F2") {
                event.preventDefault();
                startRenaming();
              }
            }}
            disabled={!activeJourney || loading}
            title={activeJourney ? "Double-click to rename" : undefined}
            className="min-w-[11rem] px-3 py-2 text-left text-sm font-medium text-gray-900 transition hover:bg-gray-50 hover:underline hover:decoration-dotted hover:underline-offset-4 disabled:cursor-default disabled:text-gray-400 disabled:hover:bg-white disabled:hover:no-underline"
          >
            {displayName}
          </button>
        )}
        <button
          type="button"
          onClick={() => setMenuOpen((current) => !current)}
          disabled={loading || creating}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="Open journey menu"
          className="rounded-r-xl border-l border-gray-200 px-2.5 py-2 text-gray-500 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <ChevronDownIcon open={menuOpen} />
        </button>
      </div>

      {menuOpen && (
        <div
          role="menu"
          aria-label="Journey menu"
          className="absolute right-0 top-full z-30 mt-3 w-80 rounded-2xl border border-gray-200 bg-white p-2 shadow-xl shadow-gray-200/70"
        >
          <div className="px-3 pb-2 pt-1">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">Journeys</p>
            <p className="mt-1 text-xs text-gray-500">Use the chevron to switch. Double-click the name above to rename.</p>
          </div>
          <div className="max-h-72 space-y-1 overflow-y-auto px-1 pb-2">
            {journeys.length === 0 ? (
              <div className="rounded-xl px-3 py-4 text-sm text-gray-500">No journeys yet.</div>
            ) : (
              journeys.map((journey) => {
                const isActive = journey.id === activeJourney?.id;
                const isPending = pendingJourneyId === journey.id;

                return (
                  <button
                    key={journey.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={isActive}
                    onClick={() => void handleActivateJourney(journey.id)}
                    disabled={isPending}
                    className={`flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left text-sm transition ${
                      isActive
                        ? "bg-emerald-50 text-emerald-700"
                        : "text-gray-700 hover:bg-gray-50"
                    } ${isPending ? "opacity-60" : ""}`}
                  >
                    <div className="min-w-0">
                      <div className="truncate font-medium">{journey.name}</div>
                      <div className="mt-0.5 text-xs text-gray-400">
                        {isActive ? "Current journey" : "Switch to this journey"}
                      </div>
                    </div>
                    <div className="ml-3 flex h-5 w-5 items-center justify-center text-emerald-600">
                      {isActive ? <CheckIcon /> : null}
                    </div>
                  </button>
                );
              })
            )}
          </div>
          <div className="border-t border-gray-100 px-1 pt-2">
            <button
              type="button"
              role="menuitem"
              onClick={() => void handleCreateJourney()}
              disabled={creating}
              className="flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span>New Journey</span>
              <span className="text-base leading-none text-gray-400">+</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
