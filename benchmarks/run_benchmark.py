#!/usr/bin/env python3
"""Run Tremor's deterministic contract-diff benchmark.

The labeled cases are deliberately small: each introduces one known OpenAPI
change, which makes false positives and missed findings unambiguous.  A second
regression check runs the engines against the repository's historical GitHub
spec pair and compares the output with the reviewed reports already committed.
"""
from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import diff_engine  # noqa: E402
import response_diff  # noqa: E402


def operation():
    return {
        "parameters": [
            {"name": "limit", "in": "query", "required": False,
             "schema": {"type": "integer"}},
            {"name": "X-Trace", "in": "header", "required": False,
             "schema": {"type": "string"}},
        ],
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "note": {"type": "string"},
                        },
                    }
                }
            }
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "name": {"type": "string"},
                            },
                        }
                    }
                }
            }
        },
    }


def base_spec():
    return {"openapi": "3.0.0", "paths": {"/widgets": {"get": operation()}}}


def key(change):
    return (
        change.get("kind"), change.get("path"), change.get("method"),
        change.get("param"), change.get("in"), change.get("field"),
        change.get("severity"),
    )


def expected(kind, *, severity="BREAKING", path="/widgets", method="GET",
             param=None, location=None, field=None):
    return (kind, path, method, param, location, field, severity)


def case(name, mutate, expected_findings, prepare=None):
    old = base_spec()
    if prepare:
        prepare(old)
    new = copy.deepcopy(old)
    mutate(new)
    return {"name": name, "old": old, "new": new,
            "expected": set(expected_findings)}


def cases():
    result = []
    result.append(case("unchanged contract", lambda s: None, []))
    result.append(case(
        "endpoint removed",
        lambda s: s["paths"].pop("/widgets"),
        [expected("endpoint_removed", method=None)],
    ))
    result.append(case(
        "method removed",
        lambda s: s["paths"]["/widgets"].pop("get"),
        [expected("method_removed")],
    ))
    result.append(case(
        "parameter removed",
        lambda s: s["paths"]["/widgets"]["get"]["parameters"].pop(0),
        [expected("parameter_removed", param="limit", location="query")],
    ))
    result.append(case(
        "parameter becomes required",
        lambda s: s["paths"]["/widgets"]["get"]["parameters"][0].update(required=True),
        [expected("parameter_now_required", param="limit", location="query")],
    ))
    result.append(case(
        "parameter type changes",
        lambda s: s["paths"]["/widgets"]["get"]["parameters"][0]["schema"].update(type="string"),
        [expected("parameter_type_changed", param="limit", location="query")],
    ))
    result.append(case(
        "required parameter added",
        lambda s: s["paths"]["/widgets"]["get"]["parameters"].append(
            {"name": "cursor", "in": "query", "required": True,
             "schema": {"type": "string"}}),
        [expected("new_required_parameter", param="cursor", location="query")],
    ))
    result.append(case(
        "optional parameter added safely",
        lambda s: s["paths"]["/widgets"]["get"]["parameters"].append(
            {"name": "cursor", "in": "query", "required": False,
             "schema": {"type": "string"}}),
        [expected("new_optional_parameter", severity="NON-BREAKING",
                  param="cursor", location="query")],
    ))
    result.append(case(
        "required request field added",
        lambda s: s["paths"]["/widgets"]["get"]["requestBody"]["content"]
        ["application/json"]["schema"]["required"].append("note"),
        [expected("request_body_field_now_required", field="note")],
    ))
    result.append(case(
        "response field removed",
        lambda s: s["paths"]["/widgets"]["get"]["responses"]["200"]["content"]
        ["application/json"]["schema"]["properties"].pop("name"),
        [expected("response_field_removed", field="name")],
    ))
    result.append(case(
        "response field type changes",
        lambda s: s["paths"]["/widgets"]["get"]["responses"]["200"]["content"]
        ["application/json"]["schema"]["properties"]["id"].update(type="string"),
        [expected("response_field_type_changed", field="id")],
    ))
    result.append(case(
        "response field added safely",
        lambda s: s["paths"]["/widgets"]["get"]["responses"]["200"]["content"]
        ["application/json"]["schema"]["properties"].update(active={"type": "boolean"}),
        [],
    ))
    result.append(case(
        "endpoint added safely",
        lambda s: s["paths"].update({"/health": {"get": operation()}}),
        [expected("endpoint_added", severity="NON-BREAKING", path="/health", method=None)],
    ))

    def wrap_response_in_any_of(spec):
        media = spec["paths"]["/widgets"]["get"]["responses"]["200"]["content"]["application/json"]
        media["schema"] = {"anyOf": [media["schema"], {"type": "object", "properties": {}}]}

    result.append(case("anyOf wrapper does not invent removals", wrap_response_in_any_of, []))

    def path_parameter(spec):
        spec["paths"]["/widgets"]["parameters"] = [
            {"name": "tenant", "in": "header", "required": False,
             "schema": {"type": "string"}}
        ]

    result.append(case(
        "path-level parameter becomes required",
        lambda s: s["paths"]["/widgets"]["parameters"][0].update(required=True),
        [expected("parameter_now_required", param="tenant", location="header")],
        prepare=path_parameter,
    ))

    def overridden_path_parameter(spec):
        path_parameter(spec)
        spec["paths"]["/widgets"]["get"]["parameters"].append(
            {"name": "tenant", "in": "header", "required": True,
             "schema": {"type": "string"}}
        )

    result.append(case(
        "operation parameter safely overrides path parameter",
        lambda s: None,
        [],
        prepare=overridden_path_parameter,
    ))

    def referenced_schema(spec):
        content = spec["paths"]["/widgets"]["get"]["requestBody"]["content"]
        schema = content["application/json"]["schema"]
        spec["components"] = {"schemas": {"WidgetInput": schema}}
        content["application/json"]["schema"] = {"$ref": "#/components/schemas/WidgetInput"}

    result.append(case(
        "required field added through schema reference",
        lambda s: s["components"]["schemas"]["WidgetInput"]["required"].append("note"),
        [expected("request_body_field_now_required", field="note")],
        prepare=referenced_schema,
    ))

    def referenced_request_body(spec):
        body = spec["paths"]["/widgets"]["get"].pop("requestBody")
        spec["components"] = {"requestBodies": {"WidgetBody": body}}
        spec["paths"]["/widgets"]["get"]["requestBody"] = {
            "$ref": "#/components/requestBodies/WidgetBody"
        }

    result.append(case(
        "required field added through requestBody reference",
        lambda s: s["components"]["requestBodies"]["WidgetBody"]["content"]
        ["application/json"]["schema"]["required"].append("note"),
        [expected("request_body_field_now_required", field="note")],
        prepare=referenced_request_body,
    ))

    def composed_body(spec):
        media = spec["paths"]["/widgets"]["get"]["requestBody"]["content"]["application/json"]
        media["schema"] = {
            "allOf": [
                {"type": "object", "required": ["name"]},
                {"type": "object", "required": []},
            ]
        }

    result.append(case(
        "required field added through allOf",
        lambda s: s["paths"]["/widgets"]["get"]["requestBody"]["content"]
        ["application/json"]["schema"]["allOf"][1]["required"].append("note"),
        [expected("request_body_field_now_required", field="note")],
        prepare=composed_body,
    ))

    def alternative_body(spec):
        media = spec["paths"]["/widgets"]["get"]["requestBody"]["content"]["application/json"]
        media["schema"] = {
            "anyOf": [
                {"type": "object", "required": ["name"]},
                {"type": "object", "required": ["name"]},
            ]
        }

    result.append(case(
        "branch-only anyOf requirement does not create false alert",
        lambda s: s["paths"]["/widgets"]["get"]["requestBody"]["content"]
        ["application/json"]["schema"]["anyOf"][0]["required"].append("note"),
        [],
        prepare=alternative_body,
    ))
    return result


def run_labeled_cases():
    rows = []
    totals = Counter()
    for item in cases():
        request_findings = diff_engine.diff_specs(item["old"], item["new"])
        response_findings, _ = response_diff.diff_specs_responses(item["old"], item["new"])
        actual = {key(c) for c in request_findings + response_findings}
        wanted = item["expected"]
        tp = len(actual & wanted)
        fp = len(actual - wanted)
        fn = len(wanted - actual)
        totals.update(tp=tp, fp=fp, fn=fn)
        rows.append({
            "case": item["name"], "passed": fp == 0 and fn == 0,
            "true_positives": tp, "false_positives": fp, "misses": fn,
            "unexpected": [list(x) for x in sorted(actual - wanted, key=str)],
            "missing": [list(x) for x in sorted(wanted - actual, key=str)],
        })
    precision = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 1.0
    recall = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 1.0
    return rows, dict(totals), precision, recall


def normalized(change):
    return {k: v for k, v in change.items() if k != "detail"}


def finding_multiset(changes):
    """Compare findings as data, independent of harmless emission ordering."""
    return Counter(json.dumps(normalized(change), sort_keys=True) for change in changes)


def run_historical_regression():
    old = diff_engine.load_spec(ROOT / "data" / "old_spec.json")
    new = diff_engine.load_spec(ROOT / "data" / "new_spec.json")
    request_actual = diff_engine.diff_specs(old, new)
    response_actual, _ = response_diff.diff_specs_responses(old, new)
    with (ROOT / "reports" / "diff_report.json").open() as f:
        request_reviewed = json.load(f)
    with (ROOT / "reports" / "response_diff_report.json").open() as f:
        response_reviewed = json.load(f)
    request_match = finding_multiset(request_actual) == finding_multiset(request_reviewed)
    response_match = finding_multiset(response_actual) == finding_multiset(response_reviewed)
    return {
        "dataset": "GitHub REST API v1.0.0 to v2.1.0",
        "request_findings": len(request_actual),
        "response_breaking_findings": len(response_actual),
        "matches_reviewed_request_report": request_match,
        "matches_reviewed_response_report": response_match,
        "passed": request_match and response_match,
    }


def write_reports(result):
    report_dir = ROOT / "reports"
    with (report_dir / "benchmark_results.json").open("w") as f:
        json.dump(result, f, indent=2)

    lines = [
        "# Tremor benchmark results", "",
        "This benchmark separates two kinds of evidence:", "",
        "1. **Labeled contract mutations** measure detection precision and recall. Each case has an exact expected result.",
        "2. **Historical regression** confirms current output still matches Tremor's reviewed GitHub API reports. It is a regression check, not independent ground truth.",
        "", "## Result", "",
        f"- Labeled cases passed: **{result['labeled']['cases_passed']}/{result['labeled']['cases_total']}**",
        f"- Precision: **{result['labeled']['precision']:.1%}**",
        f"- Recall: **{result['labeled']['recall']:.1%}**",
        f"- False positives: **{result['labeled']['totals']['fp']}**",
        f"- Missed expected findings: **{result['labeled']['totals']['fn']}**",
        f"- Historical regression: **{'PASS' if result['historical_regression']['passed'] else 'FAIL'}**",
        "", "## Labeled cases", "",
        "| Case | Result | False positives | Misses |", "|---|---:|---:|---:|",
    ]
    for row in result["labeled"]["cases"]:
        lines.append(f"| {row['case']} | {'PASS' if row['passed'] else 'FAIL'} | {row['false_positives']} | {row['misses']} |")
    lines += [
        "", "## Interpretation", "",
        "Passing this suite proves the engines behave correctly for these explicitly modeled top-level OpenAPI changes and retain their reviewed output on the bundled historical GitHub data. It does **not** prove correctness for every OpenAPI feature or every API provider.",
        "", "Known limits remain: deep nested response traversal, error-response schemas, cross-file references, and automated patches for every finding kind are not covered yet.",
        "", "## Reproduce", "", "```bash", "python3 benchmarks/run_benchmark.py", "```", "",
    ]
    (report_dir / "BENCHMARK_RESULTS.md").write_text("\n".join(lines))


def main():
    rows, totals, precision, recall = run_labeled_cases()
    historical = run_historical_regression()
    result = {
        "labeled": {
            "cases_total": len(rows),
            "cases_passed": sum(row["passed"] for row in rows),
            "precision": precision, "recall": recall,
            "totals": totals, "cases": rows,
        },
        "historical_regression": historical,
    }
    write_reports(result)
    print(f"Labeled cases: {result['labeled']['cases_passed']}/{len(rows)} passed")
    print(f"Precision: {precision:.1%} | Recall: {recall:.1%}")
    print(f"Historical regression: {'PASS' if historical['passed'] else 'FAIL'}")
    return 0 if result['labeled']['cases_passed'] == len(rows) and historical["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
