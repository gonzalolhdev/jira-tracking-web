export type DayPlanEntry = {
  date: string;
  issue_key: string;
  issue_type: string;
  summary: string;
  status: string;
  minutes: number;
  source: string;
  locked: boolean;
  removed: boolean;
  worklog_id?: number | null;
};

export type DayPlan = {
  date: string;
  entries: DayPlanEntry[];
  total_minutes: number;
};

export type MonthPlan = {
  month: string;
  timezone: string;
  days: DayPlan[];
};

export type SubmitItemResult = {
  date: string;
  issue_key: string;
  minutes: number;
  success: boolean;
  message: string;
};

export type SubmitResponse = {
  results: SubmitItemResult[];
};

export type StreamChunkEvent = {
  days: DayPlan[];
};

export type StreamDayReadyEvent = {
  date: string;
};

export type StreamCompleteEvent = MonthPlan;

export type ProductiveTimeEntry = {
  id: string;
  date: string;
  time: number;
  note: string;
  service_id: string | null;
};

export type ProductiveEntriesResponse = {
  available: boolean;
  entries: ProductiveTimeEntry[];
};
