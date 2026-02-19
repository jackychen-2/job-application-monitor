import { useState, useEffect, useCallback } from "react";
import type { PendingReviewEmail, Application } from "../types";
import {
  getPendingReviewEmails,
  linkEmail,
  dismissReview,
  listApplications,
} from "../api/client";

interface Props {
  onResolved: () => void;
}

export default function ReviewQueue({ onResolved }: Props) {
  const [emails, setEmails] = useState<PendingReviewEmail[]>([]);
  const [applications, setApplications] = useState<Application[]>([]);
  const [selectedApp, setSelectedApp] = useState<Record<number, number>>({});
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [emailsData, appsData] = await Promise.all([
        getPendingReviewEmails(),
        listApplications({ page_size: 100 }),
      ]);
      setEmails(emailsData);
      setApplications(appsData.items);
    } catch (err) {
      console.error("Failed to fetch review data:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleLink = async (emailId: number) => {
    const appId = selectedApp[emailId];
    if (!appId) return;
    try {
      await linkEmail(emailId, appId);
      setEmails((prev) => prev.filter((e) => e.id !== emailId));
      onResolved();
    } catch (err) {
      console.error("Failed to link email:", err);
    }
  };

  const handleDismiss = async (emailId: number) => {
    try {
      await dismissReview(emailId);
      setEmails((prev) => prev.filter((e) => e.id !== emailId));
    } catch (err) {
      console.error("Failed to dismiss:", err);
    }
  };

  if (loading) return null;
  if (emails.length === 0) return null;

  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg">⚠️</span>
        <h3 className="font-semibold text-amber-800">
          Emails Needing Review ({emails.length})
        </h3>
      </div>
      <p className="text-sm text-amber-700 mb-3">
        These emails matched a company with multiple applications. Please assign them to the correct application.
      </p>
      <div className="space-y-3">
        {emails.map((email) => (
          <div
            key={email.id}
            className="bg-white border border-amber-100 rounded-md p-3 flex flex-col sm:flex-row sm:items-center gap-2"
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-gray-900 truncate">
                {email.subject || "No subject"}
              </div>
              <div className="text-xs text-gray-500">
                From: {email.sender || "Unknown"} ·{" "}
                {email.email_date
                  ? new Date(email.email_date).toLocaleDateString()
                  : ""}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="text-sm border border-gray-300 rounded px-2 py-1 max-w-[200px]"
                value={selectedApp[email.id] || ""}
                onChange={(e) =>
                  setSelectedApp((prev) => ({
                    ...prev,
                    [email.id]: Number(e.target.value),
                  }))
                }
              >
                <option value="">Select application...</option>
                {applications.map((app) => (
                  <option key={app.id} value={app.id}>
                    {app.company} — {app.job_title || "Unknown role"}
                  </option>
                ))}
              </select>
              <button
                onClick={() => handleLink(email.id)}
                disabled={!selectedApp[email.id]}
                className="text-sm bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Link
              </button>
              <button
                onClick={() => handleDismiss(email.id)}
                className="text-sm text-gray-500 hover:text-gray-700 px-2 py-1"
                title="Dismiss (don't link)"
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
