import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import Avatar from "./Avatar";

function SettingsIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="h-4 w-4">
      <path
        d="M10 2.5 11.2 4.8l2.5.4.8 2.4 2.1 1.4-.8 2.4.8 2.4-2.1 1.4-.8 2.4-2.5.4L10 17.5l-1.2 2.3-2.5-.4-.8-2.4-2.1-1.4.8-2.4-.8-2.4 2.1-1.4.8-2.4 2.5-.4L10 2.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <circle cx="10" cy="10" r="2.8" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

function LogoutIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="h-4 w-4">
      <path
        d="M7 4.5H4.5A1.5 1.5 0 0 0 3 6v8a1.5 1.5 0 0 0 1.5 1.5H7"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M11 6.5 15 10l-4 3.5M15 10H7"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function UserMenu() {
  const { user, logoutUser } = useAuth();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  if (!user) {
    return null;
  }

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logoutUser();
    } finally {
      setLoggingOut(false);
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-3 rounded-full border border-gray-200 bg-white px-2 py-1.5 text-left shadow-sm transition hover:border-gray-300 hover:bg-gray-50"
      >
        <Avatar
          avatarUrl={user.avatar_url}
          displayName={user.display_name}
          email={user.email}
          sizeClassName="h-9 w-9"
          textClassName="text-sm"
        />
        <div className="hidden min-w-0 sm:block">
          <div className="max-w-[10rem] truncate text-sm font-medium text-gray-900">
            {user.display_name || user.email.split("@")[0]}
          </div>
          <div className="max-w-[10rem] truncate text-xs text-gray-500">{user.email}</div>
        </div>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="User menu"
          className="absolute right-0 top-full z-40 mt-3 w-80 overflow-hidden rounded-3xl border border-gray-200 bg-white shadow-2xl shadow-gray-200/80"
        >
          <div className="bg-gradient-to-br from-emerald-50 via-white to-sky-50 px-4 py-4">
            <div className="flex items-center gap-3">
              <Avatar
                avatarUrl={user.avatar_url}
                displayName={user.display_name}
                email={user.email}
                sizeClassName="h-12 w-12"
                textClassName="text-base"
              />
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-gray-900">
                  {user.display_name || "Account"}
                </div>
                <div className="truncate text-xs text-gray-500">{user.email}</div>
              </div>
            </div>
          </div>

          <div className="p-2">
            <Link
              to="/account"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex items-center gap-3 rounded-2xl px-3 py-3 text-sm text-gray-700 transition hover:bg-gray-50 hover:text-gray-900"
            >
              <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-gray-100 text-gray-600">
                <SettingsIcon />
              </span>
              <span className="font-medium">Manage account</span>
            </Link>
            <button
              type="button"
              role="menuitem"
              onClick={() => void handleLogout()}
              disabled={loggingOut}
              className="flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-sm text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-red-50 text-red-500">
                <LogoutIcon />
              </span>
              <span className="font-medium">{loggingOut ? "Logging out..." : "Log out"}</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
