import { describe, expect, it } from "vitest";
import {
  buildExportTextOutput,
  buildInitialExportDays,
  parseStoredActivities,
  serializeExportDayState,
  UserActivity,
} from "./export-report";
import { MonthPlan } from "./types";

function makePlan(): MonthPlan {
  return {
    month: "2026-05",
    timezone: "America/Argentina/Buenos_Aires",
    days: [
      {
        date: "2026-05-05",
        total_minutes: 480,
        entries: [
          {
            date: "2026-05-05",
            issue_key: "APP-1",
            issue_type: "Story",
            summary: "Continue dashboard work",
            status: "In Progress",
            minutes: 120,
            source: "logged",
            locked: false,
            removed: false,
          },
          {
            date: "2026-05-05",
            issue_key: "APP-2",
            issue_type: "Story",
            summary: "Ship landing page",
            status: "Done",
            minutes: 120,
            source: "logged",
            locked: false,
            removed: false,
          },
          {
            date: "2026-05-05",
            issue_key: "APP-3",
            issue_type: "Bug",
            summary: "Fix export edge case",
            status: "Done",
            minutes: 120,
            source: "logged",
            locked: false,
            removed: false,
          },
          {
            date: "2026-05-05",
            issue_key: "APP-4",
            issue_type: "Spike",
            summary: "Research alternatives",
            status: "Done",
            minutes: 120,
            source: "logged",
            locked: false,
            removed: false,
          },
        ],
      },
      {
        date: "2026-05-06",
        total_minutes: 480,
        entries: [
          {
            date: "2026-05-06",
            issue_key: "APP-1",
            issue_type: "Story",
            summary: "Continue dashboard work",
            status: "In Progress",
            minutes: 180,
            source: "logged",
            locked: false,
            removed: false,
          },
        ],
      },
    ],
  };
}

describe("buildInitialExportDays", () => {
  it("keeps the existing in-progress vs done classification and appends activities after done tickets", () => {
    const activities: UserActivity[] = [
      { id: "meetings", description: "Meetings", defaultEnabled: true },
      { id: "reviews", description: "Code reviews", defaultEnabled: false },
    ];

    const days = buildInitialExportDays(makePlan(), activities);
    const firstDay = days[0];

    expect(firstDay.folders.inProgress.itemIds.map((itemId) => firstDay.items[itemId].label)).toEqual(["APP-1: Continue dashboard work"]);
    expect(firstDay.folders.done.itemIds.map((itemId) => firstDay.items[itemId].label)).toEqual([
      "APP-2: Ship landing page",
      "APP-3: Fix export edge case",
      "Meetings",
      "Code reviews",
    ]);
    expect(firstDay.items[firstDay.folders.done.itemIds[2]].checked).toBe(true);
    expect(firstDay.items[firstDay.folders.done.itemIds[3]].checked).toBe(false);
  });

  it("merges persisted folder order and checked state without losing newly derived items", () => {
    const activities: UserActivity[] = [{ id: "meetings", description: "Meetings", defaultEnabled: true }];
    const initial = buildInitialExportDays(makePlan(), activities);
    const firstDay = initial[0];
    const doneIds = firstDay.folders.done.itemIds;

    const persisted = {
      version: 1 as const,
      days: {
        [firstDay.date]: {
          folders: {
            inProgress: { checked: false, itemIds: [...firstDay.folders.inProgress.itemIds] },
            done: { checked: true, itemIds: [doneIds[2], doneIds[0], doneIds[1]] },
          },
          items: {
            [doneIds[2]]: { checked: false },
          },
        },
      },
    };

    const merged = buildInitialExportDays(makePlan(), activities, persisted)[0];

    expect(merged.folders.inProgress.checked).toBe(false);
    expect(merged.folders.done.itemIds.map((itemId) => merged.items[itemId].label)).toEqual([
      "Meetings",
      "APP-2: Ship landing page",
      "APP-3: Fix export edge case",
    ]);
    expect(merged.items[merged.folders.done.itemIds[0]].checked).toBe(false);
  });
});

describe("buildExportTextOutput", () => {
  it("omits unchecked lines and omits folders with no effective checked children", () => {
    const [day] = buildInitialExportDays(makePlan(), [{ id: "meetings", description: "Meetings", defaultEnabled: true }]);
    const doneTicketId = day.folders.done.itemIds[0];
    const bugTicketId = day.folders.done.itemIds[1];
    const activityId = day.folders.done.itemIds[2];

    day.items[doneTicketId] = { ...day.items[doneTicketId], checked: false };
    day.items[bugTicketId] = { ...day.items[bugTicketId], checked: false };
    day.items[activityId] = { ...day.items[activityId], checked: false };

    const output = buildExportTextOutput(day);

    expect(output.plainText).toContain("In Progress:");
    expect(output.plainText).not.toContain("Done:");
    expect(output.html).not.toContain("APP-2: Ship landing page");
    expect(output.html).not.toContain("Meetings");
  });

  it("excludes checked children when their folder is unchecked", () => {
    const [day] = buildInitialExportDays(makePlan(), [{ id: "meetings", description: "Meetings", defaultEnabled: true }]);

    day.folders.done = { ...day.folders.done, checked: false };

    const output = buildExportTextOutput(day);

    expect(output.plainText).toContain("In Progress:");
    expect(output.plainText).not.toContain("Done:");
    expect(output.html).not.toContain("Meetings");
  });
});

describe("persistence helpers", () => {
  it("serializes day state and parses stored activities safely", () => {
    const [day] = buildInitialExportDays(makePlan(), [{ id: "sync", description: "Team sync", defaultEnabled: false }]);
    const serialized = serializeExportDayState([day]);
    const serializedDay = serialized.days[day.date];

    expect(serialized.version).toBe(1);
    expect(serializedDay).toBeDefined();
    expect(serializedDay?.folders.done?.itemIds).toEqual(day.folders.done.itemIds);
    expect(parseStoredActivities(JSON.stringify([{ id: "a1", description: "Meetings", defaultEnabled: true }]))).toEqual([
      { id: "a1", description: "Meetings", defaultEnabled: true },
    ]);
    expect(parseStoredActivities(JSON.stringify([{ nope: true }]))).toEqual([]);
  });
});
