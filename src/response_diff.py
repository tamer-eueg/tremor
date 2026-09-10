#!/usr/bin/env python3
"""
Response-body schema diffing (phase 2 of the drift-detection engine).

The request-side diffing in diff_engine.py catches changes that break a
request outright (a 4xx/422 the moment the call is made). This module
catches the quieter, more dangerous kind: a successful response whose
shape changed, so the call still succeeds but the code reading the
response silently gets None, a KeyError, or the wrong type.

Scope (stated honestly): compares the primary 2xx response schema for
each operation, one level of $ref resolution, unwrapping a top-level
array to its item schema. Deeply nested object diffing is not attempted
here -- see PROOF_OF_CONCEPT_PART2.md for what's still out of scope.
"""
import json
import sys
from collections import defaultdict

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
SUCCESS_CODES = ["200", "201"]


def load_spec(path):
    with open(path) as f:
        return json.load(f)


def resolve_ref(spec, schema):
    """Resolve a single $ref against this spec's own components. Non-ref schemas pass through."""
    if isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        parts = ref.lstrip("#/").split("/")
        node = spec
        for p in parts:
            node = node.get(p, {}) if isinstance(node, dict) else {}
        return node
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

    for combiner in ("anyOf", "oneOf"):
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


def diff_response_schemas(old_schema, new_schema):
    """Compare top-level properties of two (already-unwrapped) object schemas."""
    old_props = old_schema.get("properties", {}) if isinstance(old_schema, dict) else {}
    new_props = new_schema.get("properties", {}) if isinstance(new_schema, dict) else {}

    findings = []
    for name, old_p in old_props.items():
        if not isinstance(old_p, dict):
            continue
        new_p = new_props.get(name)
        if new_p is None:
            findings.append(("response_field_removed", name,
                              f"Response field '{name}' no longer appears in the response body."))
            continue
        old_type = old_p.get("type")
        new_type = new_p.get("type") if isinstance(new_p, dict) else None
        if old_type and new_type and old_type != new_type:
            findings.append(("response_field_type_changed", name,
                              f"Response field '{name}' changed type: {old_type} -> {new_type}."))

    added = [n for n in new_props if n not in old_props]
    return findings, added


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

            findings, added = diff_response_schemas(old_schema, new_schema)
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
