# Tremor

**Tremor catches API changes before they break your integration.**

Third-party APIs change their contracts constantly — a field quietly disappears from a
response, a parameter that used to be optional becomes required, an endpoint gets removed.
Most of the time nobody notices until an integration starts failing in production, or worse,
starts silently getting wrong data. Tremor watches an API's OpenAPI spec over time, detects
exactly what changed and whether it's breaking, and generates the code patch to fix it —
before a human has to read a changelog.

## Status: proof of concept, both diffing engines validated against real data

Built and tested against two real, historical versions of a real public API's official spec
(GitHub's own REST API, tags `v1.0.0` → `v2.1.0` — 428 → 561 endpoints). No synthetic data.

| Layer | Status | Result |
|---|---|---|
| Request-side diffing (`src/diff_engine.py`) | Done | 41 breaking changes found, 172 non-breaking |
| Response-side diffing (`src/response_diff.py`) | Done | 62 breaking changes found (after catching and fixing a real bug — see below) |
| Patch generation (automated, `src/patch_generator.py`) | Done for 2 of 6 request-side finding kinds | Given a real source file, finds the affected function on its own and generates the patch for required body fields and required query/header params — verified against blind tests, see `reports/PATCH_GENERATOR_RESULTS.md` |
| Recurring monitoring (`src/monitor.py`) | Watch loop built, tested against live data | Fetches a tracked API's current spec, diffs against the last check, alerts on breaking changes — see `reports/MONITOR_RESULTS.md`. Still needs an actual schedule/cron and somewhere to run it. |
| Hosting / schedule | Not started | Needs a hosting account (or similar) to run the watch loop on a timer |
| Billing | Not started | Needs a Stripe account when we get there |

**103 distinct, verified breaking changes found across both layers**, between two real
versions of one real, actively-maintained API. See `reports/` for full detail.

The patch generator (`src/patch_generator.py`) then took one of those findings and, run
blind against a source file it had never seen, found the affected function on its own,
generated the exact same fix that was previously built by hand, and the result was verified
by actually running it — see `reports/PATCH_GENERATOR_RESULTS.md`.

The recurring monitor (`src/monitor.py`) closes the loop: pointed at GitHub's real, current
API spec (fetched live, not a downloaded file), it found genuine drift since the v2.1.0
baseline — and, while checking the result before trusting it, caught its own false positive
(endpoints that had moved to a sibling spec file, not actually been removed) and got fixed to
tell the two apart automatically. See `reports/MONITOR_RESULTS.md`.

## How it works, right now

1. Feed it two versions of an API's OpenAPI spec (old, new).
2. `src/diff_engine.py` diffs the request side: removed endpoints, removed parameters,
   parameters that became required, request-body fields that became required.
3. `src/response_diff.py` diffs the response side: fields that quietly disappeared from a
   successful response, fields that changed type. This is the more dangerous class of
   change — the call still succeeds, so nothing errors, the calling code just silently gets
   `None` or the wrong shape.
4. Each finding is classified BREAKING or NON-BREAKING and written out as structured JSON
   (`reports/diff_report.json`, `reports/response_diff_report.json`).
5. `examples/example_patch.py` shows, by hand for one finding, what an automated patch would
   look like: the original integration code, and the same code auto-patched with the new
   required fields added to the signature and the outgoing payload, with a comment citing
   the spec change that caused the edit.

## Repo layout

```
tremor/
├── src/                     # the diffing engines + patch generator + monitor
│   ├── diff_engine.py       # request-side: params, required fields, removed endpoints
│   ├── response_diff.py     # response-side: removed/changed response fields
│   ├── patch_generator.py   # phase 3: finds affected functions in real source, patches them
│   └── monitor.py           # phase 4: watch loop -- fetch, diff vs. last check, alert
├── examples/
│   ├── example_patch.py         # one hand-built before/after patch, worked example (phase 1)
│   └── sample_integration.py    # stand-in customer file used to test patch_generator.py blind
├── reports/
│   ├── PROOF_OF_CONCEPT.md          # phase 1 write-up (request-side)
│   ├── RESPONSE_DIFF_RESULTS.md     # phase 2 write-up (response-side, incl. the bug fix)
│   ├── PATCH_GENERATOR_RESULTS.md   # phase 3 write-up (automated patch generation)
│   ├── MONITOR_RESULTS.md           # phase 4 write-up (recurring monitoring, live data)
│   ├── monitor_runs/                # timestamped records from real monitor.py runs
│   ├── diff_report.json             # full phase 1 output (213 changes)
│   └── response_diff_report.json    # full phase 2 output (62 breaking changes)
└── data/
    ├── old_spec.json        # GitHub REST API spec, tag v1.0.0
    ├── new_spec.json        # GitHub REST API spec, tag v2.1.0
    ├── watchlist.json       # APIs monitor.py tracks, and where to fetch each one's spec
    └── state/                # monitor.py's rolling "last seen" cache (gitignored)
```

## Run it

```bash
python3 src/diff_engine.py data/old_spec.json data/new_spec.json
python3 src/response_diff.py data/old_spec.json data/new_spec.json
python3 examples/example_patch.py
python3 src/patch_generator.py examples/sample_integration.py --write
python3 src/monitor.py check                # first run: establishes a baseline per watchlist entry
python3 src/monitor.py check                # any run after that: reports what's new since the last one
```

## A bug worth knowing about (and how it was caught)

The first response-diffing run reported 204 "removed" fields — high enough to be suspicious,
so it got checked by hand before being trusted. `GET /orgs/{org}/interaction-limits` was
flagged as losing all three of its fields; it turned out the new spec wraps that response in
an `anyOf` (a real object, or an empty one for orgs with no limit set), and the first version
of the resolver didn't look inside `anyOf`/`oneOf` branches — so every field in that shape
read as gone. Fixed, re-run (204 → 62), and a second, unrelated example verified by hand
against the raw spec to confirm the fix held. Full account in
`reports/RESPONSE_DIFF_RESULTS.md`.

## Honest scope — what's not done yet

- Only the primary 2xx response is compared; 4xx/5xx error-response shapes aren't diffed.
- Nested objects more than a few levels deep aren't fully walked.
- Patch generation is automated for two finding kinds so far: a request-body field becoming
  required, and a query/header parameter becoming required — together the most common
  breaking-change kinds in the phase 1 data. Other request-side kinds (removed
  endpoints/params, newly-required *path*/cookie params, param type changes) are detected
  and flagged with a precise comment but not yet auto-rewritten. Response-side findings are
  always flagged, not auto-rewritten, since the fix lives wherever the response is *read*,
  not at the call site itself — see `reports/PATCH_GENERATOR_RESULTS.md`.
- The watch loop (`src/monitor.py`) exists and has been run against live data, but there's no
  actual schedule/cron calling it yet, and nowhere to host it running continuously — the
  latter is a hosting decision, not engineering.
- Only one API's spec (plus one sibling, for reconciling moved-not-removed endpoints) is on
  the watchlist right now.
- No hosting, no domain, no billing yet — deliberately deferred until there's something
  worth putting in front of a real user. These three are identity/payment steps under EUEG,
  not engineering.

## Who's building this

Built solo, by Claude, for EUEG OÜ. The scope agreed with Tamer (EUEG's founder, not a
software engineer): 100% of the code and every engineering decision happen without his
input; the only steps that need him are the ones that require an identity or a payment
method — registering a domain, opening a hosting account, connecting Stripe — handed over
as exact steps only when actually needed, not upfront.
