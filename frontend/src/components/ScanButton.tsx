import { useState, useEffect, useRef } from "react";
import { triggerScan, getScanRunning, getLastScanResult, cancelScan } from "../api/client";
import type { ScanResult } from "../types";

interface Props {
  onScanComplete: (result: ScanResult) => void;
}

const EMAIL_OPTIONS = [
  { label: "Latest 5", value: 5 },
  { label: "Latest 10", value: 10 },
  { label: "Latest 15", value: 15 },
  { label: "Latest 20", value: 20 },
  { label: "Latest 50", value: 50 },
  { label: "Latest 100", value: 100 },
  { label: "Latest 200", value: 200 },
  { label: "Latest 500", value: 500 },
];

export default function ScanButton({ onScanComplete }: Props) {
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [maxEmails, setMaxEmails] = useState(5);
  const pollIntervalRef = useRef<number | null>(null);

  // Check if scan is already running on mount
  useEffect(() => {
    checkScanStatus();
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);

  const checkScanStatus = async () => {
    try {
      const { running } = await getScanRunning();
      if (running) {
        setScanning(true);
        startPolling();
      }
    } catch (err) {
      console.error("Failed to check scan status:", err);
    }
  };

  const startPolling = () => {
    if (pollIntervalRef.current) return;
    
    pollIntervalRef.current = window.setInterval(async () => {
      try {
        const { running } = await getScanRunning();
        if (!running) {
          stopPolling();
          // Get the result
          const result = await getLastScanResult();
          if (result) {
            onScanComplete(result);
          }
          setScanning(false);
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 2000); // Poll every 2 seconds
  };

  const stopPolling = () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  };

  const handleScan = async () => {
    setScanning(true);
    setError(null);
    try {
      await triggerScan({ max_emails: maxEmails });
      startPolling();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan failed");
      setScanning(false);
    }
  };

  const handleCancel = async () => {
    try {
      await cancelScan();
    } catch (err) {
      console.error("Failed to cancel scan:", err);
    }
  };

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        {/* Main scan button */}
        <button
          onClick={handleScan}
          disabled={scanning}
          className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {scanning ? (
            <>
              <svg
                className="animate-spin h-4 w-4"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
              Scanning...
            </>
          ) : (
            <>
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
              </svg>
              Scan Emails
            </>
          )}
        </button>

        {/* Cancel button (shown when scanning) */}
        {scanning && (
          <button
            onClick={handleCancel}
            className="inline-flex items-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-red-500 transition-colors"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
            Cancel
          </button>
        )}

        {/* Count selector */}
        <select
          value={maxEmails}
          onChange={(e) => setMaxEmails(Number(e.target.value))}
          disabled={scanning}
          className="rounded-md border border-gray-300 bg-white py-2 px-2 text-sm text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          title="Number of latest emails to scan"
        >
          {EMAIL_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {error && <span className="text-sm text-red-600 mt-1 block">{error}</span>}
    </div>
  );
}
