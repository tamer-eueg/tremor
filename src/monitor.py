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
  4. Write a timestamped run record, and print an alert if anything BREAKING
     showed up.
  5. Advance the baseline to the spec just fetched, so the next run's diff
     starts from here.

Deliberately not attempted here: the actual cron/timer that calls this on a
schedule, and the hosting to run it on. Both are the next step, and the
second one needs a hosting account (an identity/payment step, not
engineering). What's built here is everything that step would call.
"""
import argparse
import datetime
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(__file__))
import diff_engine
import response_diff

DEFAULT_WATCHLIST = os.path.join(os.path.dirname(__file__), "..", "data", "watchlist.json")
STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "state")
RUNS_DIR = os.path.join(os.path.dirname(__file__), "..", "reports", "monitor_runs")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_watchlist(path):
    with open(path) as f:
        return json.load(f)


def state_path(name):
    return os.path.join(STATE_DIR, f"{name}.json")


def load_state(name):
    p = state_path(name)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def save_state(name, spec, checked_at):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(name), "w") as f:
        json.dump({"checked_at": checked_at, "spec": spec}, f)


def fetch_spec(url, timeout=60):
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

    record = {
        "name": name,
        "checked_at": checked_at,
        "status": "checked",
        "compared_against": prior["checked_at"],
        "total_changes": len(all_changes),
        "breaking_count": len(breaking),
        "non_breaking_count": len(all_changes) - len(breaking),
        "changes": all_changes,
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
