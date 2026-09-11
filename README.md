# Tremor

**Tremor catches API changes before they break your integration.**

Third-party APIs change their contracts constantly — a field quietly disappears from a
response, a parameter that used to be optional becomes required, an endpoint gets removed.
Most of the time nobody notices until an integration starts failing in production, or worse,
starts silently getting wrong data. Tremor watches an API's OpenAPI spec over time, detects
exactly what changed and whether it's breaking, and generates the code patch to fix it —
before a human has to read a changelog.

## Status: working proof of concept with a repeatable benchmark

Built and tested against two real, historical versions of a real public API's official spec
(GitHub's own REST API, tags `v1.0.0` → `v2.1.0` — 428 → 561 endpoints). No synthetic data.

| Layer | Status | Result |
|---|---|---|
| Request-side diffing (`src/diff_engine.py`) | Done | 41 breaking changes found, 172 non-breaking |
| Response-side diffing (`src/response_diff.py`) | Done | 62 breaking changes found (after catching and fixing a real bug — see below) |
| Patch generation (automated, `src/patch_generator.py`) | Done for 2 of 6 request-side finding kinds | Given a real source file, finds the affected function on its own and generates the patch for required body fields and required query/header params — verified against blind tests, see `reports/PATCH_GENERATOR_RESULTS.md` |
| Recurring monitoring (`src/monitor.py`) | Watch loop built, tested against live data | Fetches a tracked API's current spec, diffs against the last check, alerts on breaking changes — see `reports/MONITOR_RESULTS.md`. |
| Monitor → patch generator wiring | Done | A live check now calls the patch generator directly on any watched files, with no manual step in between — see `reports/PIPELINE_WIRING_RESULTS.md` |
| Real, applyable patches | Done | Every auto-patch is also emitted as a standard `git apply`-compatible `.patch` file, verified with an actual `git apply` in an isolated worktree, not just asserted — see `reports/GIT_APPLY_RESULTS.md` |
| Detection benchmark (`benchmarks/run_benchmark.py`) | 27/27 labeled cases pass | 100% precision and recall within the explicitly modeled scope; every reviewed historical GitHub finding is retained — see `reports/BENCHMARK_RESULTS.md` |
| Schedule (`.github/workflows/monitor.yaml`) | Live on GitHub Actions | The first manual production run completed successfully and committed its updated state/findings back to the repo — see the repository's Actions tab. |
| Billing | Not started | Needs a Stripe account when we get there |

**103 distinct, verified breaking changes found across both layers**, between two real
versions of one real, actively-maintained API. See `reports/` for full detail.

The patch generator (`src/patch_generator.py`) then took one of those findings and, run
blind against a source file it had never seen, found the affected function on its own,
generated the exact same fix that was previously built by hand, and the result was verified
by actually running it — see `reports/PATCH_GENERATOR_RESULTS.md`.

The recurring monitor (`src/monitor.py`) closes the detection loop: pointed at GitHub's real,
current API spec (fetched live, not a downloaded file), it found genuine drift since the
v2.1.0 baseline — and, while checking the result before trusting it, caught its own false
positive (endpoints that had moved to a sibling spec file, not actually been removed) and got
fixed to tell the two apart automatically. See `reports/MONITOR_RESULTS.md`.

Then the last gap: the monitor and patch generator weren't actually connected — a live check
still needed a person to run the patch generator by hand afterward. They're wired together
now; a check runs the patch generator directly against any watched files, in-memory, no
manual hand-off. See `reports/PIPELINE_WIRING_RESULTS.md`.

What came out the other end was still a full replacement file, not something applyable —
fixed next: every auto-patch is now also a standard `git apply`-compatible `.patch` file,
proven not just asserted, by actually running `git apply` in an isolated worktree and
recompiling the result. See `reports/GIT_APPLY_RESULTS.md`.

Last: `.github/workflows/monitor.yaml` runs the watch loop daily on GitHub Actions' free tier
and commits the updated baseline back to the repo — zero cost, no hosting account needed,
made sustainable by compressing the rolling state cache about 24x. Its first manual run on
GitHub's infrastructure completed successfully.

`benchmarks/run_benchmark.py` now provides the measurable quality gate: 27 isolated,
labeled contract-change cases plus a regression against the historical GitHub API pair.
The benchmark runs on every push and pull request through `.github/workflows/benchmark.yaml`.

## How it works, right now

1. Feed it two versions of an API's OpenAPI spec (old, new).
2. `src/diff_engine.py` diffs the request side: removed endpoints, removed parameters,
   parameters that became required, request-body fields that became required. It supports
   path-level parameters, local `$ref` pointers, and safe `allOf`/`anyOf`/`oneOf` required-field
   semantics.
3. `src/response_diff.py` diffs the response side: fields that quietly disappeared or
   changed type in successful and matching documented error responses. It follows nested
   objects, arrays, local references, and composed schemas with cycle/depth guards. Successful
   response drift is especially dangerous — the call still succeeds, so nothing errors, the
   calling code just silently gets `None` or the wrong shape.
4. Each finding is classified BREAKING or NON-BREAKING and written out as structured JSON
   (`reports/diff_report.json`, `reports/response_diff_report.json`).
5. `examples/example_patch.py` shows, by hand for one finding, what an automated patch would
   look like: the original integration code, and the same code auto-patched with the new
   required fields added to the signature and the outgoing payload, with a comment citing
   the spec change that caused the edit.

## Repo layout

```
tremor/
├── .github/workflows/
│   ├── monitor.yaml         # phase 7: runs the watch loop daily on GitHub Actions, free tier
│   └── benchmark.yaml       # quality gate on every push and pull request
├── benchmarks/
│   └── run_benchmark.py     # labeled precision/recall + historical regression suite
├── src/                     # the diffing engines + patch generator + monitor
│   ├── diff_engine.py       # request-side: params, required fields, removed endpoints
│   ├── response_diff.py     # response-side: removed/changed response fields
│   ├── patch_generator.py   # phase 3/6: finds affected functions, patches them, emits a real .patch
│   └── monitor.py           # phase 4/5: watch loop -- fetch, diff vs. last check, alert, auto-patch
├── examples/
│   ├── example_patch.py         # one hand-built before/after patch, worked example (phase 1)
│   └── sample_integration.py    # stand-in customer file used to test patch_generator.py blind
├── reports/
│   ├── PROOF_OF_CONCEPT.md          # phase 1 write-up (request-side)
│   ├── RESPONSE_DIFF_RESULTS.md     # phase 2 write-up (response-side, incl. the bug fix)
│   ├── PATCH_GENERATOR_RESULTS.md   # phase 3 write-up (automated patch generation)
│   ├── MONITOR_RESULTS.md           # phase 4 write-up (recurring monitoring, live data)
│   ├── PIPELINE_WIRING_RESULTS.md   # phase 5 write-up (monitor -> patch generator, wired)
│   ├── GIT_APPLY_RESULTS.md         # phase 6 write-up (real, git-apply-verified .patch files)
│   ├── HOSTING_RESULTS.md           # phase 7 write-up (free, scheduled, zero-cost hosting)
│   ├── BENCHMARK_RESULTS.md         # reproducible detection-quality evidence
│   ├── monitor_runs/                # timestamped records from real monitor.py runs
│   │   └── patches/                     # patches monitor.py generated automatically (.py copy + .patch)
│   ├── diff_report.json             # full phase 1 output (213 changes)
│   └── response_diff_report.json    # full phase 2 output (62 breaking changes)
└── data/
    ├── old_spec.json        # GitHub REST API spec, tag v1.0.0
    ├── new_spec.json        # GitHub REST API spec, tag v2.1.0
    ├── watchlist.json       # APIs monitor.py tracks, and where to fetch each one's spec
    └── state/                # monitor.py's rolling "last seen" cache, gzip-compressed (~24x smaller)
                               # and committed on purpose -- the scheduled job's durable baseline
```

## Run it

```bash
python3 src/diff_engine.py data/old_spec.json data/new_spec.json
python3 src/response_diff.py data/old_spec.json data/new_spec.json
python3 examples/example_patch.py
python3 src/patch_generator.py examples/sample_integration.py --write
python3 src/patch_generator.py examples/sample_integration.py --patch-file /tmp/out.patch && git apply --check /tmp/out.patch
python3 src/monitor.py check                # first run: establishes a baseline per watchlist entry
python3 src/monitor.py check                # any run after that: reports what's new -- auto-patches
                                             # any watched_files the new findings affect, and writes
                                             # a verified, git-apply-able .patch alongside each one
python3 benchmarks/run_benchmark.py         # labeled quality gate + historical regression
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

- The primary 2xx response and matching explicit 4xx/5xx response schemas are compared;
  wildcard/default responses and changes to the set of possible status codes are not yet
  classified.
- Nested response objects are walked to a guarded depth of eight; extremely deep or cyclic
  structures intentionally stop there.
- Patch generation is automated for two finding kinds so far: a request-body field becoming
  required, and a query/header parameter becoming required — together the most common
  breaking-change kinds in the phase 1 data. Other request-side kinds (removed
  endpoints/params, newly-required *path*/cookie params, param type changes) are detected
  and flagged with a precise comment but not yet auto-rewritten. Response-side findings are
  always flagged, not auto-rewritten, since the fix lives wherever the response is *read*,
  not at the call site itself — see `reports/PATCH_GENERATOR_RESULTS.md`.
- The watch loop (`src/monitor.py`) is live on a working, zero-cost daily GitHub Actions
  schedule. Its first manual run passed; scheduled operation still needs normal observation
  over time rather than being treated as proven by one run.
- Only one API's spec (plus one sibling, for reconciling moved-not-removed endpoints) is on
  the watchlist right now, and the one watched file is a stand-in, since there's no real
  customer repo yet.
- Generated patches land in `reports/monitor_runs/patches/`, not back into the watched file
  or a pull request — deciding how to deliver a patch to a real repo needs a real repo to
  design around.
- No domain, no billing yet — deliberately deferred until there's something worth putting in
  front of a real user. Both are identity/payment steps under EUEG, not engineering; hosting
  itself is no longer on that list, since the GitHub Actions free tier covers it.

## Who's building this

Built solo, by Claude, for EUEG OÜ. The scope agreed with Tamer (EUEG's founder, not a
software engineer): 100% of the code and every engineering decision happen without his
input; the only steps that need him are the ones that require an identity or a payment
method — registering a domain, opening a hosting account, connecting Stripe — handed over
as exact steps only when actually needed, not upfront.
