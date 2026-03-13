import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getAccount, updateProfile } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import Avatar from "../components/Avatar";
import { useJourney } from "../journey/JourneyContext";
import type { AccountDetails } from "../types";

function formatDateTime(value: string | null): string {
  if (!value) {
    return "Not available";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function AccountPage() {
  const navigate = useNavigate();
  const { user, refreshAuth, loginWithGoogle, deleteAccountUser } = useAuth();
  const { activeJourney, journeys, deleteJourney } = useJourney();
  const [account, setAccount] = useState<AccountDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [deletingJourney, setDeletingJourney] = useState(false);
  const [deletingAccount, setDeletingAccount] = useState(false);
  const [journeyConfirm, setJourneyConfirm] = useState("");
  const [accountConfirm, setAccountConfirm] = useState("");

  useEffect(() => {
    if (!user) {
      return;
    }

    let cancelled = false;

    const loadAccount = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getAccount();
        if (cancelled) {
          return;
        }
        setAccount(data);
        setDraftName(data.display_name ?? "");
      } catch (err) {
        if (!cancelled) {
          console.error("Failed to load account:", err);
          setError("Failed to load account settings.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadAccount();

    return () => {
      cancelled = true;
    };
  }, [user]);

  const handleSaveProfile = async () => {
    setSavingProfile(true);
    setError(null);
    try {
      const updated = await updateProfile({ display_name: draftName.trim() || null });
      setAccount(updated);
      setDraftName(updated.display_name ?? "");
      await refreshAuth();
    } catch (err) {
      console.error("Failed to update profile:", err);
      setError("Failed to update profile.");
    } finally {
      setSavingProfile(false);
    }
  };

  const handleDeleteJourney = async () => {
    if (!activeJourney) {
      return;
    }

    setDeletingJourney(true);
    setError(null);
    try {
      await deleteJourney(activeJourney.id);
      setJourneyConfirm("");
      const updated = await getAccount();
      setAccount(updated);
      navigate("/");
    } catch (err) {
      console.error("Failed to delete journey:", err);
      setError("Failed to delete the current journey.");
    } finally {
      setDeletingJourney(false);
    }
  };

  const handleDeleteAccount = async () => {
    setDeletingAccount(true);
    setError(null);
    try {
      await deleteAccountUser();
      navigate("/");
    } catch (err) {
      console.error("Failed to delete account:", err);
      setError("Failed to delete account.");
    } finally {
      setDeletingAccount(false);
    }
  };

  if (loading) {
    return (
      <div className="rounded-3xl border border-gray-200 bg-white p-8 shadow-sm">
        <p className="text-sm text-gray-500">Loading account settings...</p>
      </div>
    );
  }

  if (!account || !user) {
    return (
      <div className="rounded-3xl border border-red-200 bg-red-50 p-8 text-sm text-red-700">
        {error || "Account settings are unavailable right now."}
      </div>
    );
  }

  const nameChanged = (account.display_name ?? "") !== draftName.trim();
  const journeyDeleteReady = activeJourney != null && journeyConfirm.trim() === activeJourney.name;
  const accountDeleteReady = accountConfirm.trim().toLowerCase() === account.email.toLowerCase();

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <section className="overflow-hidden rounded-[2rem] border border-gray-200 bg-white shadow-sm">
        <div className="bg-gradient-to-br from-emerald-50 via-white to-sky-50 px-6 py-8 sm:px-8">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-4">
              <Avatar
                avatarUrl={account.avatar_url}
                displayName={account.display_name}
                email={account.email}
                sizeClassName="h-20 w-20"
                textClassName="text-xl"
              />
              <div>
                <p className="text-sm font-medium uppercase tracking-[0.18em] text-emerald-700">
                  Account
                </p>
                <h1 className="mt-2 text-3xl font-semibold text-gray-900">
                  {account.display_name || account.email.split("@")[0]}
                </h1>
                <p className="mt-1 text-sm text-gray-500">{account.email}</p>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:min-w-[18rem]">
              <div className="rounded-2xl border border-white/70 bg-white/80 p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-gray-400">Sessions</p>
                <p className="mt-2 text-2xl font-semibold text-gray-900">{account.active_session_count}</p>
              </div>
              <div className="rounded-2xl border border-white/70 bg-white/80 p-4">
                <p className="text-xs uppercase tracking-[0.14em] text-gray-400">Journeys</p>
                <p className="mt-2 text-2xl font-semibold text-gray-900">{account.journey_count}</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1.2fr,0.8fr]">
        <section className="rounded-3xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Profile</h2>
              <p className="mt-1 text-sm text-gray-500">
                Manage the name shown in the app and review your account details.
              </p>
            </div>
          </div>

          <div className="mt-6 space-y-5">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-gray-700">Display name</span>
              <input
                type="text"
                value={draftName}
                onChange={(event) => setDraftName(event.target.value)}
                maxLength={200}
                placeholder="Add a display name"
                className="w-full rounded-2xl border border-gray-200 px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-100"
              />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-2xl border border-gray-100 bg-gray-50 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.14em] text-gray-400">Email</p>
                <p className="mt-2 text-sm font-medium text-gray-800">{account.email}</p>
              </div>
              <div className="rounded-2xl border border-gray-100 bg-gray-50 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.14em] text-gray-400">Joined</p>
                <p className="mt-2 text-sm font-medium text-gray-800">{formatDateTime(account.created_at)}</p>
              </div>
            </div>

            <button
              type="button"
              onClick={() => void handleSaveProfile()}
              disabled={!nameChanged || savingProfile}
              className="inline-flex items-center justify-center rounded-2xl bg-gray-900 px-5 py-3 text-sm font-medium text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              {savingProfile ? "Saving..." : "Save profile"}
            </button>
          </div>
        </section>

        <div className="flex flex-col gap-6">
          <section className="rounded-3xl border border-gray-200 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-gray-900">Connected Google account</h2>
            <p className="mt-1 text-sm text-gray-500">
              Gmail scanning depends on this account staying connected with read access.
            </p>

            <div className="mt-5 space-y-3">
              <div className="rounded-2xl border border-gray-100 bg-gray-50 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.14em] text-gray-400">Mailbox</p>
                <p className="mt-2 text-sm font-medium text-gray-800">
                  {account.google_account_email || "Not connected"}
                </p>
              </div>
              <div className="rounded-2xl border border-gray-100 bg-gray-50 px-4 py-3">
                <p className="text-xs uppercase tracking-[0.14em] text-gray-400">Status</p>
                <p className="mt-2 text-sm font-medium text-gray-800">
                  {account.google_account_connected && account.gmail_scope_granted
                    ? "Connected and ready to scan"
                    : "Needs reconnect"}
                </p>
              </div>
              <button
                type="button"
                onClick={loginWithGoogle}
                className="inline-flex items-center justify-center rounded-2xl border border-gray-200 px-4 py-3 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
              >
                Reconnect Google
              </button>
            </div>
          </section>

          <section className="rounded-3xl border border-gray-200 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-gray-900">Current journey</h2>
            <p className="mt-1 text-sm text-gray-500">
              Delete the active journey when you want to clear only this workspace.
            </p>

            <div className="mt-5 rounded-2xl border border-gray-100 bg-gray-50 px-4 py-4">
              <p className="text-xs uppercase tracking-[0.14em] text-gray-400">Active journey</p>
              <p className="mt-2 text-sm font-medium text-gray-800">
                {activeJourney?.name || account.active_journey_name || "No active journey"}
              </p>
              <p className="mt-2 text-xs text-gray-500">
                {journeys.length <= 1
                  ? "Deleting the last journey creates a fresh empty replacement automatically."
                  : `You have ${journeys.length} journeys. Another one will become active after deletion.`}
              </p>
            </div>

            <div className="mt-5 space-y-3 rounded-2xl border border-amber-200 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-900">
                Type the current journey name to delete it.
              </p>
              <input
                type="text"
                value={journeyConfirm}
                onChange={(event) => setJourneyConfirm(event.target.value)}
                placeholder={activeJourney?.name || "Journey name"}
                className="w-full rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-amber-400 focus:ring-4 focus:ring-amber-100"
              />
              <button
                type="button"
                onClick={() => void handleDeleteJourney()}
                disabled={!journeyDeleteReady || deletingJourney}
                className="inline-flex items-center justify-center rounded-2xl bg-amber-500 px-4 py-3 text-sm font-medium text-white transition hover:bg-amber-600 disabled:cursor-not-allowed disabled:bg-amber-200"
              >
                {deletingJourney ? "Deleting journey..." : "Delete current journey"}
              </button>
            </div>
          </section>
        </div>
      </div>

      <section className="rounded-3xl border border-red-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-gray-900">Danger zone</h2>
        <p className="mt-1 text-sm text-gray-500">
          Deleting your account removes all journeys, applications, scanned emails, and sessions.
        </p>

        <div className="mt-5 space-y-3 rounded-2xl border border-red-200 bg-red-50 p-4">
          <p className="text-sm font-medium text-red-900">
            Type your email address to permanently delete this account.
          </p>
          <input
            type="text"
            value={accountConfirm}
            onChange={(event) => setAccountConfirm(event.target.value)}
            placeholder={account.email}
            className="w-full rounded-2xl border border-red-200 bg-white px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-red-400 focus:ring-4 focus:ring-red-100"
          />
          <div className="grid gap-3 sm:grid-cols-[1fr,auto] sm:items-center">
            <p className="text-xs text-red-700">
              This action cannot be undone. You will be signed out immediately.
            </p>
            <button
              type="button"
              onClick={() => void handleDeleteAccount()}
              disabled={!accountDeleteReady || deletingAccount}
              className="inline-flex items-center justify-center rounded-2xl bg-red-600 px-5 py-3 text-sm font-medium text-white transition hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-200"
            >
              {deletingAccount ? "Deleting account..." : "Delete account"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
