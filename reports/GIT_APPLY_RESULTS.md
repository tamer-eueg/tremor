# Real, Applyable Patches (Phase 6)

Phase 5 wired detection to patch generation, but what came out the other
end was still a full replacement file (`..._patched.py`) for a human to
diff against the original by eye. That's not what a real delivery
mechanism looks like — a real one is a patch someone (or something) can
actually apply to their checkout, or that becomes a pull request. This
phase closes that gap.

## What changed

`PatchResult` (in `src/patch_generator.py`) gained `as_git_patch()`: the
same change, expressed as a standard unified diff with `a/`/`b/` path
prefixes — the format `git apply` and `patch -p1` both expect. The CLI
gained `--patch-file <path>` to write one directly. `monitor.py`'s
`patch_watched_files()` now writes both forms for every auto-patchable
finding — the full `_patched.py` copy (useful for reading) and a real
`.patch` file (useful for applying) — and, before calling it done, actually
checks the patch with `git apply --check` against this repo's real,
currently-tracked copy of the file.

## Verified for real, not just asserted

Three checks, in increasing order of how real they are:

1. **`git apply --check`** against the actual file this repo has tracked
   in git — a dry run, no changes made. Passed.
2. **An actual `git apply`**, done in an isolated git worktree (so the
   real fixture file in the main checkout stays untouched for future blind
   tests) — the patch applied cleanly with no conflicts.
3. **The applied result recompiled** (`ast.parse`/`compile`) inside that
   worktree, confirming the patch doesn't just apply syntactically to the
   diff format — the *resulting Python file* is valid, same as every prior
   phase's checks.

The worktree was removed afterward; nothing in the tracked repo changed.
This is deliberate — `examples/sample_integration.py` has to stay in its
original, unpatched form to keep working as the blind-test fixture for
phase 3.

## What this proves, and what it doesn't

It proves the artifact Tremor generates is a real, standard patch that any
git-based workflow can consume directly — apply it, open it as a PR, hand
it to a review tool — not a bespoke format someone would need custom
tooling to use. It does not prove anything about *delivering* that patch
automatically (opening a PR against a real repo, for instance) — that
still needs an actual target repo and, likely, write credentials to it,
neither of which exist yet since Tremor has no real users. The patch file
sitting in `reports/monitor_runs/patches/` is the artifact a real delivery
mechanism would pick up and send; building that mechanism further without
a real repo to send it to would be engineering against a guess.

## Run it

```bash
python3 src/patch_generator.py examples/sample_integration.py --patch-file /tmp/out.patch
git apply --check /tmp/out.patch   # verify without applying
```
