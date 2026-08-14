import { DayPlan, MonthPlan } from "./types";

export const EXPORT_ACTIVITIES_STORAGE_KEY = "jira-tracking-web.export-activities.v1";
export const EXPORT_DAY_STATE_STORAGE_KEY = "jira-tracking-web.export-day-state.v1";

export type UserActivity = {
  id: string;
  description: string;
  defaultEnabled: boolean;
};

export type ExportFolderKey = "inProgress" | "done";

export type ExportLineKind = "ticket" | "activity";

export type ExportLine = {
  id: string;
  label: string;
  checked: boolean;
  kind: ExportLineKind;
};

export type ExportFolder = {
  key: ExportFolderKey;
  label: string;
  checked: boolean;
  itemIds: string[];
};

export type ExportDayReport = {
  date: string;
  heading: string;
  folders: Record<ExportFolderKey, ExportFolder>;
  items: Record<string, ExportLine>;
};

export type PersistedExportDayState = {
  folders: Partial<Record<ExportFolderKey, { checked: boolean; itemIds: string[] }>>;
  items: Record<string, { checked: boolean }>;
};

export type PersistedExportState = {
  version: 1;
  days: Record<string, PersistedExportDayState>;
};

export type ExportTextOutput = {
  html: string;
  plainText: string;
};

export type ParseStoredResult<T> = {
  value: T;
  corrupted: boolean;
};

type DerivedDayCollections = {
  inProgress: string[];
  done: string[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object";
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isValidFolderState(value: unknown): value is { checked: boolean; itemIds: string[] } {
  if (!isRecord(value)) {
    return false;
  }
  return typeof value.checked === "boolean" && isStringArray(value.itemIds);
}

function isValidPersistedDayState(value: unknown): value is PersistedExportDayState {
  if (!isRecord(value) || !isRecord(value.folders) || !isRecord(value.items)) {
    return false;
  }

  const inProgress = value.folders.inProgress;
  const done = value.folders.done;
  if (inProgress !== undefined && !isValidFolderState(inProgress)) {
    return false;
  }
  if (done !== undefined && !isValidFolderState(done)) {
    return false;
  }

  for (const item of Object.values(value.items)) {
    if (!isRecord(item) || typeof item.checked !== "boolean") {
      return false;
    }
  }

  return true;
}

function formatDayHeading(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00`);
  const day = date.getDate();
  const suffix = day % 10 === 1 && day !== 11 ? "st" : day % 10 === 2 && day !== 12 ? "nd" : day % 10 === 3 && day !== 13 ? "rd" : "th";
  const monthAndYear = date.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  return `${monthAndYear.split(" ")[0]} ${day}${suffix} ${date.getFullYear()}`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function issueTypeGroup(type: string): "bug" | "spike" | "storyOrSubtask" | "other" {
  const lower = type.toLowerCase();
  if (lower.includes("spike")) return "spike";
  if (lower.includes("bug")) return "bug";
  if (lower.includes("story") || lower.includes("sub") || lower.includes("subtask") || lower.includes("child")) {
    return "storyOrSubtask";
  }
  return "other";
}

function uniqueLoggedEntries(day: DayPlan): DayPlan["entries"] {
  const byKey = new Map<string, DayPlan["entries"][number]>();
  for (const entry of day.entries) {
    if (entry.removed || entry.source !== "logged" || entry.minutes <= 0) {
      continue;
    }
    if (!byKey.has(entry.issue_key)) {
      byKey.set(entry.issue_key, entry);
    }
  }
  return [...byKey.values()];
}

function toReportLabel(entry: DayPlan["entries"][number]): string {
  return `${entry.issue_key}: ${entry.summary}`;
}

function deriveDayCollections(plan: MonthPlan): Map<string, DerivedDayCollections> {
  const days = [...plan.days].sort((a, b) => a.date.localeCompare(b.date));
  const result = new Map<string, DerivedDayCollections>();

  for (let index = 0; index < days.length; index += 1) {
    const day = days[index];
    const currentEntries = uniqueLoggedEntries(day);
    const nextEntries = index < days.length - 1 ? uniqueLoggedEntries(days[index + 1]) : [];
    const nextKeys = new Set(nextEntries.map((entry) => entry.issue_key));
    const inProgress: string[] = [];
    const done: string[] = [];

    for (const entry of currentEntries) {
      const group = issueTypeGroup(entry.issue_type);
      if (group === "spike" || group === "other") {
        continue;
      }
      if (group === "bug") {
        done.push(toReportLabel(entry));
        continue;
      }
      if (nextKeys.has(entry.issue_key)) {
        inProgress.push(toReportLabel(entry));
      } else {
        done.push(toReportLabel(entry));
      }
    }

    result.set(day.date, { inProgress, done });
  }

  return result;
}

function makeTicketId(folderKey: ExportFolderKey, label: string): string {
  return `${folderKey}:ticket:${label}`;
}

function makeActivityId(activityId: string): string {
  return `done:activity:${activityId}`;
}

function mergeItemOrder(defaultOrder: string[], persistedOrder?: string[]): string[] {
  if (!persistedOrder?.length) {
    return defaultOrder;
  }

  const known = new Set(defaultOrder);
  const merged = persistedOrder.filter((itemId) => known.has(itemId));
  const mergedSet = new Set(merged);

  for (const itemId of defaultOrder) {
    if (!mergedSet.has(itemId)) {
      merged.push(itemId);
    }
  }

  return merged;
}

export function buildInitialExportDays(plan: MonthPlan, activities: UserActivity[], persisted?: PersistedExportState | null): ExportDayReport[] {
  const collectionsByDate = deriveDayCollections(plan);
  const sortedDays = [...plan.days].sort((a, b) => a.date.localeCompare(b.date));

  return sortedDays.map((day) => {
    const derived = collectionsByDate.get(day.date) ?? { inProgress: [], done: [] };
    const persistedDay = persisted?.days[day.date];
    const items: Record<string, ExportLine> = {};

    const inProgressItemIds = derived.inProgress.map((label) => {
      const id = makeTicketId("inProgress", label);
      items[id] = {
        id,
        label,
        checked: persistedDay?.items[id]?.checked ?? true,
        kind: "ticket",
      };
      return id;
    });

    const doneTicketIds = derived.done.map((label) => {
      const id = makeTicketId("done", label);
      items[id] = {
        id,
        label,
        checked: persistedDay?.items[id]?.checked ?? true,
        kind: "ticket",
      };
      return id;
    });

    const activityIds = activities.map((activity) => {
      const id = makeActivityId(activity.id);
      items[id] = {
        id,
        label: activity.description,
        checked: persistedDay?.items[id]?.checked ?? activity.defaultEnabled,
        kind: "activity",
      };
      return id;
    });

    const defaultInProgressOrder = inProgressItemIds;
    const defaultDoneOrder = [...doneTicketIds, ...activityIds];

    return {
      date: day.date,
      heading: formatDayHeading(day.date),
      folders: {
        inProgress: {
          key: "inProgress",
          label: "In Progress",
          checked: persistedDay?.folders.inProgress?.checked ?? true,
          itemIds: mergeItemOrder(defaultInProgressOrder, persistedDay?.folders.inProgress?.itemIds),
        },
        done: {
          key: "done",
          label: "Done",
          checked: persistedDay?.folders.done?.checked ?? true,
          itemIds: mergeItemOrder(defaultDoneOrder, persistedDay?.folders.done?.itemIds),
        },
      },
      items,
    };
  });
}

function getEffectiveFolderItems(day: ExportDayReport, folderKey: ExportFolderKey): ExportLine[] {
  const folder = day.folders[folderKey];
  if (!folder.checked) {
    return [];
  }

  return folder.itemIds
    .map((itemId) => day.items[itemId])
    .filter((item): item is ExportLine => Boolean(item))
    .filter((item) => item.checked);
}

export function buildExportTextOutput(day: ExportDayReport): ExportTextOutput {
  const sections = (["inProgress", "done"] as ExportFolderKey[])
    .map((folderKey) => {
      const folder = day.folders[folderKey];
      const items = getEffectiveFolderItems(day, folderKey);
      if (!folder.checked || items.length === 0) {
        return null;
      }

      return {
        label: folder.label,
        items,
      };
    })
    .filter((section): section is { label: string; items: ExportLine[] } => Boolean(section));

  if (sections.length === 0) {
    return { html: "<div></div>", plainText: "" };
  }

  const html = `<div>\n${sections
    .map((section) => {
      const listItems = section.items.map((item) => `    <li><p>${escapeHtml(item.label)}</p></li>`).join("\n");
      return `  <p>${escapeHtml(section.label)}:</p>\n  <ul>\n${listItems}\n  </ul>`;
    })
    .join("\n")}
</div>`;

  const plainText = sections.map((section) => `${section.label}:\n${section.items.map((item) => `- ${item.label}`).join("\n")}`).join("\n\n");

  return { html, plainText };
}

export function serializeExportDayState(days: ExportDayReport[]): PersistedExportState {
  const serializedDays = days.reduce<Record<string, PersistedExportDayState>>((acc, day) => {
    acc[day.date] = {
      folders: {
        inProgress: {
          checked: day.folders.inProgress.checked,
          itemIds: [...day.folders.inProgress.itemIds],
        },
        done: {
          checked: day.folders.done.checked,
          itemIds: [...day.folders.done.itemIds],
        },
      },
      items: Object.fromEntries(Object.values(day.items).map((item) => [item.id, { checked: item.checked }])),
    };
    return acc;
  }, {});

  return {
    version: 1,
    days: serializedDays,
  };
}

export function parsePersistedExportStateSafely(raw: string | null): ParseStoredResult<PersistedExportState | null> {
  if (!raw) {
    return { value: null, corrupted: false };
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed) || parsed.version !== 1 || !isRecord(parsed.days)) {
      return { value: null, corrupted: true };
    }

    for (const day of Object.values(parsed.days)) {
      if (!isValidPersistedDayState(day)) {
        return { value: null, corrupted: true };
      }
    }

    return { value: parsed as PersistedExportState, corrupted: false };
  } catch {
    return { value: null, corrupted: true };
  }
}

export function parsePersistedExportState(raw: string | null): PersistedExportState | null {
  return parsePersistedExportStateSafely(raw).value;
}

export function parseStoredActivitiesSafely(raw: string | null): ParseStoredResult<UserActivity[]> {
  if (!raw) {
    return { value: [], corrupted: false };
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return { value: [], corrupted: true };
    }

    const activities = parsed.flatMap((item) => {
      if (!item || typeof item !== "object") {
        return [];
      }
      const candidate = item as Partial<UserActivity>;
      if (typeof candidate.id !== "string" || typeof candidate.description !== "string" || typeof candidate.defaultEnabled !== "boolean") {
        return [];
      }
      return [{ id: candidate.id, description: candidate.description, defaultEnabled: candidate.defaultEnabled }];
    });

    if (activities.length !== parsed.length) {
      return { value: [], corrupted: true };
    }

    return { value: activities, corrupted: false };
  } catch {
    return { value: [], corrupted: true };
  }
}

export function parseStoredActivities(raw: string | null): UserActivity[] {
  return parseStoredActivitiesSafely(raw).value;
}
