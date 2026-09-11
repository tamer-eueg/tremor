#!/usr/bin/env python3
"""
Recurring monitoring (phase 4).

Phases 1-3 all answered "given two spec snapshots I already have, what
changed?" A real product has to answer a different question: "has anything
changed since the last time I looked?" -- on its own, on a schedule, without
someone handing it two files.

This module is the watch loop. For each API in a watchlist:
  1. Fetch its current spec from the URL on record.
  2. If this is the first time this API has been checked, store the spec as
     the baseline and stop -- there's nothing to compare yet.
  3. Otherwise, diff the current spec against the *last checked* spec (a
     rolling baseline, not the all-time-original one -- so a recurring run
     reports only what's new since the previous run, the way `git diff`
     between successive commits does, not a cumulative diff back to day one).
  4. If the watchlist entry names files to watch (real integration code that
     calls this API), run phase 3's patch generator against each one with
     this check's findings, and save whatever it can auto-patch.
  5. Write a timestamped run record (findings + which files got patched),
     and print an alert if anything BREAKING showed up.
  6. Advance the baseline to the spec just fetched, so the next run's diff
     starts from here.

This is what makes phase 3 more than a demo run by hand: the patch
generator now fires from a live detection, on findings it has never seen
before, against whatever files the watchlist names -- the same code path
as examples/sample_integration.py's blind test, just triggered by a real
check instead of a CLI invocation.

The schedule/hosting piece (phase 7): this module's state cache is gzip-
compressed and lives under version control on purpose, so a scheduled
GitHub Actions run can commit the updated baseline back to the repo after
each check -- no database, no server, no cost. See
.github/workflows/monitor.yml and reports/HOSTING_RESULTS.md.
"""
import argparse
import datetime
import gzip
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(__file__))
import diff_engine
import response_diff
import patch_generator

PACKAGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.abspath(os.environ.get("TREMOR_REPO_ROOT", PACKAGE_ROOT))
DEFAULT_WATCHLIST = os.environ.get(
    "TREMOR_WATCHLIST", os.path.join(REPO_ROOT, "data", "watchlist.json")
)
STATE_DIR = os.path.abspath(os.environ.get(
    "TREMOR_STATE_DIR", os.path.join(REPO_ROOT, "data", "state")
))
RUNS_DIR = os.path.abspath(os.environ.get(
    "TREMOR_RUNS_DIR", os.path.join(REPO_ROOT, "reports", "monitor_runs")
))
PATCHES_DIR = os.path.abspath(os.environ.get(
    "TREMOR_PATCHES_DIR", os.path.join(RUNS_DIR, "patches")
))
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_watchlist(path):
    with open(path) as f:
        watchlist = json.load(f)
    validate_watchlist(watchlist)
    return watchlist


def validate_watchlist(watchlist):
    """Fail early with actionable errors before fetching or writing anything."""
    if not isinstance(watchlist, list) or not watchlist:
        raise ValueError("watchlist must be a non-empty JSON array")
    names = set()
    for index, entry in enumerate(watchlist):
        if not isinstance(entry, dict):
            raise ValueError(f"watchlist entry {index} must be an object")
        name = entry.get("name")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise ValueError(
                f"watchlist entry {index} has an invalid name; use 1-80 letters, "
                "numbers, dots, underscores, or hyphens"
            )
        if name in names:
            raise ValueError(f"watchlist contains duplicate name '{name}'")
        names.add(name)
        urls = [("spec_url", entry.get("spec_url"))]
        urls.extend(("sibling_spec_urls", url) for url in entry.get("sibling_spec_urls", []) or [])
        for field, url in urls:
            parsed = urlparse(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(f"watchlist entry '{name}' {field} must contain HTTPS URL(s)")
        watched_files = entry.get("watched_files", []) or []
        if not isinstance(watched_files, list) or not all(isinstance(p, str) for p in watched_files):
            raise ValueError(f"watchlist entry '{name}' watched_files must be a list of paths")
        for rel_path in watched_files:
            safe_repo_path(rel_path)


def safe_repo_path(rel_path):
    """Resolve a customer path and prevent absolute/parent-directory escapes."""
    if os.path.isabs(rel_path):
        raise ValueError(f"watched file must be relative to the repository: {rel_path}")
    resolved = os.path.abspath(os.path.join(REPO_ROOT, rel_path))
    try:
        inside = os.path.commonpath([REPO_ROOT, resolved]) == REPO_ROOT
    except ValueError:
        inside = False
    if not inside:
        raise ValueError(f"watched file escapes the repository: {rel_path}")
    return resolved


def validate_runtime_paths():
    """Keep all state, reports, and patches inside the customer repository."""
    for label, path in (
        ("state directory", STATE_DIR),
        ("reports directory", RUNS_DIR),
        ("patches directory", PATCHES_DIR),
    ):
        try:
            inside = os.path.commonpath([REPO_ROOT, os.path.abspath(path)]) == REPO_ROOT
        except ValueError:
            inside = False
        if not inside:
            raise ValueError(f"Tremor {label} must stay inside the repository: {path}")


def state_path(name):
    # gzip-compressed: a raw spec JSON runs ~7-13MB; API specs compress very
    # well (repeated keys, verbose schema boilerplate) -- typically 8-10x.
    # That's what makes committing the state back to the repo on every
    # scheduled run sustainable instead of bloating it by gigabytes a year.
    return os.path.join(STATE_DIR, f"{name}.json.gz")


def load_state(name):
    p = state_path(name)
    if not os.path.exists(p):
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def save_state(name, spec, checked_at):
    os.makedirs(STATE_DIR, exist_ok=True)
    with gzip.open(state_path(name), "wt", encoding="utf-8") as f:
        json.dump({"checked_at": checked_at, "spec": spec}, f)


def fetch_spec(url, timeout=60):
    import requests

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def save_run_record(name, record):
    os.makedirs(RUNS_DIR, exist_ok=True)
    ts = record["checked_at"].replace(":", "").replace("-", "")
    out_path = os.path.join(RUNS_DIR, f"{name}_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(record, f, indent=2)
    return out_path


def reclassify_moved_endpoints(changes, sibling_specs):
    """An 'endpoint_removed' finding whose exact path exists verbatim in one of
    the provider's sibling spec files (e.g. GitHub splits github.com's public
    API and GitHub Enterprise Cloud's API into separate files) isn't a real
    removal -- it moved. Downgrade those from BREAKING/endpoint_removed to
    NON-BREAKING/endpoint_moved so they don't drown out genuine breakage.
    Mutates severity/kind/detail in place; returns nothing."""
    if not sibling_specs:
        return
    for c in changes:
        if c["kind"] != "endpoint_removed":
            continue
        for sib_name, sib_spec in sibling_specs.items():
            if c["path"] in sib_spec.get("paths", {}):
                c["severity"] = "NON-BREAKING"
                c["kind"] = "endpoint_moved"
                c["detail"] = (f"Endpoint {c['path']} no longer appears in this spec, but is "
                                f"present in the '{sib_name}' sibling spec -- looks like it moved, "
                                f"not a real removal.")
                break


def patch_watched_files(entry, all_changes, checked_at, quiet=False):
    """Run patch_generator.py against every file this watchlist entry names,
    using this check's findings directly (no JSON round-trip -- they're
    already the same Python dicts diff_engine.py/response_diff.py produced).
    Writes any successful patch under reports/monitor_runs/patches/ and
    returns a summary per file, for the run record."""
    watched_files = entry.get("watched_files") or []
    summaries = []
    if not watched_files:
        return summaries

    os.makedirs(PATCHES_DIR, exist_ok=True)
    ts = checked_at.replace(":", "").replace("-", "")

    for rel_path in watched_files:
        abs_path = safe_repo_path(rel_path)
        if not os.path.exists(abs_path):
            summaries.append({"file": rel_path, "status": "not_found"})
            if not quiet:
                print(f"[{entry['name']}] watched file not found, skipping: {rel_path}")
            continue

        with open(abs_path) as f:
            source = f.read()
        result = patch_generator.generate_patch(rel_path, source, all_changes)

        summary = {
            "file": rel_path,
            "sites_matched": result.sites_matched,
            "sites_affected": result.sites_affected,
        }
        if result.has_changes:
            basename = os.path.splitext(os.path.basename(rel_path))[0]
            out_path = os.path.join(PATCHES_DIR, f"{entry['name']}_{basename}_{ts}_patched.py")
            with open(out_path, "w") as f:
                f.write(result.patched_source)

            # Also emit a standalone, git-apply-able .patch file -- the form a real
            # deployment would actually deliver (as a PR or a direct `git apply`),
            # not just a full replacement file for a human to diff by eye.
            patch_path = os.path.join(PATCHES_DIR, f"{entry['name']}_{basename}_{ts}.patch")
            with open(patch_path, "w") as f:
                f.write(result.as_git_patch())

            # Prove it's actually applyable, don't just assert it: dry-run `git apply
            # --check` against this repo's real, currently-tracked copy of the file.
            check = subprocess.run(
                ["git", "apply", "--check", os.path.relpath(patch_path, REPO_ROOT)],
                cwd=REPO_ROOT, capture_output=True, text=True,
            )
            git_apply_ok = check.returncode == 0

            summary["status"] = "patched"
            summary["patch_path"] = os.path.relpath(out_path, REPO_ROOT)
            summary["patch_file"] = os.path.relpath(patch_path, REPO_ROOT)
            summary["git_apply_check"] = "ok" if git_apply_ok else "failed"
            if not git_apply_ok:
                summary["git_apply_error"] = check.stderr.strip()
            summary["changelog"] = result.changelog
            if not quiet:
                print(f"[{entry['name']}] auto-patched {rel_path} ({result.sites_affected} "
                      f"call site(s) affected) -> {summary['patch_file']}")
                for line in result.changelog:
                    print(f"    - {line}")
                if git_apply_ok:
                    print(f"    git apply --check: OK -- applies cleanly against the current repo")
                else:
                    print(f"    git apply --check: FAILED -- {check.stderr.strip()}")
        else:
            summary["status"] = "nothing_to_patch"
            if not quiet and result.sites_matched:
                print(f"[{entry['name']}] checked {rel_path}: {result.sites_matched} API call(s) "
                      f"found, none affected by this check's findings.")
        summaries.append(summary)

    return summaries


def check_one(entry, quiet=False):
    name = entry["name"]
    url = entry["spec_url"]
    checked_at = now_iso()

    if not quiet:
        print(f"[{name}] fetching {url} ...")
    try:
        current_spec = fetch_spec(url)
    except Exception as e:
        print(f"[{name}] FETCH FAILED: {e}")
        return {"name": name, "checked_at": checked_at, "status": "fetch_failed", "error": str(e)}

    prior = load_state(name)
    if prior is None:
        save_state(name, current_spec, checked_at)
        result = {
            "name": name, "checked_at": checked_at, "status": "baseline_established",
            "paths_seen": len(current_spec.get("paths", {})),
        }
        if not quiet:
            print(f"[{name}] no prior baseline -- stored this spec "
                  f"({result['paths_seen']} paths) as the starting point. Nothing to diff yet.")
        return result

    old_spec = prior["spec"]
    request_changes = diff_engine.diff_specs(old_spec, current_spec)
    response_changes, _ = response_diff.diff_specs_responses(old_spec, current_spec)
    all_changes = request_changes + response_changes

    sibling_urls = entry.get("sibling_spec_urls") or []
    if sibling_urls:
        sibling_specs = {}
        for sib_url in sibling_urls:
            try:
                sibling_specs[sib_url] = fetch_spec(sib_url)
            except Exception as e:
                if not quiet:
                    print(f"[{name}] warning: couldn't fetch sibling spec {sib_url}: {e}")
        reclassify_moved_endpoints(all_changes, sibling_specs)

    breaking = [c for c in all_changes if c["severity"] == "BREAKING"]

    patch_summaries = patch_watched_files(entry, all_changes, checked_at, quiet=quiet)

    record = {
        "name": name,
        "checked_at": checked_at,
        "status": "checked",
        "compared_against": prior["checked_at"],
        "total_changes": len(all_changes),
        "breaking_count": len(breaking),
        "non_breaking_count": len(all_changes) - len(breaking),
        "changes": all_changes,
        "patches": patch_summaries,
    }
    run_path = save_run_record(name, record)

    # Roll the baseline forward regardless of outcome -- the next run should
    # ask "what's new since *this* check," not re-report the same drift again.
    save_state(name, current_spec, checked_at)

    if not quiet:
        if breaking:
            print(f"[{name}] ALERT: {len(breaking)} breaking change(s) since last check "
                  f"({prior['checked_at']}). Full record: {run_path}")
            for c in breaking[:5]:
                print(f"    [{c['kind']}] {c.get('detail', c)}")
            if len(breaking) > 5:
                print(f"    ... and {len(breaking) - 5} more")
        else:
            print(f"[{name}] clean -- {len(all_changes)} non-breaking change(s), "
                  f"0 breaking, since last check ({prior['checked_at']}).")

    return record


def run(watchlist_path, only_name=None, quiet=False):
    validate_runtime_paths()
    watchlist = load_watchlist(watchlist_path)
    results = []
    for entry in watchlist:
        if only_name and entry["name"] != only_name:
            continue
        results.append(check_one(entry, quiet=quiet))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"], help="'check' runs the watchlist once")
    parser.add_argument("--watchlist", default=DEFAULT_WATCHLIST)
    parser.add_argument("--name", default=None, help="only check this one entry, by name")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    results = run(args.watchlist, only_name=args.name, quiet=args.quiet)
    any_breaking = any(r.get("breaking_count", 0) > 0 for r in results)
    sys.exit(1 if any_breaking else 0)
