# Running Continuously, at Zero Cost (Phase 7)

Every phase so far ran when I ran it, by hand, inside this session. That's
not "recurring monitoring" — it's a script that works. This phase makes it
actually recurring, without waiting on a hosting decision.

## The approach

`.github/workflows/monitor.yml` runs `python3 src/monitor.py check` once a
day on GitHub Actions' free tier, then commits whatever changed —
the updated rolling-baseline state and the new run record — straight back
to the repo, using the token GitHub Actions provides automatically (no
separate secret needed to push to the same repo). No server, no database,
no monthly bill.

This only works because of one change made alongside it: `monitor.py`'s
state cache is now gzip-compressed. A raw GitHub API spec runs 7-13MB;
compressed, it's about 460KB — roughly 24x smaller in practice (specs are
mostly repeated key names and verbose schema boilerplate, which gzip is
very good at). At that size, committing an updated baseline every day for
a year adds well under 200MB to the repo — sustainable indefinitely, not a
countdown to a storage problem. Verified directly: re-seeded the state,
ran a real check, confirmed the compressed file size and that the check
still produces the identical result (139 breaking changes, same as the
uncompressed version) — the compression is transparent to correctness, not
just smaller.

The schedule is daily rather than something tighter, deliberately: GitHub
Actions doesn't guarantee cron fires to the exact minute on a busy
scheduler, and a monitor checking "about once a day" is the actual
requirement here, not a stopwatch. `workflow_dispatch` is also enabled, so
a run can be triggered on demand from the Actions tab or its API — useful
for demonstrating it live rather than waiting for the next 06:00 UTC.

## What's verified, and what isn't yet

Verified: the workflow file is valid YAML with the right structure: two
triggers (schedule + manual), `contents: write` permission (needed to push
the commit back), and the same shell sequence — `git add` the state and
run-record directories, commit if anything changed, push — tested by hand
against this real repo (minus the push, since there's no remote yet).

Not yet verified, because it can't be from inside this session: an actual
run on GitHub's infrastructure. That requires a real GitHub repository to
push this code to, which is the one genuine identity step left — someone
has to own a GitHub account or org for Tremor's code to live in. Once that
exists, the very first scheduled or manually-triggered run either works or
it doesn't, and that's the real proof, not this write-up.

## What this doesn't solve

- No dashboard or public status page — a check's result lives in a commit
  and a JSON file, readable by anyone who can read the repo, not presented
  as anything yet.
- No notification beyond the workflow run's own red/green status (and a
  failed run's log) — nobody gets pinged when something breaks.
- Still just one API (plus one sibling spec) and one stand-in watched file.
- The free tier is genuinely free at this scale (one short job, once a
  day) — it stops being free, or hits Actions minute limits, well before
  it stops being useful, so this isn't a permanent substitute for real
  hosting if Tremor ever has real, paying watch targets. It's the right
  amount of infrastructure for right now, not the final architecture.

## What's needed to actually turn this on

A GitHub repository this code can be pushed to, under an account or org
Tamer controls. That's the one remaining identity step — creating the
repo — and, separately, either he pushes this code himself, or gives a
narrowly-scoped access token for just that one repo so the push can happen
from here directly. Either way, once the code is there, the schedule and
the `workflow_dispatch` button do the rest with no further engineering.
