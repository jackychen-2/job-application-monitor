import { useState, useRef, useEffect, useCallback } from 'react';
import { format, subDays, subWeeks, startOfDay } from 'date-fns';
import { DayPicker, DateRange } from 'react-day-picker';
import 'react-day-picker/style.css';
import { getScanStreamUrl, cancelScanStream, getScanRunning, getScanProgress, getLastScanResult } from '../api/client';
import type { ScanResult } from '../types';

interface Props {
  onScanComplete: (result: ScanResult) => void;
}

type PresetKey = 'last1day' | 'last3days' | 'lastweek' | null;

const PRESETS: { key: PresetKey; label: string; getDates: () => { from: Date; to: Date } }[] = [
  {
    key: 'last1day',
    label: 'Last 1 Day',
    getDates: () => ({ from: subDays(startOfDay(new Date()), 1), to: new Date() }),
  },
  {
    key: 'last3days',
    label: 'Last 3 Days',
    getDates: () => ({ from: subDays(startOfDay(new Date()), 3), to: new Date() }),
  },
  {
    key: 'lastweek',
    label: 'Last Week',
    getDates: () => ({ from: subWeeks(startOfDay(new Date()), 1), to: new Date() }),
  },
];

export default function ScanButton({ onScanComplete }: Props) {
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState<{
    processed: number;
    total: number;
    currentSubject: string;
  } | null>(null);
  const [selectedPreset, setSelectedPreset] = useState<PresetKey>(null);
  const [dateRange, setDateRange] = useState<DateRange | undefined>(undefined);
  const [showCalendar, setShowCalendar] = useState(false);
  
  const eventSourceRef = useRef<EventSource | null>(null);
  const scanInProgressRef = useRef(false);
  const calendarRef = useRef<HTMLDivElement>(null);

  // Close calendar when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (calendarRef.current && !calendarRef.current.contains(e.target as Node)) {
        setShowCalendar(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // On mount: check if a scan is already running (e.g. after page refresh)
  // If so, start polling progress until it completes
  useEffect(() => {
    let pollInterval: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;

    async function checkAndPoll() {
      try {
        const { running } = await getScanRunning();
        if (!running || cancelled) return;

        // A scan is running — show scanning UI and poll for progress
        scanInProgressRef.current = true;
        setScanning(true);

        pollInterval = setInterval(async () => {
          if (cancelled) {
            if (pollInterval) clearInterval(pollInterval);
            return;
          }
          try {
            const prog = await getScanProgress();
            if (prog.type === 'idle') {
              // Scan finished while we were polling
              if (pollInterval) clearInterval(pollInterval);
              scanInProgressRef.current = false;
              setScanning(false);
              setProgress(null);
              // Fetch final result and notify parent
              try {
                const result = await getLastScanResult();
                if (result) onScanComplete(result);
              } catch { /* ignore */ }
              return;
            }
            if (prog.type === 'progress') {
              setProgress({
                processed: prog.processed,
                total: prog.total,
                currentSubject: prog.current_subject || '',
              });
            }
          } catch {
            // API error — stop polling
            if (pollInterval) clearInterval(pollInterval);
            scanInProgressRef.current = false;
            setScanning(false);
            setProgress(null);
          }
        }, 1000);
      } catch {
        // getScanRunning failed — ignore
      }
    }

    checkAndPoll();

    return () => {
      cancelled = true;
      if (pollInterval) clearInterval(pollInterval);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const formatDate = (date: Date | undefined) => {
    if (!date) return '';
    return format(date, 'yyyy-MM-dd');
  };

  const formatDisplayDate = (date: Date | undefined) => {
    if (!date) return 'Select';
    return format(date, 'MMM dd, yyyy');
  };

  const [selectedCount, setSelectedCount] = useState(50);

  const EMAIL_COUNT_OPTIONS = [5, 10, 15, 20, 50, 75, 100, 200, 500];

  const handleScan = useCallback((options: {
    incremental?: boolean;
    since_date?: string;
    before_date?: string;
    max_emails?: number;
  }) => {
    if (scanInProgressRef.current) return;
    scanInProgressRef.current = true;
    setScanning(true);
    setError('');
    setProgress(null);

    const url = getScanStreamUrl({
      max_emails: options.max_emails ?? (options.incremental ? 100 : undefined),
      incremental: options.incremental,
      since_date: options.since_date,
      before_date: options.before_date,
    });

    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'progress') {
          setProgress({
            processed: data.processed,
            total: data.total,
            currentSubject: data.current_subject || '',
          });
        } else if (data.type === 'complete') {
          es.close();
          eventSourceRef.current = null;
          scanInProgressRef.current = false;
          setScanning(false);
          setProgress(null);
          onScanComplete(data.result);
        } else if (data.type === 'error') {
          es.close();
          eventSourceRef.current = null;
          scanInProgressRef.current = false;
          setScanning(false);
          setProgress(null);
          setError(data.message || 'Scan failed');
        }
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      if (scanInProgressRef.current) {
        // SSE connection dropped but scan may still be running on the backend.
        // Fall back to polling for completion instead of giving up.
        const pollInterval = setInterval(async () => {
          try {
            const prog = await getScanProgress();
            if (prog.type === 'idle') {
              // Scan finished while we were polling
              clearInterval(pollInterval);
              scanInProgressRef.current = false;
              setScanning(false);
              setProgress(null);
              // Fetch final result and notify parent
              try {
                const result = await getLastScanResult();
                if (result) onScanComplete(result);
              } catch { /* ignore */ }
              return;
            }
            if (prog.type === 'progress') {
              setProgress({
                processed: prog.processed,
                total: prog.total,
                currentSubject: prog.current_subject || '',
              });
            }
          } catch {
            // API error — stop polling and show error
            clearInterval(pollInterval);
            scanInProgressRef.current = false;
            setScanning(false);
            setProgress(null);
            setError('Connection lost during scan');
          }
        }, 1000);
      }
    };
  }, [onScanComplete]);

  const handleCancel = async () => {
    try {
      await cancelScanStream();
    } catch {
      // ignore
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    scanInProgressRef.current = false;
    setScanning(false);
    setProgress(null);
  };

  const handleScanRange = () => {
    if (!dateRange?.from) return;
    setSelectedPreset(null); // clear preset highlight if manually changed
    handleScan({
      since_date: formatDate(dateRange.from),
      before_date: dateRange.to ? formatDate(dateRange.to) : formatDate(new Date()),
    });
  };

  const handleScanCount = () => {
    handleScan({ max_emails: selectedCount });
  };

  const handleScanNew = () => {
    handleScan({ incremental: true });
  };

  const handlePresetScan = (preset: typeof PRESETS[number]) => {
    const dates = preset.getDates();
    setSelectedPreset(preset.key);
    setDateRange({ from: dates.from, to: dates.to });
    handleScan({
      since_date: formatDate(dates.from),
      before_date: formatDate(dates.to),
    });
  };

  // Scanning state UI
  if (scanning) {
    return (
      <div className="flex items-center gap-3 bg-white rounded-lg shadow px-4 py-2">
        <svg className="animate-spin h-5 w-5 text-indigo-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
        <span className="text-sm text-gray-700">
          {progress
            ? `Scanning: ${progress.processed}/${progress.total}`
            : 'Starting scan...'}
        </span>
        {progress?.currentSubject && (
          <span className="text-xs text-gray-400 truncate max-w-[200px]" title={progress.currentSubject}>
            {progress.currentSubject}
          </span>
        )}
        <button
          onClick={handleCancel}
          className="ml-2 px-3 py-1 text-xs font-medium text-white bg-red-500 rounded hover:bg-red-600 transition-colors"
        >
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {error && (
        <div className="text-sm text-red-600 bg-red-50 px-3 py-1 rounded">{error}</div>
      )}
      <div className="flex items-center gap-2 flex-wrap">
        {/* Preset buttons */}
        {PRESETS.map((preset) => (
          <button
            key={preset.key}
            onClick={() => handlePresetScan(preset)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
              selectedPreset === preset.key
                ? 'bg-indigo-100 border-indigo-300 text-indigo-700'
                : 'bg-white border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}
          >
            {preset.label}
          </button>
        ))}

        {/* Divider */}
        <div className="w-px h-6 bg-gray-300 mx-1" />

        {/* Date range picker */}
        <div className="relative" ref={calendarRef}>
          <button
            onClick={() => setShowCalendar(!showCalendar)}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md border border-gray-300 bg-white text-gray-600 hover:bg-gray-50 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <span>{formatDisplayDate(dateRange?.from)}</span>
            <span className="text-gray-400">→</span>
            <span>{formatDisplayDate(dateRange?.to)}</span>
          </button>

          {showCalendar && (
            <div className="absolute right-0 top-full mt-1 z-50 bg-white rounded-lg shadow-lg border border-gray-200 p-3">
              {/* Selected range display */}
              <div className="flex items-center justify-between mb-2 text-xs">
                <span className="text-gray-500">
                  {dateRange?.from ? format(dateRange.from, 'MMM dd, yyyy') : 'Start date'}
                  {' → '}
                  {dateRange?.to ? format(dateRange.to, 'MMM dd, yyyy') : 'End date'}
                </span>
                <button
                  onClick={() => { setDateRange(undefined); setSelectedPreset(null); }}
                  className="text-xs text-red-500 hover:text-red-700"
                >
                  Reset
                </button>
              </div>

              <DayPicker
                mode="range"
                selected={dateRange}
                onSelect={(range) => {
                  setDateRange(range);
                  setSelectedPreset(null);
                }}
                captionLayout="dropdown"
                fromYear={2020}
                toYear={new Date().getFullYear()}
                disabled={{ after: new Date() }}
                numberOfMonths={1}
                showOutsideDays
              />

              {/* Apply button */}
              <div className="flex justify-end mt-2 pt-2 border-t border-gray-100">
                <button
                  onClick={() => setShowCalendar(false)}
                  className="px-3 py-1 text-xs font-medium text-white bg-indigo-600 rounded hover:bg-indigo-700"
                >
                  Apply
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Scan Range button */}
        <button
          onClick={handleScanRange}
          disabled={!dateRange?.from}
          className="px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Scan Range
        </button>

        {/* Divider */}
        <div className="w-px h-6 bg-gray-300 mx-1" />

        {/* Email count dropdown + Scan button */}
        <select
          value={selectedCount}
          onChange={(e) => setSelectedCount(Number(e.target.value))}
          className="px-2 py-1.5 text-xs font-medium rounded-md border border-gray-300 bg-white text-gray-600 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        >
          {EMAIL_COUNT_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n} emails
            </option>
          ))}
        </select>
        <button
          onClick={handleScanCount}
          className="px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 rounded-md hover:bg-indigo-700 transition-colors"
        >
          Scan
        </button>

        {/* Divider */}
        <div className="w-px h-6 bg-gray-300 mx-1" />

        {/* Scan New (incremental) */}
        <button
          onClick={handleScanNew}
          className="px-3 py-1.5 text-xs font-medium text-white bg-emerald-600 rounded-md hover:bg-emerald-700 transition-colors"
        >
          Scan New
        </button>
      </div>
    </div>
  );
}
