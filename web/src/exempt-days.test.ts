import { describe, expect, it } from "vitest";
import {
  addExemptDays,
  isExemptDay,
  parseExemptDaysSafely,
  pruneExemptDays,
  removeExemptDay,
} from "./exempt-days";

const NOW = new Date(2026, 9, 30); // 2026-10-30

describe("exempt days storage", () => {
  it("adds days grouped by month and sorted", () => {
    const state = addExemptDays({}, ["2026-10-20", "2026-10-05", "2026-09-01"], NOW);
    expect(state).toEqual({ "2026-10": ["2026-10-05", "2026-10-20"], "2026-09": ["2026-09-01"] });
    expect(isExemptDay(state, "2026-10-05")).toBe(true);
    expect(isExemptDay(state, "2026-10-06")).toBe(false);
  });

  it("prunes months older than current minus two when writing", () => {
    const state = addExemptDays({ "2026-07": ["2026-07-01"], "2026-08": ["2026-08-03"] }, ["2026-10-01"], NOW);
    expect(Object.keys(state).sort()).toEqual(["2026-08", "2026-10"]);
  });

  it("prunes across a year boundary", () => {
    const now = new Date(2026, 0, 15);
    const state = pruneExemptDays({ "2025-10": ["2025-10-01"], "2025-11": ["2025-11-01"] }, now);
    expect(Object.keys(state)).toEqual(["2025-11"]);
  });

  it("removes a day and its empty month", () => {
    const state = removeExemptDay({ "2026-10": ["2026-10-05"] }, "2026-10-05", NOW);
    expect(state).toEqual({});
  });

  it("flags corrupted payloads", () => {
    expect(parseExemptDaysSafely("{nope").corrupted).toBe(true);
    expect(parseExemptDaysSafely('{"2026-10":["bad"]}').corrupted).toBe(true);
    expect(parseExemptDaysSafely(null)).toEqual({ value: {}, corrupted: false });
    expect(parseExemptDaysSafely('{"2026-10":["2026-10-05"]}').value).toEqual({ "2026-10": ["2026-10-05"] });
  });
});
