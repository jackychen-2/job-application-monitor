import { Link, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import JourneySwitcher from "./JourneySwitcher";

export default function Layout() {
  const { user, logoutUser } = useAuth();
  const location = useLocation();
  const dashboardHref =
    location.pathname === "/"
      ? `${location.pathname}${location.search}`
      : window.sessionStorage.getItem("dashboard:returnTo") || "/";
  const markDashboardRestore = () => {
    window.sessionStorage.setItem("dashboard:restore", dashboardHref);
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b border-gray-200">
        <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <Link
              to={dashboardHref}
              onClick={markDashboardRestore}
              className="flex items-center gap-2 text-xl font-bold text-gray-900"
            >
              <span className="text-2xl">📋</span>
              Job Application Monitor
            </Link>
            <nav className="flex items-center gap-4">
              <Link to={dashboardHref} onClick={markDashboardRestore} className="text-sm text-gray-600 hover:text-gray-900">Dashboard</Link>
              <JourneySwitcher />
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
