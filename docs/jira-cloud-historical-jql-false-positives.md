# Jira Cloud historical JQL returns tickets that were never mine / never in progress

Written once, 2026-11-04, as a reference. It records the root cause only. It is not
meant to be kept in sync with the code.

## Symptom

After the Jira instance moved from on-premises (Server/Data Center) to Jira Cloud,
historical JQL for a given day started returning tickets that were not assigned to the
user and not in the tracked status on that day, and that had no activity on that day.

The app discovers a day's tickets with clauses of this shape:

```
assignee WAS IN (currentUser()) ON "<day>"
AND status WAS IN ("<tracked status>") ON "<day>"
```

(or the `DURING ("<day> 00:00", "<next day> 00:00")` variant), plus project and
resolution filters.

## Evidence from the exported sample

A manual CSV export (`Jira (3).csv`, 7 issues, taken 2026-11-04) of issues the query
returned for the current day:

| Issue | Status | Assignee | Last updated | Legit? |
|---|---|---|---|---|
| IPT-13929 | In Progress | the user | 2026-11-03 | Yes |
| IPT-12585 | Closed | unassigned | 2026-08-23 | No |
| IPT-13517 | Ready to Test | another person | 2026-08-23 | No |
| MWW-6958 | Ready to Test | another person | 2026-04-05 | No |
| MWW-3919 | On Hold | another person | 2026-03-30 | No |
| MWW-6101 | To Do | another person | 2026-03-30 | No |
| MWW-8479 | In Progress | another person | 2025-12-16 | No |

Observations:

- The six false positives were last updated between 1 and 11 months ago. Nothing in
  their recent history can make them "assigned to me" or "in progress" today.
- Both clauses (assignee and status) matched wrongly at once, for example on a Closed,
  unassigned ticket. So this is not a quirk of a single field.
- No issue in the sample has a Resolution or Resolved value, including the Closed one.
  The `resolution is EMPTY OR resolved >= day` clause therefore cannot exclude any of
  them. That is a workflow-configuration fact, separate from the bug, but it removes the
  last filter that would have hidden a closed ticket.
- The assignee IDs mix old 24-hex-character IDs (`631a2576...`) with the newer
  `712020:...` / `557058:...` account IDs, which shows these accounts and their history
  went through the migration.

## Root cause

`WAS`, `WAS IN`, `WAS NOT` with `ON` / `DURING` / `BEFORE` / `AFTER` are not evaluated
against the issue's current fields. Jira evaluates them against the issue's change
history, the `changeitem` records that also back the History tab. The history is
replayed to reconstruct "what was the value at time T". A correct answer requires the
history to be complete and consistent.

Migrating from on-premises to Cloud does not always produce that. Atlassian documents
the following defects, all on the history data and not on the query:

1. **Corrupted history on some migrated issues (JRACLOUD-84058).** Reported for Server
   to Cloud migrations after March 2024. Historical `WAS` / `WAS IN` searches with
   `ON`, `DURING`, `BEFORE`, `AFTER` can return extra issues or no issues. The
   description gives `assignee WAS currentUser() ...` and `status WAS closed DURING ...`
   as affected examples. The cause is stated as "corrupted data in the history of some
   issues". Only JQL is affected, because the search engine is the only consumer of this
   data. Marked Fixed, but the fix applies to data repair through Atlassian support
   and not necessarily to every tenant retroactively.
2. **Missing initial `changeitem` records (JRACLOUD-83057).** After migration, some
   issues lack the records that describe the field's initial value at creation.
   Without them, a query such as `status was not "To Do" ON <date before the issue was
   created>` wrongly matches, because the replay has no starting state. Closed as a
   duplicate, with no workaround stated.
3. **Cloud expects records that Data Center never wrote (MIG-1813).** In Cloud, setting
   a field during issue creation also writes `changeitem` rows. Data Center does not.
   The migration assistant copies the Data Center history as-is and does not backfill
   the missing rows. Confirmed only for Fix Version, but the report says other fields
   may be affected. Closed as "Timed out" and not fixed.
4. **Unreliable "was" results in general (JRACLOUD-70797, JRACLOUD-76991).** Older
   reports describe unreliable `status was` results, most consistently for imported
   issues, and extra issues returned when a value was set and then changed before the
   queried date, again attributed to corrupted history.
5. **Status IDs can change in a migration.** Community reports say status IDs differ
   after migration, which breaks history that refers to the old IDs. Queries by status
   name can then match history entries incorrectly. JRACLOUD-77518 recommends status ID
   instead of name for some `WAS` problems.

What these have in common is that the replayed history for an affected issue does not
reflect reality, so the engine concludes the issue matched the clause on that day.
That fits the sample above: old tickets, long idle, matching both an assignee and a
status "was" clause for today.

## What is and is not established

- Established: the symptom, the temporal context (started after the on-prem to Cloud
  migration), and that Atlassian documents history-data defects of this kind causing
  false positives for `WAS ... ON/DURING`, for `assignee` and `status` specifically.
- Not established: which of the defects above (1 to 3, or a combination) affects these
  six tickets. That can only be determined from each issue's stored history. The CSV
  export has no changelog, and the Jira UI does not show missing rows, only the rows
  that exist.
- To pin it down for a given ticket: compare the History tab and the REST changelog
  (`GET /rest/api/3/issue/<key>?expand=changelog`) against the ticket's real lifecycle.
  A missing creation entry for assignee or status, or entries with IDs or values that
  do not map to real statuses and users, identifies the defect. Atlassian support is the
  only party that can repair the underlying `changeitem` data (per JRACLOUD-84058).

## Properties that matter when reasoning about this

- It is a data problem on specific issues, not a JQL syntax or timezone problem. The
  same query is correct for issues with clean history, which is why the sample shows
  one legitimate result among the wrong ones.
- It is selective by issue and by field. Results cannot be trusted as a whole, and
  cannot be assumed wrong as a whole.
- The current field values on the issue (what the CSV and the issue view show) are
  correct. Only the reconstructed past is wrong. Anything that reads current values
  can be used to cross-check what the history-based query claims.
- The inclusion filter `resolution is EMPTY` does not help when tickets are closed
  without a resolution.

## Sources

- JRACLOUD-84058, Historical JQL searches return wrong results: https://jira.atlassian.com/browse/JRACLOUD-84058
- JRACLOUD-83057, Historical search returns too many issues after migration: https://jira.atlassian.com/browse/JRACLOUD-83057
- MIG-1813, Migrated issue history is missing fix-version changelog entries: https://jira.atlassian.com/browse/MIG-1813
- JRACLOUD-76991, Historical searches return additional issues: https://jira.atlassian.com/browse/JRACLOUD-76991
- JRACLOUD-70797, Unreliable "status was" results: https://jira.atlassian.com/browse/JRACLOUD-70797
- JRACLOUD-77518, WAS with AFTER/endOfDay ignoring the date (use status ID): https://jira.atlassian.com/browse/JRACLOUD-77518
- Community thread, status WAS ON returning far more results on Cloud than Server: https://community.atlassian.com/forums/Jira-questions/Difference-in-JQL-results-between-Server-and-Cloud/qaq-p/2359959
