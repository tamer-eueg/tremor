#!/usr/bin/env python3
"""
Response-body schema diffing (phase 2 of the drift-detection engine).

The request-side diffing in diff_engine.py catches changes that break a
request outright (a 4xx/422 the moment the call is made). This module
catches the quieter, more dangerous kind: a successful response whose
shape changed, so the call still succeeds but the code reading the
response silently gets None, a KeyError, or the wrong type.

Scope: compares the primary 2xx response schema and matching documented
4xx/5xx response schemas for each operation. Local refs, arrays, composed
schemas, and nested object fields are traversed with a cycle/depth guard.
"""
import json
import sys
from collections import defaultdict

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
SUCCESS_CODES = ["200", "201"]


def load_spec(path):
    with open(path) as f:
        return json.load(f)


def resolve_ref(spec, schema, seen=None):
    """Resolve local refs against this spec, including escaped pointer tokens."""
    if isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return schema
        seen = set() if seen is None else set(seen)
        if ref in seen:
            return {}
        seen.add(ref)
        parts = ref[2:].split("/")
        node = spec
        for raw_part in parts:
            p = raw_part.replace("~1", "/").replace("~0", "~")
            node = node.get(p, {}) if isinstance(node, dict) else {}
        return resolve_ref(spec, node, seen)
    return schema if isinstance(schema, dict) else {}


def unwrap_schema(spec, schema, depth=0):
    """Resolve refs, unwrap a top-level array to its item schema, and merge
    anyOf/oneOf branches into one properties dict (a field counts as present
    if any branch declares it -- e.g. GitHub's "real object OR empty object"
    pattern for endpoints that can return nothing)."""
    schema = resolve_ref(spec, schema)
    if depth > 4:
        return schema

    if schema.get("type") == "array" and "items" in schema:
        return unwrap_schema(spec, schema["items"], depth + 1)

    for combiner in ("allOf", "anyOf", "oneOf"):
        if combiner in schema:
            merged_props = {}
            for branch in schema[combiner]:
                resolved = unwrap_schema(spec, branch, depth + 1)
                merged_props.update(resolved.get("properties", {}) or {})
            return {"type": "object", "properties": merged_props}

    return schema


def get_success_schema(spec, operation):
    responses = operation.get("responses", {})
    for code in SUCCESS_CODES:
        entry = responses.get(code)
        if not entry:
            continue
        content = entry.get("content", {})
        for media_type, media in content.items():
            schema = media.get("schema")
            if schema:
                return code, media_type, unwrap_schema(spec, schema)
    return None, None, None


def get_response_schema(spec, response):
    """Return the first documented media schema for one response entry."""
    response = resolve_ref(spec, response)
    for media_type, media in (response.get("content", {}) or {}).items():
        if not isinstance(media, dict):
            continue
        schema = media.get("schema")
        if schema:
            return media_type, unwrap_schema(spec, schema)
    return None, None


def diff_response_schemas(old_schema, new_schema, old_spec=None, new_spec=None,
                          prefix="", depth=0):
    """Compare response objects recursively while bounding cycles and noise."""
    old_spec = old_spec or {}
    new_spec = new_spec or {}
    old_schema = unwrap_schema(old_spec, old_schema)
    new_schema = unwrap_schema(new_spec, new_schema)
    old_props = old_schema.get("properties", {}) if isinstance(old_schema, dict) else {}
    new_props = new_schema.get("properties", {}) if isinstance(new_schema, dict) else {}

    findings = []
    for name, old_p in old_props.items():
        if not isinstance(old_p, dict):
            continue
        field_path = f"{prefix}.{name}" if prefix else name
        new_p = new_props.get(name)
        if new_p is None:
            findings.append(("response_field_removed", field_path,
                              f"Response field '{field_path}' no longer appears in the response body."))
            continue
        old_resolved = unwrap_schema(old_spec, old_p)
        new_resolved = unwrap_schema(new_spec, new_p)
        old_type = old_resolved.get("type")
        new_type = new_resolved.get("type") if isinstance(new_resolved, dict) else None
        if old_type and new_type and old_type != new_type:
            findings.append(("response_field_type_changed", field_path,
                              f"Response field '{field_path}' changed type: {old_type} -> {new_type}."))
            continue
        if depth < 8:
            nested, _ = diff_response_schemas(
                old_resolved, new_resolved, old_spec, new_spec,
                prefix=field_path, depth=depth + 1,
            )
            findings.extend(nested)

    added = [n for n in new_props if n not in old_props]
    return findings, added


def error_response_codes(operation):
    """Explicit documented HTTP error codes; ranges/default are intentionally excluded."""
    return {
        str(code) for code in (operation.get("responses", {}) or {})
        if str(code).isdigit() and 400 <= int(code) <= 599
    }


def diff_specs_responses(old, new):
    changes = []
    added_field_count = 0
    old_paths = old.get("paths", {})
    new_paths = new.get("paths", {})

    for path, old_ops in old_paths.items():
        new_ops = new_paths.get(path)
        if new_ops is None:
            continue  # already reported as endpoint_removed by diff_engine.py
        for method, old_op in old_ops.items():
            if method not in HTTP_METHODS:
                continue
            new_op = new_ops.get(method)
            if new_op is None:
                continue  # already reported as method_removed

            old_code, old_mt, old_schema = get_success_schema(old, old_op)
            new_code, new_mt, new_schema = get_success_schema(new, new_op)
            if old_schema is None or new_schema is None:
                continue  # nothing to compare (e.g. 204 No Content on both sides)

            findings, added = diff_response_schemas(
                old_schema, new_schema, old, new,
            )
            added_field_count += len(added)
            for kind, field, detail in findings:
                changes.append({
                    "severity": "BREAKING",
                    "kind": kind,
                    "path": path,
                    "method": method.upper(),
                    "field": field,
                    "detail": f"{method.upper()} {path}: {detail}",
                })

            # Error bodies are part of the integration contract too: clients often
            # branch on a machine-readable error code or message. Compare only codes
            # documented on both sides so adding/removing a possible status itself
            # does not create a speculative field-shape alert.
            common_errors = error_response_codes(old_op) & error_response_codes(new_op)
            for status_code in sorted(common_errors, key=int):
                _, old_error = get_response_schema(old, old_op["responses"][status_code])
                _, new_error = get_response_schema(new, new_op["responses"][status_code])
                if old_error is None or new_error is None:
                    continue
                error_findings, _ = diff_response_schemas(
                    old_error, new_error, old, new,
                )
                for kind, field, detail in error_findings:
                    changes.append({
                        "severity": "BREAKING",
                        "kind": kind.replace("response_", "error_response_", 1),
                        "path": path,
                        "method": method.upper(),
                        "status_code": status_code,
                        "field": field,
                        "detail": f"{method.upper()} {path} HTTP {status_code}: {detail}",
                    })

    return changes, added_field_count


def summarize(changes):
    by_kind = defaultdict(int)
    for c in changes:
        by_kind[c["kind"]] += 1
    return dict(by_kind)


if __name__ == "__main__":
    old_path, new_path = sys.argv[1], sys.argv[2]
    old_spec = load_spec(old_path)
    new_spec = load_spec(new_path)

    changes, added_fields = diff_specs_responses(old_spec, new_spec)
    by_kind = summarize(changes)

    print(f"Old spec: {old_path}")
    print(f"New spec: {new_path}")
    print(f"\nResponse-body BREAKING changes found: {len(changes)}")
    for kind, count in sorted(by_kind.items(), key=lambda x: -x[1]):
        print(f"  {kind}: {count}")
    print(f"\n(informational, not reported as findings) new response fields added: {added_fields}")

    with open("response_diff_report.json", "w") as f:
        json.dump(changes, f, indent=2)
    print("\nFull list written to response_diff_report.json")

    print("\nFirst 10 examples:")
    for c in changes[:10]:
        print(f"  [{c['kind']}] {c['detail']}")
