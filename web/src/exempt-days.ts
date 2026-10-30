export const EXEMPT_DAYS_STORAGE_KEY = "jira-tracking-web.exempt-days.v1";

/** Months kept besides the current one (the app shows current + 2 past months). */
const EXEMPT_MONTHS_TO_KEEP = 2;

/** Submitted exempt (Sick Leave/PTO) days keyed by month ("YYYY-MM"), values are "YYYY-MM-DD". */
export type ExemptDaysState = Record<string, string[]>;

export type ParseExemptDaysResult = {
  value: ExemptDaysState;
  corrupted: boolean;
};

const MONTH_KEY = /^\d{4}-\d{2}$/;
const DAY_KEY = /^\d{4}-\d{2}-\d{2}$/;

function monthKeyOf(date: string): string {
  return date.slice(0, 7);
}

function cutoffMonthKey(now: Date): string {
  const cutoff = new Date(now.getFullYear(), now.getMonth() - EXEMPT_MONTHS_TO_KEEP, 1);
  return `${cutoff.getFullYear()}-${String(cutoff.getMonth() + 1).padStart(2, "0")}`;
}

export function parseExemptDaysSafely(raw: string | null): ParseExemptDaysResult {
  if (!raw) {
    return { value: {}, corrupted: false };
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { value: {}, corrupted: true };
    }
    const value: ExemptDaysState = {};
    for (const [month, days] of Object.entries(parsed)) {
      if (
        !MONTH_KEY.test(month) ||
        !Array.isArray(days) ||
        !days.every((day) => typeof day === "string" && DAY_KEY.test(day))
      ) {
        return { value: {}, corrupted: true };
      }
      value[month] = days;
    }
    return { value, corrupted: false };
  } catch {
    return { value: {}, corrupted: true };
  }
}

export function isExemptDay(state: ExemptDaysState, date: string): boolean {
  return state[monthKeyOf(date)]?.includes(date) ?? false;
}

/** Drops months older than the current month minus two. Only called when writing. */
export function pruneExemptDays(state: ExemptDaysState, now: Date = new Date()): ExemptDaysState {
  const cutoff = cutoffMonthKey(now);
  return Object.fromEntries(Object.entries(state).filter(([month]) => month >= cutoff));
}

export function addExemptDays(
  state: ExemptDaysState,
  dates: string[],
  now: Date = new Date(),
): ExemptDaysState {
  const next: ExemptDaysState = { ...state };
  for (const date of dates) {
    const month = monthKeyOf(date);
    const days = next[month] ?? [];
    if (!days.includes(date)) {
      next[month] = [...days, date].sort();
    }
  }
  return pruneExemptDays(next, now);
}

export function removeExemptDay(
  state: ExemptDaysState,
  date: string,
  now: Date = new Date(),
): ExemptDaysState {
  const month = monthKeyOf(date);
  const remaining = (state[month] ?? []).filter((day) => day !== date);
  const next = { ...state };
  if (remaining.length > 0) {
    next[month] = remaining;
  } else {
    delete next[month];
  }
  return pruneExemptDays(next, now);
}
