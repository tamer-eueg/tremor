# Response-Body Diffing — Results (Phase 2)

Closes the gap flagged in the first proof-of-concept: the request-side engine catches changes that make a call fail outright; this catches the quieter kind — a call that still *succeeds* but returns a shape the calling code no longer expects.

**Same real test data as before:** GitHub's own REST API spec, `v1.0.0` → `v2.1.0`.

## Result (after fixing a bug I found in my own tool)

First run found 204 "removed" fields — too high, so I checked a sample before trusting it. `GET /orgs/{org}/interaction-limits` was flagged as losing all three of its fields, which seemed drastic enough to verify by hand. It turned out the new spec wraps that response in an `anyOf` (real object OR an empty one, for orgs with no interaction limit set), and my first pass didn't look inside `anyOf`/`oneOf` branches — so it read every field in that shape as gone. Fixed the resolver to merge fields across those branches; re-ran; verified a second, unrelated example by hand to confirm the fix held.

**Corrected result: 62 real breaking response-body changes** — 58 fields that quietly disappeared from responses, 4 that changed type. Verified two independent examples by hand against the actual spec files, not just trusting the script's output.

A genuine one: `GET /meta` used to return `github_services_sha` and `installed_version`; neither exists in the response anymore. Any integration still reading those fields gets `None` today with no error, no warning — exactly the failure mode this tool exists to catch.

## Where things stand now

- Request-side diffing: done (41 breaking changes found in phase 1)
- Response-side diffing: done (62 breaking changes found in phase 2, after catching and fixing a real bug in the first pass)
- Total distinct breaking changes across both: 103, between two real versions of one real API

## Honest scope still open

- Only the primary 2xx response is compared; error-response shapes (4xx/5xx bodies) aren't diffed.
- Nested objects more than a few levels deep aren't fully walked.
- Next build step: the automated patch-generation pipeline — given a finding like the two above, generate the actual code fix, the way `example_patch.py` did by hand for one request-side change in phase 1.
