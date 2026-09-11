#!/usr/bin/env python3
"""
Core schema-drift detection engine (proof of concept).

Compares two OpenAPI specs (an "old" version an integration was built
against, and a "new" version the provider has since shipped) and reports
every change, classified as BREAKING or NON-BREAKING for API consumers.

This is deliberately scoped to the changes that most commonly break real
integrations: removed endpoints/methods, removed or newly-required
parameters, and parameter type changes. Deep response-body diffing is
noted as future work, not attempted here.
"""
import json
import sys
from collections import defaultdict

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def load_spec(path):
    with open(path) as f:
        return json.load(f)


def params_by_key(params):
    """Index parameters by (name, in) -> param dict."""
    out = {}
    for p in params or []:
        key = (p.get("name"), p.get("in"))
        out[key] = p
    return out


def resolve_local_ref(spec, node, seen=None):
    """Resolve a local JSON Pointer while refusing cycles and remote refs."""
    if not isinstance(node, dict) or "$ref" not in node:
        return node if isinstance(node, dict) else {}
    ref = node["$ref"]
    if not ref.startswith("#/"):
        return node
    seen = set() if seen is None else set(seen)
    if ref in seen:
        return {}
    seen.add(ref)
    target = spec
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or part not in target:
            return {}
        target = target[part]
    return resolve_local_ref(spec, target, seen)


def required_schema_props(spec, schema, seen=None):
    """Collect fields required by a schema, including safe composition rules.

    allOf requires the union of its branches.  For anyOf/oneOf, a field is
    globally required only when every alternative requires it.
    """
    if not isinstance(schema, dict):
        return set()
    seen = set() if seen is None else set(seen)
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            return set()
        seen.add(ref)
        return required_schema_props(spec, resolve_local_ref(spec, schema), seen)

    required = set(schema.get("required", []) or [])
    for branch in schema.get("allOf", []) or []:
        required |= required_schema_props(spec, branch, seen)
    for combiner in ("anyOf", "oneOf"):
        branches = schema.get(combiner, []) or []
        if branches:
            branch_sets = [required_schema_props(spec, branch, seen) for branch in branches]
            required |= set.intersection(*branch_sets) if branch_sets else set()
    return required


def required_body_props(spec, operation):
    """Pull required property names from an inline or locally referenced body."""
    request_body = resolve_local_ref(spec, operation.get("requestBody", {}))
    content = request_body.get("content", {}) if isinstance(request_body, dict) else {}
    for media in content.values():
        if isinstance(media, dict):
            return required_schema_props(spec, media.get("schema", {}))
    return set()


def operation_params(path_item, operation):
    """Merge OpenAPI path-level and operation-level parameters.

    OpenAPI lets an operation override a same-name path parameter, so the
    operation list is applied second.
    """
    merged = params_by_key(path_item.get("parameters"))
    merged.update(params_by_key(operation.get("parameters")))
    return merged


def diff_specs(old, new):
    changes = []
    old_paths = old.get("paths", {})
    new_paths = new.get("paths", {})

    for path, old_ops in old_paths.items():
        new_ops = new_paths.get(path)
        if new_ops is None:
            changes.append({
                "severity": "BREAKING",
                "kind": "endpoint_removed",
                "path": path,
                "method": None,
                "detail": f"Endpoint {path} no longer exists in the new spec.",
            })
            continue

        for method, old_op in old_ops.items():
            if method not in HTTP_METHODS:
                continue
            new_op = new_ops.get(method)
            if new_op is None:
                changes.append({
                    "severity": "BREAKING",
                    "kind": "method_removed",
                    "path": path,
                    "method": method.upper(),
                    "detail": f"{method.upper()} {path} no longer exists.",
                })
                continue

            old_params = operation_params(old_ops, old_op)
            new_params = operation_params(new_ops, new_op)

            for key, old_p in old_params.items():
                name, loc = key
                new_p = new_params.get(key)
                if new_p is None:
                    changes.append({
                        "severity": "BREAKING",
                        "kind": "parameter_removed",
                        "path": path,
                        "method": method.upper(),
                        "param": name,
                        "in": loc,
                        "detail": f"Parameter '{name}' ({loc}) was removed from {method.upper()} {path}.",
                    })
                    continue
                old_req = bool(old_p.get("required"))
                new_req = bool(new_p.get("required"))
                if not old_req and new_req:
                    changes.append({
                        "severity": "BREAKING",
                        "kind": "parameter_now_required",
                        "path": path,
                        "method": method.upper(),
                        "param": name,
                        "in": loc,
                        "detail": f"Parameter '{name}' ({loc}) on {method.upper()} {path} was optional and is now required.",
                    })
                old_type = (old_p.get("schema") or {}).get("type")
                new_type = (new_p.get("schema") or {}).get("type")
                if old_type and new_type and old_type != new_type:
                    changes.append({
                        "severity": "BREAKING",
                        "kind": "parameter_type_changed",
                        "path": path,
                        "method": method.upper(),
                        "param": name,
                        "in": loc,
                        "detail": f"Parameter '{name}' ({loc}) on {method.upper()} {path} changed type: {old_type} -> {new_type}.",
                    })

            for key, new_p in new_params.items():
                if key not in old_params:
                    name, loc = key
                    sev = "BREAKING" if new_p.get("required") else "NON-BREAKING"
                    kind = "new_required_parameter" if new_p.get("required") else "new_optional_parameter"
                    changes.append({
                        "severity": sev,
                        "kind": kind,
                        "path": path,
                        "method": method.upper(),
                        "param": name,
                        "in": loc,
                        "detail": f"New {'required' if new_p.get('required') else 'optional'} parameter '{name}' ({loc}) added to {method.upper()} {path}.",
                    })

            old_req_body = required_body_props(old, old_op)
            new_req_body = required_body_props(new, new_op)
            newly_required = new_req_body - old_req_body
            for prop in newly_required:
                changes.append({
                    "severity": "BREAKING",
                    "kind": "request_body_field_now_required",
                    "path": path,
                    "method": method.upper(),
                    "field": prop,
                    "detail": f"Request body field '{prop}' on {method.upper()} {path} is now required.",
                })

    for path, new_ops in new_paths.items():
        if path not in old_paths:
            changes.append({
                "severity": "NON-BREAKING",
                "kind": "endpoint_added",
                "path": path,
                "method": None,
                "detail": f"New endpoint {path} was added.",
            })

    return changes


def summarize(changes):
    by_kind = defaultdict(int)
    breaking = 0
    for c in changes:
        by_kind[c["kind"]] += 1
        if c["severity"] == "BREAKING":
            breaking += 1
    return breaking, len(changes) - breaking, dict(by_kind)


if __name__ == "__main__":
    old_path, new_path = sys.argv[1], sys.argv[2]
    old_spec = load_spec(old_path)
    new_spec = load_spec(new_path)
    changes = diff_specs(old_spec, new_spec)
    breaking, non_breaking, by_kind = summarize(changes)

    print(f"Old spec: {old_path} ({len(old_spec.get('paths', {}))} paths)")
    print(f"New spec: {new_path} ({len(new_spec.get('paths', {}))} paths)")
    print(f"\nTotal changes detected: {len(changes)}")
    print(f"  BREAKING:     {breaking}")
    print(f"  NON-BREAKING: {non_breaking}")
    print("\nBy kind:")
    for kind, count in sorted(by_kind.items(), key=lambda x: -x[1]):
        print(f"  {kind}: {count}")

    out_path = "diff_report.json"
    with open(out_path, "w") as f:
        json.dump(changes, f, indent=2)
    print(f"\nFull change list written to {out_path}")
