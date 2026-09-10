# Automated Patch Generation — Results (Phase 3)

Closes the gap flagged at the end of phase 1: the worked example in
`examples/example_patch.py` was written *by hand* — I read one diff finding
and rewrote one function to match. This phase automates that: given a real
source file and the diff reports, `src/patch_generator.py` finds which
functions call which API endpoints on its own, and generates the patch
itself.

## The test (blind, on purpose)

`examples/sample_integration.py` was written as a stand-in for a real
customer codebase — two functions, written independently against the OLD
GitHub API spec, with no hints in the file about which diff findings apply
to them:

- `add_pr_review_comment()` — calls the same endpoint as the hand-built
  phase-1 example (`POST /repos/{owner}/{repo}/pulls/{pull_number}/comments`),
  but with different local variable names for the URL (this matters — see
  "how matching works" below).
- `get_meta()` — calls `GET /meta`, the endpoint with the verified
  response-body regression from phase 2 (`github_services_sha` and
  `installed_version` silently disappeared).

Command: `python3 src/patch_generator.py examples/sample_integration.py --write`

## Result

The tool found both functions' API calls with no hints about where to look,
matched them against `reports/diff_report.json` and
`reports/response_diff_report.json`, and:

- **`add_pr_review_comment()`**: added `commit_id` and `line` to the function
  signature (no default value — a missing argument is now a clear
  `TypeError` at the call site, not a silent 422 from GitHub) and to the
  outgoing JSON payload, with a comment on each new line citing that it was
  auto-patched. This is the exact same fix as the hand-built
  `examples/example_patch.py` — but generated automatically this time.
- **`get_meta()`**: since safely rewriting *every* place a codebase reads a
  response field is a different, harder problem than one function's source
  can answer (see "what's still manual" below), the tool doesn't attempt
  it — it inserts a comment block into the function citing exactly which
  response fields went away, so whoever owns the surrounding code knows
  precisely what to check.

Verified, not just trusted: the patched file was checked with Python's own
`ast.parse`/`compile` (syntactically valid), and called directly —
supplying the old argument list now raises `TypeError: add_pr_review_comment()
missing 2 required positional arguments: 'commit_id' and 'line'` instead of
silently going on to fail at GitHub with a 422.

## How matching works

The tool never assumes it knows the customer's variable names. It resolves
each `requests.<method>(url, ...)` call's URL — whether it's an f-string
inline or assigned to a variable first — into a "path shape": every literal
`/repos/`, `/pulls/`, etc. kept, every `{variable}` collapsed to a generic
placeholder. The same collapsing is applied to every path in the OpenAPI
spec. Comparing shapes instead of literal text is what lets
`add_pr_review_comment()`'s local variables (`owner`, `pull_number`, ...)
match the spec's own parameter names without the two ever needing to agree
on naming.

## What's automated vs. what's still manual

| Finding kind | Handling |
|---|---|
| `request_body_field_now_required` | **Fully automated** — signature + payload patched, verified above |
| `response_field_removed` / `response_field_type_changed` | Flagged with a precise comment; not rewritten (see below) |
| `endpoint_removed`, `method_removed`, `parameter_removed`, `parameter_now_required`, `parameter_type_changed` | Detected and would be flagged the same way if a matching call site existed in the test file (none did, in this run) |

Response-body changes aren't auto-rewritten because the fix isn't at the
call site — it's at every place elsewhere in the codebase that reads the
now-missing field, which a single function's source can't tell us. Flagging
precisely, rather than guessing at a rewrite, is the honest choice here.

## What's next

- Widen the auto-patchable set: `parameter_now_required` (a query/path
  param, not just a body field) is the same shape of fix as the one already
  automated and should be next.
- The scheduler/monitoring service — periodically re-fetch a tracked API's
  spec, run both diff engines, and run this patch generator against a real
  customer repo automatically.
- Multi-file support: right now this runs against one file at a time.
