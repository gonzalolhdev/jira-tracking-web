import { useEffect, useMemo, useRef, useState } from "react";
import {
  buildExportTextOutput,
  buildInitialExportDays,
  EXPORT_ACTIVITIES_STORAGE_KEY,
  EXPORT_DAY_STATE_STORAGE_KEY,
  ExportDayReport,
  ExportFolderKey,
  parsePersistedExportState,
  parsePersistedExportStateSafely,
  parseStoredActivities,
  parseStoredActivitiesSafely,
  serializeExportDayState,
  UserActivity,
} from "./export-report";
import {
  DayPlan,
  MonthPlan,
  StreamChunkEvent,
  StreamCompleteEvent,
  StreamDayReadyEvent,
  SubmitItemResult,
} from "./types";

const TARGET_MINUTES = 480;
const QUARTER_HOUR = 0.25;
const STEP_MINUTES = 15;
type AuthStatus = {
  auth_mode: string;
  session_exists: boolean;
  cookies: number;
};

type ActivityLogItem = {
  id: number;
  kind: "success" | "error" | "info";
  message: string;
  at: string;
};

function formatHours(minutes: number): string {
  const value = minutes / 60;
  const compact = Number.isInteger(value)
    ? value.toFixed(0)
    : value
        .toFixed(2)
        .replace(/\.0+$/, "")
        .replace(/(\.\d)0$/, "$1");
  return `${compact}h`;
}

function minutesToHmm(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h}:${String(m).padStart(2, "0")}`;
}

function hmmToMinutes(raw: string): number | null {
  const trimmed = raw.trim();
  // h:mm or h:m
  const colonMatch = /^(\d+):(\d{1,2})$/.exec(trimmed);
  if (colonMatch) {
    const h = parseInt(colonMatch[1], 10);
    const m = parseInt(colonMatch[2], 10);
    if (m >= 60) return null;
    const total = h * 60 + m;
    return Math.round(total / 15) * 15;
  }
  // Decimal compat: 1.25 or 1,25
  const decimalMatch = /^(\d+)[.,](\d+)$/.exec(trimmed);
  if (decimalMatch) {
    const h = parseFloat(`${decimalMatch[1]}.${decimalMatch[2]}`);
    if (!Number.isFinite(h)) return null;
    return Math.round((h * 60) / 15) * 15;
  }
  // Plain integer treated as whole hours
  const intMatch = /^\d+$/.exec(trimmed);
  if (intMatch) {
    return parseInt(trimmed, 10) * 60;
  }
  return null;
}

function formatDayHeading(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00`);
  const day = date.getDate();
  const suffix =
    day % 10 === 1 && day !== 11
      ? "st"
      : day % 10 === 2 && day !== 12
        ? "nd"
        : day % 10 === 3 && day !== 13
          ? "rd"
          : "th";
  const monthAndYear = date.toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });
  return `${monthAndYear.split(" ")[0]} ${day}${suffix} ${date.getFullYear()}`;
}

function balanceLabel(totalMinutes: number): string {
  const delta = TARGET_MINUTES - totalMinutes;
  if (delta === 0) {
    return "On target (8h)";
  }
  if (delta > 0) {
    return `Remaining ${formatHours(delta)}`;
  }
  return `Over by ${formatHours(Math.abs(delta))}`;
}

async function responseDetail(resp: Response): Promise<string> {
  const text = await resp.text();
  if (!text) {
    return `HTTP ${resp.status}`;
  }

  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      return parsed.detail;
    }
  } catch {
    // Non-JSON response; return raw text.
  }

  return text;
}

function requiresSsoLogin(status: AuthStatus | null): boolean {
  return Boolean(status && !status.session_exists);
}

function IssueTypeIcon({ type }: { type: string }) {
  const lower = type.toLowerCase();

  if (lower.includes("bug")) {
    return (
      <span className="issue-type-badge" style={{ color: "#f87171" }}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="7" fill="#ef4444" />
          <circle cx="8" cy="8" r="2.5" fill="white" />
        </svg>
        Bug
      </span>
    );
  }

  if (
    lower.includes("sub") ||
    lower.includes("subtask") ||
    lower.includes("child")
  ) {
    return (
      <span className="issue-type-badge" style={{ color: "#60a5fa" }}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
          <rect x="1" y="1" width="14" height="14" rx="3" fill="#3b82f6" />
          <path
            d="M5 8h4M7 6l2 2-2 2"
            stroke="white"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Subtask
      </span>
    );
  }

  // Default: Story
  return (
    <span className="issue-type-badge" style={{ color: "#4ade80" }}>
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
        <rect x="1" y="1" width="14" height="14" rx="3" fill="#22c55e" />
        <path
          d="M5 5h6M5 8h6M5 11h4"
          stroke="white"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
      {type || "Story"}
    </span>
  );
}

function dayLoggedMinutes(day: DayPlan): number {
  return day.entries
    .filter((e) => !e.removed && e.source === "logged")
    .reduce((acc, e) => acc + e.minutes, 0);
}

function loadStoredActivities(): UserActivity[] {
  if (typeof window === "undefined") {
    return [];
  }
  return parseStoredActivities(
    window.localStorage.getItem(EXPORT_ACTIVITIES_STORAGE_KEY),
  );
}

function loadStoredExportState() {
  if (typeof window === "undefined") {
    return null;
  }
  return parsePersistedExportState(
    window.localStorage.getItem(EXPORT_DAY_STATE_STORAGE_KEY),
  );
}

function moveItem(
  itemIds: string[],
  draggedId: string,
  targetId: string,
): string[] {
  const fromIndex = itemIds.indexOf(draggedId);
  const targetIndex = itemIds.indexOf(targetId);
  if (fromIndex === -1 || targetIndex === -1 || fromIndex === targetIndex) {
    return itemIds;
  }

  const next = [...itemIds];
  const [dragged] = next.splice(fromIndex, 1);
  next.splice(targetIndex, 0, dragged);
  return next;
}

function getMonthOptions(): { label: string; value: string }[] {
  const now = new Date();
  const options: { label: string; value: string }[] = [];
  for (let delta = 2; delta >= 1; delta--) {
    const d = new Date(now.getFullYear(), now.getMonth() - delta, 1);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    const label = d.toLocaleDateString("en-US", {
      month: "long",
      year: "numeric",
    });
    options.push({ label, value });
  }
  options.push({ label: "Current", value: "" });
  return options;
}

export default function App() {
  const [plan, setPlan] = useState<MonthPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingMonth, setSavingMonth] = useState(false);
  const [savingDayDate, setSavingDayDate] = useState<string | null>(null);
  const [refreshingDayDate, setRefreshingDayDate] = useState<string | null>(
    null,
  );
  const [daySubmitErrors, setDaySubmitErrors] = useState<
    Record<string, string>
  >({});
  const [dayRefreshErrors, setDayRefreshErrors] = useState<
    Record<string, string>
  >({});
  const [accordionOpen, setAccordionOpen] = useState<Record<string, boolean>>(
    {},
  );
  const [dayLoadingByDate, setDayLoadingByDate] = useState<
    Record<string, boolean>
  >({});
  const [authLoading, setAuthLoading] = useState(false);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [storageWarning, setStorageWarning] = useState<string | null>(null);
  const [activityDrawerOpen, setActivityDrawerOpen] = useState(false);
  const [activityLogs, setActivityLogs] = useState<ActivityLogItem[]>([]);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [exportAccordionOpen, setExportAccordionOpen] = useState<
    Record<string, boolean>
  >({});
  const [exportReports, setExportReports] = useState<ExportDayReport[]>([]);
  const [exportModalView, setExportModalView] = useState<
    "reports" | "activities"
  >("reports");
  const [previewDayDate, setPreviewDayDate] = useState<string | null>(null);
  const [copiedExportDayDate, setCopiedExportDayDate] = useState<string | null>(
    null,
  );
  const [userActivities, setUserActivities] = useState<UserActivity[]>(() =>
    loadStoredActivities(),
  );
  const [selectedMonth, setSelectedMonth] = useState<string>("");
  const [monthDropdownOpen, setMonthDropdownOpen] = useState(false);
  const nextActivityIdRef = useRef(1);
  const activeStreamRef = useRef<EventSource | null>(null);
  const draggedExportLineRef = useRef<{
    date: string;
    folderKey: ExportFolderKey;
    itemId: string;
  } | null>(null);
  const [exportDragDropHint, setExportDragDropHint] = useState<{
    date: string;
    folderKey: ExportFolderKey;
    beforeItemId: string | null;
  } | null>(null);

  useEffect(() => {
    void bootstrap();
    return () => {
      activeStreamRef.current?.close();
      activeStreamRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    let foundCorruption = false;

    const parsedActivities = parseStoredActivitiesSafely(
      window.localStorage.getItem(EXPORT_ACTIVITIES_STORAGE_KEY),
    );
    if (parsedActivities.corrupted) {
      window.localStorage.removeItem(EXPORT_ACTIVITIES_STORAGE_KEY);
      setUserActivities([]);
      foundCorruption = true;
    } else {
      setUserActivities(parsedActivities.value);
    }

    const parsedExportState = parsePersistedExportStateSafely(
      window.localStorage.getItem(EXPORT_DAY_STATE_STORAGE_KEY),
    );
    if (parsedExportState.corrupted) {
      window.localStorage.removeItem(EXPORT_DAY_STATE_STORAGE_KEY);
      setPreviewDayDate(null);
      foundCorruption = true;
    }

    if (foundCorruption) {
      setStorageWarning(
        "Saved export data was corrupted and has been cleared.",
      );
    }
  }, []);

  async function bootstrap() {
    setLoading(true);
    setError(null);

    const status = await loadAuthStatus();
    if (!status) {
      setLoading(false);
      return;
    }
    if (status && requiresSsoLogin(status)) {
      setPlan(null);
      setLoading(false);
      return;
    }

    await loadPlan();
  }

  function logActivity(
    entries: Array<{ kind: ActivityLogItem["kind"]; message: string }>,
  ) {
    if (!entries.length) return;
    const now = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    const normalized = entries.map((entry) => ({
      id: nextActivityIdRef.current++,
      kind: entry.kind,
      message: entry.message,
      at: now,
    }));
    setActivityLogs((current) => [...normalized, ...current]);
    if (entries.some((entry) => entry.kind === "error")) {
      setActivityDrawerOpen(true);
    }
  }

  function logSubmitResults(items: SubmitItemResult[]) {
    const entries = items.map((item) => ({
      kind: item.success ? ("success" as const) : ("error" as const),
      message: `${item.date} · ${item.issue_key} · ${formatHours(item.minutes)} · ${item.success ? "ok" : "error"} · ${item.message}`,
    }));
    logActivity(entries);
  }

  function reportError(message: string) {
    setError(message);
    logActivity([{ kind: "error", message }]);
  }

  async function loadAuthStatus(): Promise<AuthStatus | null> {
    try {
      const resp = await fetch("/api/auth/status");
      if (!resp.ok) {
        throw new Error(await responseDetail(resp));
      }
      const data = (await resp.json()) as AuthStatus;
      setAuthStatus(data);
      return data;
    } catch (err) {
      setAuthStatus(null);
      reportError(
        err instanceof Error ? err.message : "Auth status check failed",
      );
      return null;
    }
  }

  async function loginSso() {
    setAuthLoading(true);
    setError(null);
    try {
      const resp = await fetch("/api/auth/login-sso", {
        method: "POST",
      });
      if (!resp.ok) {
        throw new Error(await responseDetail(resp));
      }
      const status = await loadAuthStatus();
      if (status && !requiresSsoLogin(status)) {
        await loadPlan();
      }
    } catch (err) {
      reportError(err instanceof Error ? err.message : "SSO login failed");
    } finally {
      setAuthLoading(false);
    }
  }

  async function loadPlan(monthParam?: string) {
    const month = monthParam ?? selectedMonth;
    setLoading(true);
    setError(null);
    setDaySubmitErrors({});
    setDayLoadingByDate({});
    const streamOk = await loadPlanStreaming(month);
    if (!streamOk) {
      // Fallback to regular non-streaming fetch
      try {
        const url = month
          ? `/api/month/plan?month=${encodeURIComponent(month)}`
          : "/api/month/plan";
        const resp = await fetch(url);
        if (!resp.ok) {
          throw new Error(await responseDetail(resp));
        }
        const data = (await resp.json()) as MonthPlan;
        applyPlanData(data);
      } catch (err) {
        reportError(
          err instanceof Error ? err.message : "Failed to load month plan",
        );
      } finally {
        setLoading(false);
      }
    }
  }

  function applyPlanData(data: MonthPlan) {
    setPlan(data);
    const byFinalState: Record<string, boolean> = {};
    for (const day of data.days) {
      byFinalState[day.date] = dayLoggedMinutes(day) < TARGET_MINUTES;
    }
    setAccordionOpen(byFinalState);
    setDayLoadingByDate({});
  }

  function setDaysLoading(dayDates: string[], isLoading: boolean) {
    if (!dayDates.length) return;
    setDayLoadingByDate((current) => {
      const next = { ...current };
      for (const dayDate of dayDates) {
        if (isLoading) {
          next[dayDate] = true;
        } else {
          delete next[dayDate];
        }
      }
      return next;
    });
  }

  function isDayLoading(dayDate: string): boolean {
    return Boolean(dayLoadingByDate[dayDate]);
  }

  /** Returns true if streaming completed (or errored cleanly); false to trigger fallback. */
  function loadPlanStreaming(month?: string): Promise<boolean> {
    return new Promise((resolve) => {
      activeStreamRef.current?.close();
      activeStreamRef.current = null;

      let source: EventSource | null = null;
      const STREAM_TIMEOUT_MS = 120_000;
      let resolved = false;

      const finish = (ok: boolean) => {
        if (resolved) return;
        resolved = true;
        source?.close();
        if (activeStreamRef.current === source) {
          activeStreamRef.current = null;
        }
        clearTimeout(timer);
        if (!ok) {
          setDayLoadingByDate({});
        }
        resolve(ok);
      };

      const timer = setTimeout(() => {
        // Timed out — fall back to regular fetch
        finish(false);
      }, STREAM_TIMEOUT_MS);

      try {
        const streamUrl = month
          ? `/api/month/plan/stream?month=${encodeURIComponent(month)}`
          : "/api/month/plan/stream";
        source = new EventSource(streamUrl);
        activeStreamRef.current = source;
      } catch {
        finish(false);
        return;
      }

      source.addEventListener("chunk", (e: MessageEvent) => {
        try {
          const chunk = JSON.parse(e.data) as StreamChunkEvent;
          if (!Array.isArray(chunk.days) || chunk.days.length === 0) return;
          // Merge incoming days into the existing plan (or seed a new plan)
          setPlan((current) => {
            const existingByDate = new Map(
              current?.days.map((d) => [d.date, d]) ?? [],
            );
            for (const day of chunk.days) {
              existingByDate.set(day.date, day);
            }
            const sorted = [...existingByDate.values()].sort((a, b) =>
              a.date.localeCompare(b.date),
            );
            return {
              month: current?.month ?? "",
              timezone: current?.timezone ?? "",
              days: sorted,
            };
          });
          setDaysLoading(
            chunk.days.map((day) => day.date),
            true,
          );
          // During streaming chunks, keep newly arrived days closed by default.
          // Final open/close state is applied on the `complete` event.
          setAccordionOpen((prev) => {
            const updates: Record<string, boolean> = {};
            for (const day of chunk.days) {
              if (!(day.date in prev)) {
                updates[day.date] = false;
              }
            }
            return { ...prev, ...updates };
          });
          setLoading(false);
        } catch {
          // Ignore malformed chunks
        }
      });

      source.addEventListener("day-ready", (e: MessageEvent) => {
        try {
          const payload = JSON.parse(e.data) as StreamDayReadyEvent;
          if (!payload?.date) return;
          setDaysLoading([payload.date], false);
        } catch {
          // Ignore malformed ready payloads.
        }
      });

      source.addEventListener("complete", (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data) as StreamCompleteEvent;
          applyPlanData(data);
        } catch {
          // Ignore
        }
        setLoading(false);
        finish(true);
      });

      source.addEventListener("error", (e: MessageEvent) => {
        let detail = "Stream error";
        try {
          const parsed = JSON.parse(e.data) as { detail?: string };
          if (parsed.detail) detail = parsed.detail;
        } catch {
          // Not JSON error data
        }
        reportError(detail);
        setDayLoadingByDate({});
        setLoading(false);
        finish(true); // handled (don't trigger fallback for auth errors)
      });

      source.onerror = () => {
        // SSE connection-level error (not our custom event) — fall back
        finish(false);
      };
    });
  }

  function toggleAccordion(date: string) {
    setAccordionOpen((prev) => ({ ...prev, [date]: !prev[date] }));
  }

  function updateEntry(
    dayIndex: number,
    entryIndex: number,
    partial: Partial<DayPlan["entries"][number]>,
  ) {
    setPlan((current) => {
      if (!current) return current;
      const days = current.days.map((day, di) => {
        if (di !== dayIndex) return day;
        const entries = day.entries.map((entry, ei) =>
          ei === entryIndex ? { ...entry, ...partial } : entry,
        );
        const total = entries
          .filter((e) => !e.removed)
          .reduce((acc, e) => acc + e.minutes, 0);
        return { ...day, entries, total_minutes: total };
      });
      return { ...current, days };
    });
  }

  function resetEntryMinutes(dayIndex: number, entryIndex: number) {
    updateEntry(dayIndex, entryIndex, { minutes: 0 });
  }

  function resetDayMinutes(dayIndex: number) {
    setPlan((current) => {
      if (!current) return current;
      const days = current.days.map((day, di) => {
        if (di !== dayIndex) return day;
        const entries = day.entries.map((entry) => ({ ...entry, minutes: 0 }));
        return { ...day, entries, total_minutes: 0 };
      });
      return { ...current, days };
    });
  }

  async function rebalance() {
    if (!plan) return;
    setError(null);
    try {
      const resp = await fetch("/api/month/rebalance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(plan),
      });
      if (!resp.ok) {
        throw new Error(await responseDetail(resp));
      }
      const data = (await resp.json()) as MonthPlan;
      setPlan(data);
    } catch (err) {
      reportError(err instanceof Error ? err.message : "Failed to rebalance");
    }
  }

  async function submitMonth() {
    if (!plan) return;
    const pendingDays = plan.days.filter(
      (day) => dayLoggedMinutes(day) < TARGET_MINUTES,
    );
    if (pendingDays.length === 0) {
      logActivity([{ kind: "info", message: "No pending days to submit." }]);
      return;
    }

    setSavingMonth(true);
    setError(null);
    setDaySubmitErrors({});
    try {
      const resp = await fetch("/api/month/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          month: plan.month,
          timezone: plan.timezone,
          days: pendingDays,
        }),
      });
      if (!resp.ok) {
        throw new Error(await responseDetail(resp));
      }
      const data = (await resp.json()) as { results: SubmitItemResult[] };
      logSubmitResults(data.results);
      // Month submit succeeded — reload whole month to reflect fresh logged state.
      setSavingMonth(false);
      await loadPlan();
      return;
    } catch (err) {
      reportError(err instanceof Error ? err.message : "Submit failed");
    } finally {
      setSavingMonth(false);
    }
  }

  async function submitDay(dayIndex: number) {
    if (!plan) return;

    const day = plan.days[dayIndex];
    setSavingDayDate(day.date);
    setError(null);
    setDaySubmitErrors((current) => {
      const { [day.date]: _removed, ...rest } = current;
      return rest;
    });

    try {
      const resp = await fetch("/api/month/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          month: plan.month,
          timezone: plan.timezone,
          days: [day],
        }),
      });
      if (!resp.ok) {
        throw new Error(await responseDetail(resp));
      }
      const data = (await resp.json()) as { results: SubmitItemResult[] };
      logSubmitResults(data.results);
      setDaySubmitErrors((current) => {
        const { [day.date]: _removed, ...rest } = current;
        return rest;
      });
      // Submit succeeded — immediately refresh the day to show the newly logged data.
      setSavingDayDate(null);
      await refreshDay(day.date);
      return;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Day submit failed";
      reportError(message);
      setDaySubmitErrors((current) => ({ ...current, [day.date]: message }));
    } finally {
      setSavingDayDate(null);
    }
  }

  async function refreshDay(dayDate: string) {
    if (!plan) return;
    setDaysLoading([dayDate], true);
    setRefreshingDayDate(dayDate);
    setDayRefreshErrors((current) => {
      const { [dayDate]: _removed, ...rest } = current;
      return rest;
    });
    try {
      const resp = await fetch("/api/day/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: dayDate, timezone: plan.timezone }),
      });
      if (!resp.ok) {
        throw new Error(await responseDetail(resp));
      }
      const freshDay = (await resp.json()) as DayPlan;
      setPlan((current) => {
        if (!current) return current;
        const days = current.days.map((d) =>
          d.date === dayDate ? freshDay : d,
        );
        return { ...current, days };
      });
      setAccordionOpen((prev) => ({
        ...prev,
        [dayDate]: dayLoggedMinutes(freshDay) < TARGET_MINUTES,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Day refresh failed";
      logActivity([{ kind: "error", message }]);
      setDayRefreshErrors((current) => ({ ...current, [dayDate]: message }));
    } finally {
      setDaysLoading([dayDate], false);
      setRefreshingDayDate(null);
    }
  }

  async function logout() {
    setAuthLoading(true);
    setError(null);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
      setPlan(null);
      await loadAuthStatus();
    } catch {
      // ignore
    } finally {
      setAuthLoading(false);
    }
  }

  const dayStats = useMemo(() => {
    if (!plan) return { exact: 0, under: 0, over: 0 };
    let exact = 0;
    let under = 0;
    let over = 0;
    for (const day of plan.days) {
      if (day.total_minutes === TARGET_MINUTES) exact += 1;
      else if (day.total_minutes < TARGET_MINUTES) under += 1;
      else over += 1;
    }
    return { exact, under, over };
  }, [plan]);

  const exportSeedActivities = useMemo(
    () =>
      userActivities.filter(
        (activity) => activity.description.trim().length > 0,
      ),
    [userActivities],
  );

  useEffect(() => {
    if (!plan) {
      setExportReports([]);
      return;
    }

    setExportReports(
      buildInitialExportDays(
        plan,
        exportSeedActivities,
        loadStoredExportState(),
      ),
    );
  }, [plan, exportSeedActivities]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(
      EXPORT_ACTIVITIES_STORAGE_KEY,
      JSON.stringify(userActivities),
    );
  }, [userActivities]);

  useEffect(() => {
    if (typeof window === "undefined" || exportReports.length === 0) {
      return;
    }
    window.localStorage.setItem(
      EXPORT_DAY_STATE_STORAGE_KEY,
      JSON.stringify(serializeExportDayState(exportReports)),
    );
  }, [exportReports]);

  useEffect(() => {
    setExportAccordionOpen((current) => {
      if (exportReports.length === 0) {
        return {};
      }
      const openDate = Object.keys(current).find((date) => current[date]);
      if (openDate && exportReports.some((day) => day.date === openDate)) {
        return current;
      }
      return { [exportReports[0].date]: true };
    });
  }, [exportReports]);

  useEffect(() => {
    if (
      previewDayDate &&
      !exportReports.some((day) => day.date === previewDayDate)
    ) {
      setPreviewDayDate(null);
    }
  }, [previewDayDate, exportReports]);

  function openExportModal() {
    setExportModalView("reports");
    setPreviewDayDate(null);
    setExportAccordionOpen(
      exportReports[0] ? { [exportReports[0].date]: true } : {},
    );
    setExportModalOpen(true);
  }

  function closeExportModal() {
    setPreviewDayDate(null);
    setExportModalView("reports");
    setExportModalOpen(false);
  }

  function toggleExportAccordion(date: string) {
    setExportAccordionOpen((prev) => (prev[date] ? {} : { [date]: true }));
  }

  function updateExportReport(
    date: string,
    updater: (day: ExportDayReport) => ExportDayReport,
  ) {
    setExportReports((current) =>
      current.map((day) => (day.date === date ? updater(day) : day)),
    );
  }

  function updateFolderChecked(
    date: string,
    folderKey: ExportFolderKey,
    checked: boolean,
  ) {
    updateExportReport(date, (day) => ({
      ...day,
      folders: {
        ...day.folders,
        [folderKey]: {
          ...day.folders[folderKey],
          checked,
        },
      },
    }));
  }

  function updateLineChecked(date: string, itemId: string, checked: boolean) {
    updateExportReport(date, (day) => ({
      ...day,
      items: {
        ...day.items,
        [itemId]: {
          ...day.items[itemId],
          checked,
        },
      },
    }));
  }

  function moveExportLine(
    date: string,
    folderKey: ExportFolderKey,
    draggedId: string,
    targetId: string,
  ) {
    updateExportReport(date, (day) => ({
      ...day,
      folders: {
        ...day.folders,
        [folderKey]: {
          ...day.folders[folderKey],
          itemIds: moveItem(
            day.folders[folderKey].itemIds,
            draggedId,
            targetId,
          ),
        },
      },
    }));
  }

  function moveExportLineBetweenFolders(
    date: string,
    fromFolderKey: ExportFolderKey,
    toFolderKey: ExportFolderKey,
    draggedId: string,
    beforeItemId: string | null,
  ) {
    updateExportReport(date, (day) => {
      // Remove from source folder
      const fromItemIds = day.folders[fromFolderKey].itemIds.filter(
        (id) => id !== draggedId,
      );

      // Add to target folder
      const toItemIds = [...day.folders[toFolderKey].itemIds];
      const insertIndex =
        beforeItemId === null
          ? toItemIds.length
          : toItemIds.indexOf(beforeItemId);
      if (insertIndex >= 0) {
        toItemIds.splice(insertIndex, 0, draggedId);
      } else {
        toItemIds.push(draggedId);
      }

      return {
        ...day,
        folders: {
          ...day.folders,
          [fromFolderKey]: {
            ...day.folders[fromFolderKey],
            itemIds: fromItemIds,
          },
          [toFolderKey]: {
            ...day.folders[toFolderKey],
            itemIds: toItemIds,
          },
        },
      };
    });
  }

  function addUserActivity() {
    const id =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `activity-${Date.now()}`;
    setUserActivities((current) => [
      ...current,
      { id, description: "", defaultEnabled: true },
    ]);
  }

  function updateUserActivity(id: string, patch: Partial<UserActivity>) {
    setUserActivities((current) =>
      current.map((activity) =>
        activity.id === id ? { ...activity, ...patch } : activity,
      ),
    );
  }

  function removeUserActivity(id: string) {
    setUserActivities((current) =>
      current.filter((activity) => activity.id !== id),
    );
  }

  async function copyExportDay(date: string) {
    const day = exportReports.find((report) => report.date === date);
    if (!day) {
      return;
    }

    const output = buildExportTextOutput(day);

    try {
      if (typeof ClipboardItem !== "undefined") {
        const item = new ClipboardItem({
          "text/html": new Blob([output.html], { type: "text/html" }),
          "text/plain": new Blob([output.plainText], { type: "text/plain" }),
        });
        await navigator.clipboard.write([item]);
      } else {
        await navigator.clipboard.writeText(output.plainText);
      }
      setCopiedExportDayDate(date);
      setTimeout(() => {
        setCopiedExportDayDate((current) =>
          current === date ? null : current,
        );
      }, 1600);
      logActivity([
        { kind: "info", message: `${date} export copied to clipboard.` },
      ]);
    } catch {
      reportError("Copy failed. Your browser may block clipboard access.");
    }
  }

  const previewDay = previewDayDate
    ? (exportReports.find((day) => day.date === previewDayDate) ?? null)
    : null;

  if (!authStatus || requiresSsoLogin(authStatus)) {
    return (
      <main className="page gate-page">
        <section className="gate-card">
          <p className="eyebrow">Jira Time Tracker</p>
          <h1>Sign in to continue</h1>
          <p className="gate-copy">
            Login with your SSO session to access month planning and worklog
            submissions.
          </p>
          <button
            className="primary"
            onClick={() => void loginSso()}
            disabled={authLoading}
          >
            {authLoading ? "Opening SSO..." : "Login with SSO"}
          </button>
          {storageWarning && <p className="warning-small">{storageWarning}</p>}
          {error && <p className="error">{error}</p>}
        </section>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="topbar">
        <h1 className="topbar-title">Jira Time Tracker</h1>
        <div className="topbar-actions">
          <button
            className="secondary"
            onClick={() => setActivityDrawerOpen((open) => !open)}
          >
            {activityDrawerOpen
              ? "Close Activity"
              : `Activity (${activityLogs.length})`}
          </button>
          <button onClick={() => void loadPlan()} disabled={loading}>
            Reload Month
          </button>
          <button onClick={() => void rebalance()} disabled={!plan || loading}>
            Rebalance to 8h/day
          </button>
          <button onClick={() => openExportModal()} disabled={!plan || loading}>
            Export Logs
          </button>
          <button
            className="primary"
            onClick={() => void submitMonth()}
            disabled={!plan || savingMonth}
          >
            {savingMonth ? "Submitting..." : "Submit Month"}
          </button>
          <button
            className="danger-ghost"
            onClick={() => void logout()}
            disabled={authLoading}
          >
            Log out
          </button>
        </div>
      </header>

      {error && <p className="error">{error}</p>}
      {storageWarning && <p className="warning-small">{storageWarning}</p>}

      {plan && (
        <section className="stats">
          <div
            className="stat-card month-selector-card"
            style={{ "--stat-accent": "#4f8ef7" } as React.CSSProperties}
            onClick={() => setMonthDropdownOpen((v) => !v)}
            role="button"
            aria-haspopup="listbox"
            aria-expanded={monthDropdownOpen}
          >
            <span>Month</span>
            <strong>{plan.month}</strong>
            <span className="month-selector-caret" aria-hidden="true">
              {monthDropdownOpen ? "▲" : "▼"}
            </span>
            {monthDropdownOpen && (
              <ul
                className="month-dropdown"
                role="listbox"
                onClick={(e) => e.stopPropagation()}
              >
                {getMonthOptions().map((opt) => (
                  <li
                    key={opt.value}
                    role="option"
                    aria-selected={selectedMonth === opt.value}
                    className={selectedMonth === opt.value ? "selected" : ""}
                    onClick={() => {
                      setSelectedMonth(opt.value);
                      setMonthDropdownOpen(false);
                      setPlan(null);
                      void loadPlan(opt.value);
                    }}
                  >
                    {opt.label}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div
            className="stat-card"
            style={{ "--stat-accent": "#4ade80" } as React.CSSProperties}
          >
            <span>On target</span>
            <strong>{dayStats.exact}</strong>
          </div>
          <div
            className="stat-card"
            style={{ "--stat-accent": "#eab308" } as React.CSSProperties}
          >
            <span>Under 8h</span>
            <strong>{dayStats.under}</strong>
          </div>
          <div
            className="stat-card"
            style={{ "--stat-accent": "#f87171" } as React.CSSProperties}
          >
            <span>Over 8h</span>
            <strong>{dayStats.over}</strong>
          </div>
        </section>
      )}

      {loading && (
        <p className="loading">
          <span className="spinner" aria-hidden="true" />
          {plan ? "Loading remaining days..." : "Loading month plan..."}
        </p>
      )}

      {plan && (
        <section className="days-grid">
          {plan.days.map((day, dayIndex) => {
            const dayBalance = balanceLabel(day.total_minutes);
            const balanceClass =
              day.total_minutes === TARGET_MINUTES
                ? "balanced"
                : day.total_minutes < TARGET_MINUTES
                  ? "under"
                  : "over";
            const loggedMins = dayLoggedMinutes(day);
            const isFullyLogged = loggedMins >= TARGET_MINUTES;
            const dayIsLoading = isDayLoading(day.date);
            const isOpen = accordionOpen[day.date] ?? true;
            const isSubmitting = savingDayDate === day.date;
            const isRefreshing = refreshingDayDate === day.date;
            const isDayBusy =
              dayIsLoading || isSubmitting || isRefreshing || savingMonth;

            return (
              <article
                className={`day-card${isOpen ? "" : " day-card-closed"}${dayIsLoading ? " day-card-loading" : ""}`}
                key={day.date}
              >
                <header
                  className="day-header"
                  onClick={() => {
                    if (!dayIsLoading) {
                      toggleAccordion(day.date);
                    }
                  }}
                  style={{ cursor: dayIsLoading ? "default" : "pointer" }}
                >
                  <div className="day-header-left">
                    <span
                      className={`accordion-caret${isOpen ? " open" : ""}`}
                      aria-hidden="true"
                    >
                      ▶
                    </span>
                    <div>
                      <div className="day-title-row">
                        <h2>{formatDayHeading(day.date)}</h2>
                        {dayIsLoading && (
                          <span className="day-loading-indicator">
                            Loading...
                          </span>
                        )}
                        <span
                          className={`day-status-badge ${isFullyLogged ? "badge-logged" : "badge-pending"}`}
                        >
                          {isFullyLogged ? "logged" : "pending"}
                        </span>
                      </div>
                      <p className="day-subtitle">
                        {day.entries.length} records
                      </p>
                    </div>
                  </div>
                  <div
                    className="day-header-right"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      className="day-refresh-btn"
                      title="Refresh this day"
                      aria-label="Refresh day"
                      disabled={isDayBusy}
                      onClick={() => void refreshDay(day.date)}
                    >
                      {isRefreshing ? (
                        <span
                          className="spinner spinner--sm"
                          aria-hidden="true"
                        />
                      ) : (
                        <svg
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          aria-hidden="true"
                        >
                          <path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5" />
                          <path d="M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5" />
                        </svg>
                      )}
                    </button>
                    <div className={`day-summary ${balanceClass}`}>
                      <div className="day-summary-meta">
                        <strong>{formatHours(day.total_minutes)} total</strong>
                        <span>{dayBalance}</span>
                        {daySubmitErrors[day.date] && (
                          <p className="day-submit-error">
                            {daySubmitErrors[day.date]}
                          </p>
                        )}
                        {dayRefreshErrors[day.date] && (
                          <p className="day-submit-error">
                            {dayRefreshErrors[day.date]}
                          </p>
                        )}
                      </div>
                      <div className="day-summary-actions">
                        <button
                          className="ghost danger-ghost"
                          onClick={() => resetDayMinutes(dayIndex)}
                          disabled={isDayBusy}
                        >
                          Reset Day to 0
                        </button>
                        <button
                          className="secondary"
                          onClick={() => void submitDay(dayIndex)}
                          disabled={isDayBusy || !day.entries.length}
                        >
                          {isSubmitting ? (
                            <>
                              <span
                                className="spinner spinner--sm"
                                aria-hidden="true"
                              />{" "}
                              Submitting...
                            </>
                          ) : (
                            "Submit Day"
                          )}
                        </button>
                      </div>
                    </div>
                  </div>
                </header>

                {isOpen &&
                  (dayIsLoading ? (
                    <div className="day-loading">
                      <span className="spinner" aria-hidden="true" />
                      <span>Loading...</span>
                    </div>
                  ) : (
                    <div className="day-table-shell">
                      <table>
                        <thead>
                          <tr>
                            <th>Ticket</th>
                            <th>Type</th>
                            <th>Summary</th>
                            <th>Hours</th>
                            <th>Source</th>
                            <th>Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {day.entries.length === 0 && (
                            <tr>
                              <td colSpan={6} className="empty-row">
                                No records for this date.
                              </td>
                            </tr>
                          )}
                          {day.entries.map((entry, entryIndex) => (
                            <tr
                              key={`${entry.date}-${entry.issue_key}-${entryIndex}`}
                              className={[
                                entry.removed ? "removed" : "",
                                entry.source === "generated-secondary" &&
                                entry.minutes === 0
                                  ? "secondary-zero"
                                  : "",
                              ]
                                .filter(Boolean)
                                .join(" ")}
                            >
                              <td className="col-ticket">{entry.issue_key}</td>
                              <td>
                                <IssueTypeIcon type={entry.issue_type} />
                              </td>
                              <td className="col-summary">{entry.summary}</td>
                              <td>
                                <div className="hours-stepper">
                                  <button
                                    type="button"
                                    className="step-btn"
                                    disabled={
                                      isDayBusy ||
                                      entry.removed ||
                                      entry.minutes <= 0
                                    }
                                    onClick={() =>
                                      updateEntry(dayIndex, entryIndex, {
                                        minutes: Math.max(
                                          0,
                                          entry.minutes - STEP_MINUTES,
                                        ),
                                      })
                                    }
                                  >
                                    -
                                  </button>
                                  <input
                                    type="text"
                                    inputMode="text"
                                    className="hmm-input"
                                    value={minutesToHmm(entry.minutes)}
                                    disabled={isDayBusy || entry.removed}
                                    onChange={(e) => {
                                      const mins = hmmToMinutes(e.target.value);
                                      if (mins !== null) {
                                        updateEntry(dayIndex, entryIndex, {
                                          minutes: mins,
                                        });
                                      }
                                    }}
                                    onBlur={(e) => {
                                      const mins = hmmToMinutes(e.target.value);
                                      if (mins === null) {
                                        updateEntry(dayIndex, entryIndex, {
                                          minutes: entry.minutes,
                                        });
                                      }
                                    }}
                                  />
                                  <button
                                    type="button"
                                    className="step-btn"
                                    disabled={isDayBusy || entry.removed}
                                    onClick={() =>
                                      updateEntry(dayIndex, entryIndex, {
                                        minutes: entry.minutes + STEP_MINUTES,
                                      })
                                    }
                                  >
                                    +
                                  </button>
                                </div>
                              </td>
                              <td>
                                <span
                                  className={`pill${entry.source === "generated-secondary" && entry.minutes === 0 ? " pill-secondary-zero" : ""}`}
                                >
                                  {entry.source}
                                </span>
                              </td>
                              <td className="action-cell">
                                <button
                                  className="ghost"
                                  disabled={isDayBusy}
                                  onClick={() =>
                                    updateEntry(dayIndex, entryIndex, {
                                      removed: !entry.removed,
                                    })
                                  }
                                >
                                  {entry.removed ? "Restore" : "Remove"}
                                </button>
                                <button
                                  className="ghost reset-row-btn"
                                  disabled={isDayBusy}
                                  onClick={() =>
                                    resetEntryMinutes(dayIndex, entryIndex)
                                  }
                                >
                                  Set 0h
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))}
              </article>
            );
          })}
        </section>
      )}

      {exportModalOpen && (
        <button
          type="button"
          className="export-backdrop"
          aria-label="Close export modal"
          onClick={() => closeExportModal()}
        />
      )}

      <section
        className={`export-modal${exportModalOpen ? " open" : ""}`}
        aria-label="Daily export report"
        aria-hidden={!exportModalOpen}
      >
        <header className="export-modal-header">
          <div>
            <p className="activity-kicker">Export</p>
            <h2>
              {exportModalView === "activities"
                ? "Activity Presets"
                : "Logged Records Report"}
            </h2>
          </div>
          <div className="export-modal-header-actions">
            {exportModalView === "reports" ? (
              <button
                className="secondary"
                onClick={() => setExportModalView("activities")}
              >
                + Add Activities
              </button>
            ) : null}
            <button
              className="ghost"
              onClick={() => {
                if (exportModalView === "activities") {
                  setExportModalView("reports");
                  return;
                }
                closeExportModal();
              }}
            >
              {exportModalView === "activities" ? "Back" : "Close"}
            </button>
          </div>
        </header>

        {exportModalView === "reports" ? (
          <>
            <p className="export-modal-copy">
              Each day has rich text output ready to copy.
            </p>

            <div className="export-days">
              {exportReports.map((day) => {
                const isOpen = exportAccordionOpen[day.date] ?? false;
                return (
                  <article
                    className={`export-day${isOpen ? " open" : ""}`}
                    key={day.date}
                  >
                    <header
                      className="export-day-header"
                      onClick={() => toggleExportAccordion(day.date)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          toggleExportAccordion(day.date);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <div className="export-day-header-main">
                        <span
                          className={`accordion-caret${isOpen ? " open" : ""}`}
                          aria-hidden="true"
                        >
                          ▶
                        </span>
                        <h3>{day.heading}</h3>
                      </div>
                      <div
                        className="export-day-actions"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <button
                          type="button"
                          className="copy-day-btn preview-day-btn"
                          onClick={() => setPreviewDayDate(day.date)}
                        >
                          Preview
                        </button>
                        <button
                          type="button"
                          className={`copy-day-btn${copiedExportDayDate === day.date ? " copied" : ""}`}
                          onClick={() => {
                            void copyExportDay(day.date);
                          }}
                        >
                          {copiedExportDayDate === day.date
                            ? "Copied"
                            : "Copy Day"}
                        </button>
                      </div>
                    </header>
                    {isOpen && (
                      <div className="export-day-body">
                        {(["inProgress", "done"] as ExportFolderKey[]).map(
                          (folderKey) => {
                            const folder = day.folders[folderKey];
                            const checkedChildren = folder.itemIds.filter(
                              (itemId) => day.items[itemId]?.checked,
                            ).length;
                            return (
                              <section
                                className={`export-folder${folder.checked ? "" : " is-muted"}`}
                                key={folder.key}
                              >
                                <label
                                  className={`export-row export-folder-row${folder.checked ? "" : " is-muted"}`}
                                >
                                  <input
                                    type="checkbox"
                                    checked={folder.checked}
                                    onChange={(e) =>
                                      updateFolderChecked(
                                        day.date,
                                        folderKey,
                                        e.target.checked,
                                      )
                                    }
                                  />
                                  <span className="export-folder-label">
                                    {folder.label}
                                  </span>
                                  <span className="export-folder-count">
                                    {checkedChildren}/{folder.itemIds.length}
                                  </span>
                                </label>
                                <ul
                                  className="export-folder-items"
                                  onDragOver={(e) => {
                                    const dragged =
                                      draggedExportLineRef.current;
                                    if (!dragged || dragged.date !== day.date) {
                                      return;
                                    }
                                    e.preventDefault();
                                    e.dataTransfer.dropEffect = "move";
                                    if (folder.itemIds.length === 0) {
                                      setExportDragDropHint({
                                        date: day.date,
                                        folderKey,
                                        beforeItemId: null,
                                      });
                                    }
                                  }}
                                  onDragLeave={(e) => {
                                    if (
                                      e.target === e.currentTarget &&
                                      folder.itemIds.length === 0
                                    ) {
                                      setExportDragDropHint(null);
                                    }
                                  }}
                                  onDrop={(e) => {
                                    e.preventDefault();
                                    const dragged =
                                      draggedExportLineRef.current;
                                    if (
                                      !dragged ||
                                      dragged.date !== day.date ||
                                      dragged.itemId ===
                                        folder.itemIds[folder.itemIds.length - 1]
                                    ) {
                                      return;
                                    }
                                    if (folder.itemIds.length === 0) {
                                      moveExportLineBetweenFolders(
                                        day.date,
                                        dragged.folderKey,
                                        folderKey,
                                        dragged.itemId,
                                        null,
                                      );
                                    }
                                    draggedExportLineRef.current = null;
                                    setExportDragDropHint(null);
                                  }}
                                >
                                  {folder.itemIds.length === 0 ? (
                                    <li
                                      className={`export-folder-empty${exportDragDropHint?.folderKey === folderKey && exportDragDropHint?.date === day.date ? " is-drop-target" : ""}`}
                                    >
                                      No entries.
                                    </li>
                                  ) : (
                                    folder.itemIds
                                      .map((itemId, itemIndex) => {
                                        const item = day.items[itemId];
                                        if (!item) {
                                          return null;
                                        }
                                        const isMuted =
                                          !folder.checked || !item.checked;
                                        const isDropTarget =
                                          exportDragDropHint?.date ===
                                            day.date &&
                                          exportDragDropHint?.beforeItemId ===
                                            itemId &&
                                          exportDragDropHint?.folderKey ===
                                            folderKey;
                                        const isDragSource =
                                          draggedExportLineRef.current?.itemId ===
                                            itemId &&
                                          draggedExportLineRef.current?.folderKey ===
                                            folderKey;
                                        return (
                                          <li
                                            key={item.id}
                                            className={`export-item-shell${isMuted ? " is-muted" : ""}${isDropTarget ? " is-drop-target" : ""}${isDragSource ? " is-drag-source" : ""}`}
                                            draggable
                                            onDragStart={(e) => {
                                              draggedExportLineRef.current = {
                                                date: day.date,
                                                folderKey,
                                                itemId: item.id,
                                              };
                                              e.dataTransfer.effectAllowed =
                                                "move";
                                            }}
                                            onDragOver={(e) => {
                                              const dragged =
                                                draggedExportLineRef.current;
                                              if (
                                                !dragged ||
                                                dragged.date !== day.date ||
                                                dragged.itemId === item.id
                                              ) {
                                                return;
                                              }
                                              e.preventDefault();
                                              e.dataTransfer.dropEffect =
                                                "move";
                                              setExportDragDropHint({
                                                date: day.date,
                                                folderKey,
                                                beforeItemId: item.id,
                                              });
                                            }}
                                            onDrop={(e) => {
                                              e.preventDefault();
                                              const dragged =
                                                draggedExportLineRef.current;
                                              if (
                                                !dragged ||
                                                dragged.date !== day.date ||
                                                dragged.itemId === item.id
                                              ) {
                                                return;
                                              }
                                              if (
                                                dragged.folderKey === folderKey
                                              ) {
                                                moveExportLine(
                                                  day.date,
                                                  folderKey,
                                                  dragged.itemId,
                                                  item.id,
                                                );
                                              } else {
                                                moveExportLineBetweenFolders(
                                                  day.date,
                                                  dragged.folderKey,
                                                  folderKey,
                                                  dragged.itemId,
                                                  item.id,
                                                );
                                              }
                                              draggedExportLineRef.current =
                                                null;
                                              setExportDragDropHint(null);
                                            }}
                                            onDragEnd={() => {
                                              draggedExportLineRef.current =
                                                null;
                                              setExportDragDropHint(null);
                                            }}
                                          >
                                            <label
                                              className={`export-row export-item-row${isMuted ? " is-muted" : ""}`}
                                            >
                                              <span
                                                className="export-drag-handle"
                                                aria-hidden="true"
                                              >
                                                ⋮⋮
                                              </span>
                                              <input
                                                type="checkbox"
                                                checked={item.checked}
                                                onChange={(e) =>
                                                  updateLineChecked(
                                                    day.date,
                                                    item.id,
                                                    e.target.checked,
                                                  )
                                                }
                                              />
                                              <span className="export-item-label">
                                                {item.label}
                                              </span>
                                            </label>
                                          </li>
                                        );
                                      })
                                      .reduce(
                                        (acc, elem, idx) => {
                                          const dragged =
                                            draggedExportLineRef.current;
                                          if (
                                            dragged?.date === day.date &&
                                            dragged?.folderKey === folderKey &&
                                            folder.itemIds[idx] ===
                                              dragged.itemId
                                          ) {
                                            acc.push(
                                              <li
                                                key={`gap-${dragged.itemId}`}
                                                className="export-gap-placeholder"
                                                aria-hidden="true"
                                              />
                                            );
                                          }
                                          if (elem) acc.push(elem);
                                          return acc;
                                        },
                                        [] as React.ReactNode[],
                                      )
                                  )}
                                </ul>
                              </section>
                            );
                          },
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </>
        ) : (
          <div className="export-activities-view">
            <p className="export-modal-copy">
              These activities are stored in your browser and are added to each
              day's Done folder.
            </p>
            <div className="export-activities-head export-activities-grid">
              <span>Default</span>
              <span>Description</span>
              <span aria-hidden="true" />
            </div>
            <div className="export-activities-list">
              {userActivities.length === 0 ? (
                <p className="export-activities-empty">
                  No activities yet. Add reusable lines for meetings, reviews,
                  or any recurring work.
                </p>
              ) : (
                userActivities.map((activity) => (
                  <div
                    className="export-activities-grid export-activity-row"
                    key={activity.id}
                  >
                    <label className="export-activity-default">
                      <input
                        type="checkbox"
                        checked={activity.defaultEnabled}
                        onChange={(e) =>
                          updateUserActivity(activity.id, {
                            defaultEnabled: e.target.checked,
                          })
                        }
                      />
                    </label>
                    <input
                      type="text"
                      value={activity.description}
                      placeholder="Describe the activity"
                      onChange={(e) =>
                        updateUserActivity(activity.id, {
                          description: e.target.value,
                        })
                      }
                    />
                    <button
                      className="ghost danger-ghost"
                      onClick={() => removeUserActivity(activity.id)}
                    >
                      Remove
                    </button>
                  </div>
                ))
              )}
            </div>
            <div className="export-activities-actions">
              <button className="secondary" onClick={() => addUserActivity()}>
                + Add Activity Row
              </button>
            </div>
          </div>
        )}
      </section>

      {previewDay ? (
        <>
          <button
            type="button"
            className="export-preview-backdrop"
            aria-label="Close preview"
            onClick={() => setPreviewDayDate(null)}
          />
          <section className="export-preview-modal" aria-label="Export preview">
            <button
              type="button"
              className="export-preview-close"
              aria-label="Close preview"
              onClick={() => setPreviewDayDate(null)}
            >
              ×
            </button>
            <div
              className="export-preview-richtext"
              dangerouslySetInnerHTML={{
                __html: buildExportTextOutput(previewDay).html,
              }}
            />
          </section>
        </>
      ) : null}

      {activityDrawerOpen && (
        <button
          type="button"
          className="activity-backdrop"
          aria-label="Close activity drawer"
          onClick={() => setActivityDrawerOpen(false)}
        />
      )}

      <aside
        className={`activity-drawer${activityDrawerOpen ? " open" : ""}`}
        aria-label="Activity logs"
        aria-hidden={!activityDrawerOpen}
      >
        <header className="activity-drawer-header">
          <div>
            <p className="activity-kicker">Activity</p>
            <h2>Submission Logs</h2>
          </div>
          <button
            className="ghost"
            onClick={() => setActivityDrawerOpen(false)}
          >
            Close
          </button>
        </header>

        {activityLogs.length === 0 ? (
          <p className="activity-empty">
            No activity yet. Submission and error events will show here.
          </p>
        ) : (
          <ul className="activity-list">
            {activityLogs.map((item) => (
              <li
                key={item.id}
                className={`activity-item activity-${item.kind}`}
              >
                <div className="activity-item-head">
                  <span className="activity-kind">{item.kind}</span>
                  <time>{item.at}</time>
                </div>
                <p>{item.message}</p>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </main>
  );
}
