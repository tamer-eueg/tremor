#!/usr/bin/env python3
"""Check engineering/documentation gates without making launch decisions."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    errors = []
    required = [
        "README.md", "INSTALLATION.md", "SECURITY.md", "CONTRIBUTING.md",
        "CHANGELOG.md", "RELEASE_CHECKLIST.md", "VERSION", "action.yml",
        "examples/tremor-watch.workflow.yml",
        "examples/tremor-review-pr.workflow.yml",
    ]
    for relative in required:
        if not (ROOT / relative).is_file():
            errors.append(f"missing required release file: {relative}")

    version = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"0\.[0-9]+\.[0-9]+", version):
        errors.append("VERSION must be a pre-1.0 semantic version such as 0.1.0")

    if (ROOT / "requirements.txt").exists():
        errors.append("unexpected runtime dependency manifest: requirements.txt")

    action = (ROOT / "action.yml").read_text()
    if "pip install" in action:
        errors.append("composite Action still installs third-party Python packages")
    if "pull-requests: write" in action or "gh pr merge" in action:
        errors.append("base Action contains PR privilege or merge behavior")

    review_workflow = (ROOT / "examples" / "tremor-review-pr.workflow.yml").read_text()
    if "gh pr create" not in review_workflow:
        errors.append("review workflow does not create the proposed PR")
    if "gh pr merge" in review_workflow:
        errors.append("review workflow contains forbidden automatic merge behavior")

    readme = (ROOT / "README.md").read_text()
    stale_claims = [
        "there's no GitHub repo",
        "schedule has never actually fired",
        "not back into the watched file or a pull request",
        "Built solo, by Claude",
    ]
    for claim in stale_claims:
        if claim in readme:
            errors.append(f"README contains stale claim: {claim}")

    with (ROOT / "reports" / "benchmark_results.json").open() as f:
        benchmark = json.load(f)
    labeled = benchmark.get("labeled", {})
    if labeled.get("cases_total") != 27 or labeled.get("cases_passed") != 27:
        errors.append("committed benchmark result is not 27/27")
    if benchmark.get("historical_regression", {}).get("passed") is not True:
        errors.append("committed historical regression is not passing")

    if errors:
        print("Release engineering gates: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Release engineering gates: PASS (candidate v{version})")
    print("Owner gates intentionally remain: license selection and public-launch approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
