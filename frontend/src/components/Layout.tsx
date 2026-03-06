import { Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useJourney } from "../journey/JourneyContext";

export default function Layout() {
  const { user, logoutUser } = useAuth();
  const { loading, journeys, activeJourney, createJourney, activateJourney, renameJourney } = useJourney();

  const handleCreateJourney = async () => {
    const defaultName = `Journey ${new Date().toISOString().slice(0, 10)}`;
    const name = window.prompt("Create a new journey", defaultName);
    if (name === null) return;
    try {
      await createJourney(name.trim() || defaultName);
    } catch (err) {
      console.error("Failed to create journey:", err);
      alert("Failed to create journey");
    }
  };

  const handleRenameJourney = async () => {
    if (!activeJourney) return;
    const nextName = window.prompt("Rename journey", activeJourney.name);
    if (nextName === null) return;
    const cleaned = nextName.trim();
    if (!cleaned) return;
    try {
      await renameJourney(activeJourney.id, cleaned);
    } catch (err) {
      console.error("Failed to rename journey:", err);
      alert("Failed to rename journey");
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b border-gray-200">
        <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <a href="/" className="flex items-center gap-2 text-xl font-bold text-gray-900">
              <span className="text-2xl">📋</span>
              Job Application Monitor
            </a>
            <nav className="flex items-center gap-4">
              <a href="/" className="text-sm text-gray-600 hover:text-gray-900">Dashboard</a>
              <a href="/eval" className="text-sm text-gray-600 hover:text-gray-900">Evaluation</a>
              <div className="flex items-center gap-2">
                <select
                  value={activeJourney?.id ?? ""}
                  onChange={(e) => void activateJourney(Number(e.target.value))}
                  disabled={loading || journeys.length === 0}
                  className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700"
                  title="Active Journey"
                >
                  {journeys.length === 0 ? (
                    <option value="">No journeys</option>
                  ) : (
                    journeys.map((journey) => (
                      <option key={journey.id} value={journey.id}>
                        {journey.name}
                      </option>
                    ))
                  )}
                </select>
                <button
                  onClick={() => void handleCreateJourney()}
                  className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
                  title="Create Journey"
                >
                  + Journey
                </button>
                <button
                  onClick={() => void handleRenameJourney()}
                  disabled={!activeJourney}
                  className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  title="Rename Active Journey"
                >
                  Rename
                </button>
              </div>
              <span className="text-xs text-gray-500">{user?.email}</span>
              <button
                onClick={() => void logoutUser()}
                className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                Log out
              </button>
            </nav>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <Outlet />
      </main>
    </div>
  );
}
