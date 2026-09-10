# Recurring Monitoring — Results (Phase 4)

Closes the last gap in the pipeline: phases 1-3 all started from two spec files
someone had already fetched. `src/monitor.py` is the watch loop that fetches on
its own, remembers what it saw last time, and reports only what's new.

## How it works

A watchlist (`data/watchlist.json`) names the APIs to track and where to fetch
their current spec. Each check:

1. Fetches the current spec.
2. First time ever seeing this API: stores it as the baseline, nothing to
   compare yet.
3. Otherwise: diffs the current spec against the spec from the *last* check
   (not the all-time original) — so a recurring run reports what changed
   since the previous run, the way `git diff` between two commits does.
4. Writes a timestamped run record and alerts if anything BREAKING showed up.
5. Advances the baseline to the spec just fetched.

## The real test — and a false-positive it caught

To test this against something real rather than two files handed to it, I
seeded the "last checked" baseline with the same GitHub v2.1.0 spec used in
phases 1-2, then ran a real check against the actual, current
`api.github.com.json` fetched live from `github/rest-api-description`'s
`main` branch (561 paths at v2.1.0 → 813 paths today).

First run: **168 breaking changes**, dominated by `endpoint_removed` (74 of
them). That count was high enough to be suspicious on its own — the same
instinct that caught the `anyOf` bug in phase 2 — so a sample got checked by
hand before it went in a report. It turned out **29 of those 74** "removed"
endpoints weren't removed at all: GitHub splits its API description into
separate spec files per product (`api.github.com.json` for github.com,
`ghec.json` for GitHub Enterprise Cloud, plus one per GitHub Enterprise
Server version), and 29 endpoints had simply moved from the github.com file
to the Enterprise Cloud one somewhere between the old tag and today. Checked
by fetching `ghec.json` directly and confirming those exact paths are
present there, verbatim.

**Fixed properly, not just for this run:** `monitor.py` now accepts a list
of `sibling_spec_urls` per watchlist entry. Any `endpoint_removed` finding
whose path is found verbatim in a sibling spec gets reclassified as
`endpoint_moved` (NON-BREAKING, informational) instead of counting as a
real break. Re-ran after the fix: **139 breaking changes** — exactly 29
fewer, matching the reclassified count precisely.

**Spot-checked a real one, too:** `/orgs/{org}/projects` is in the
remaining 45 endpoint removals. A search confirms GitHub announced the
sunset of the Projects (classic) API in May 2024 — this one's a genuine,
documented deprecation, not another reorganization artifact.

## Result

| | |
|---|---|
| Total changes since the v2.1.0 baseline | 511 |
| Breaking | 139 |
| Non-breaking (incl. 326 new endpoints, 29 reclassified moves) | 372 |

Breaking breakdown: 45 endpoints genuinely removed, 72 response fields that
quietly disappeared, 12 parameters removed, 10 request-body fields newly
required.

## Honest caveat on this particular number

139 is real drift, but it's drift accumulated over however many years
separate the v2.1.0 tag from today — not what a real recurring monitor
would ever report in one alert. A monitor checking daily or weekly would
almost never see a provider reorganize their spec files *between two
consecutive checks*; that's a rare event this test only surfaced because
the seeded gap was artificially huge. The mechanism this proves out — roll
the baseline forward, diff against the last check, reconcile against
sibling specs — is the same either way; the *size* of this one number is a
property of the test setup, not a claim about normal operation.

## What's still open
- Only `ghec.json` is checked as a sibling; the GHES version-specific spec
  files (`ghes-3.x`) aren't, so an endpoint that moved there instead would
  still show as a false `endpoint_removed`.
- `monitor.py` doesn't yet call `patch_generator.py` automatically against a
  tracked customer repo when it finds something auto-patchable — the pieces
  exist, they're just not wired together yet.
- No actual schedule/cron and no hosting to run it on — the next piece
  needed is a place to run this on a timer, which is a hosting decision
  (identity/payment step), not engineering.

## Run it

```bash
python3 src/monitor.py check               # checks everything on the watchlist
python3 src/monitor.py check --name github  # checks just one entry
```
