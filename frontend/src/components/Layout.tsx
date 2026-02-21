import { Outlet } from "react-router-dom";

export default function Layout() {
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
