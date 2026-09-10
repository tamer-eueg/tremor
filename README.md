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
| Patch generation (automated, `src/patch_generator.py`) | Done for the first finding kind | Given a real source file, finds the affected function on its own and generates the patch — verified against a blind test, see `reports/PATCH_GENERATOR_RESULTS.md` |
| Scheduler / hosting | Not started | Needs a domain + hosting account when we get there |
| Billing | Not started | Needs a Stripe account when we get there |

**103 distinct, verified breaking changes found across both layers**, between two real
versions of one real, actively-maintained API. See `reports/` for full detail.

The patch generator (`src/patch_generator.py`) then took one of those findings and, run
blind against a source file it had never seen, found the affected function on its own,
generated the exact same fix that was previously built by hand, and the result was verified
by actually running it — see `reports/PATCH_GENERATOR_RESULTS.md`.

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
├── src/                     # the diffing engines + patch generator
│   ├── diff_engine.py       # request-side: params, required fields, removed endpoints
│   ├── response_diff.py     # response-side: removed/changed response fields
│   └── patch_generator.py   # phase 3: finds affected functions in real source, patches them
├── examples/
│   ├── example_patch.py         # one hand-built before/after patch, worked example (phase 1)
│   └── sample_integration.py    # stand-in customer file used to test patch_generator.py blind
├── reports/
│   ├── PROOF_OF_CONCEPT.md          # phase 1 write-up (request-side)
│   ├── RESPONSE_DIFF_RESULTS.md     # phase 2 write-up (response-side, incl. the bug fix)
│   ├── PATCH_GENERATOR_RESULTS.md   # phase 3 write-up (automated patch generation)
│   ├── diff_report.json             # full phase 1 output (213 changes)
│   └── response_diff_report.json    # full phase 2 output (62 breaking changes)
└── data/
    ├── old_spec.json        # GitHub REST API spec, tag v1.0.0
    └── new_spec.json        # GitHub REST API spec, tag v2.1.0
```

## Run it

```bash
python3 src/diff_engine.py data/old_spec.json data/new_spec.json
python3 src/response_diff.py data/old_spec.json data/new_spec.json
python3 examples/example_patch.py
python3 src/patch_generator.py examples/sample_integration.py --write
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
- Patch generation is automated for one finding kind so far (a request-body field becoming
  required — the most common breaking change in the phase 1 data). Other request-side kinds
  (removed endpoints/params, newly-required params) are detected and flagged but not yet
  auto-rewritten. Response-side findings are flagged with a precise comment, not
  auto-rewritten, since the fix lives wherever the response is *read*, not at the call site
  itself — see `reports/PATCH_GENERATOR_RESULTS.md`. **Widening the auto-patchable set is
  the current build focus.**
- Only one API has been tested against. A real product needs a scheduler that periodically
  re-fetches specs for every API a customer depends on, and somewhere to store/display
  findings over time.
- No hosting, no domain, no billing yet — deliberately deferred until there's something
  worth putting in front of a real user. These three are identity/payment steps under EUEG,
  not engineering.

## Who's building this

Built solo, by Claude, for EUEG OÜ. The scope agreed with Tamer (EUEG's founder, not a
software engineer): 100% of the code and every engineering decision happen without his
input; the only steps that need him are the ones that require an identity or a payment
method — registering a domain, opening a hosting account, connecting Stripe — handed over
as exact steps only when actually needed, not upfront.
