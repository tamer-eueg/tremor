#!/usr/bin/env python3
"""
Automated patch generation (phase 3).

Takes a diff finding (from diff_engine.py / response_diff.py's output) and a
real Python source file that calls the affected API, and generates the code
patch automatically -- the step that was done by hand in examples/example_patch.py.

How it finds the call to patch, without being told where it is:
  1. Parse the source file's AST.
  2. For every function, find calls shaped like `requests.<method>(url, ...)`.
  3. Resolve the URL (an f-string, inline or assigned to a variable first) into
     a "path shape" -- literal segments kept, every `{...}` placeholder
     collapsed to `{}` -- and do the same to every OpenAPI path in the diff
     report. Comparing shapes instead of literal paths means the function's
     own parameter names (`owner`, `repo`, ...) don't need to match the
     spec's.
  4. Method + path-shape match -> this function calls this endpoint.

What it patches automatically (request-side, BREAKING, kind
request_body_field_now_required): adds the newly-required field(s) to the
function signature (no default -- a missing value should be a loud
TypeError at the call site, not a silent bug) and to the outgoing JSON
payload dict, exactly like the hand-built example in examples/example_patch.py.

What it flags but does NOT try to rewrite (response-side findings, and
request-side kinds it doesn't have a safe automatic rewrite for yet --
endpoint_removed, method_removed, parameter_removed, parameter_type_changed):
a comment block is inserted at the top of the function body citing the
exact findings, so a human (or a later pass) knows exactly what to check.
Rewriting arbitrary response-field-access call sites safely requires
knowing every place the return value is read, which is outside what a
single function's source can tell us -- honest scope, not attempted here.

Safety: never edits a file in place. Writes '<name>_patched.py' next to the
original, or prints a unified diff to stdout if --write isn't passed.
"""
import argparse
import ast
import difflib
import json
import re
import sys

REQUEST_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}

AUTO_PATCHABLE_REQUEST_KINDS = {"request_body_field_now_required"}
FLAGGED_REQUEST_KINDS = {
    "endpoint_removed", "method_removed", "parameter_removed",
    "parameter_now_required", "parameter_type_changed",
}


# ---------------------------------------------------------------------------
# Path-shape matching
# ---------------------------------------------------------------------------

def joinedstr_shape(node):
    """An f-string AST node, or a plain string constant -> a string with every
    {expr} collapsed to {} (a plain constant has none, and passes through as-is)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        else:
            parts.append("{}")
    return "".join(parts)


def url_shape_to_path_shape(url_shape):
    """Strip scheme+host from a full-URL shape, leaving just the path shape."""
    if "://" in url_shape:
        after_scheme = url_shape.split("://", 1)[1]
        path = "/" + after_scheme.split("/", 1)[1] if "/" in after_scheme else "/"
    else:
        path = url_shape
    return path.rstrip("/") or "/"


def spec_path_to_shape(spec_path):
    """An OpenAPI path template ('/repos/{owner}/{repo}') -> the same shape form."""
    return re.sub(r"\{[^}]*\}", "{}", spec_path).rstrip("/") or "/"


# ---------------------------------------------------------------------------
# Finding a function's requests.<method>(url, ...) call
# ---------------------------------------------------------------------------

class CallSite:
    def __init__(self, funcdef, call_node, method, path_shape, json_arg_node):
        self.funcdef = funcdef
        self.call_node = call_node
        self.method = method
        self.path_shape = path_shape
        self.json_arg_node = json_arg_node  # ast.Dict node for the JSON payload, or None


def find_call_sites(tree):
    """Walk every function in the module, return a CallSite for each requests.* call found."""
    sites = []
    for funcdef in ast.walk(tree):
        if not isinstance(funcdef, ast.FunctionDef):
            continue

        # Map local variable name -> the JoinedStr (f-string) AST node assigned to it,
        # and separately -> a Dict node, so `url = f"..."` / `payload = {...}` can be
        # resolved when referenced later by name in the requests.* call.
        str_vars = {}
        dict_vars = {}
        for node in ast.walk(funcdef):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if isinstance(node.value, ast.JoinedStr) or (
                    isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                ):
                    str_vars[name] = node.value
                elif isinstance(node.value, ast.Dict):
                    dict_vars[name] = node.value

        for node in ast.walk(funcdef):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in REQUEST_METHODS):
                continue
            if not (isinstance(func.value, ast.Name)):
                continue  # only handle `requests.post(...)` / `session.post(...)` style

            if not node.args:
                continue
            url_arg = node.args[0]
            if isinstance(url_arg, ast.JoinedStr) or (
                isinstance(url_arg, ast.Constant) and isinstance(url_arg.value, str)
            ):
                url_node = url_arg
            elif isinstance(url_arg, ast.Name) and url_arg.id in str_vars:
                url_node = str_vars[url_arg.id]
            else:
                continue  # can't resolve this URL statically -- skip, don't guess

            path_shape = url_shape_to_path_shape(joinedstr_shape(url_node))

            json_node = None
            for kw in node.keywords:
                if kw.arg == "json":
                    if isinstance(kw.value, ast.Dict):
                        json_node = kw.value
                    elif isinstance(kw.value, ast.Name) and kw.value.id in dict_vars:
                        json_node = dict_vars[kw.value.id]

            sites.append(CallSite(funcdef, node, func.attr.upper(), path_shape, json_node))
    return sites


# ---------------------------------------------------------------------------
# Matching findings to call sites
# ---------------------------------------------------------------------------

def load_findings(*report_paths):
    findings = []
    for p in report_paths:
        if p is None:
            continue
        with open(p) as f:
            findings.extend(json.load(f))
    return findings


def findings_for_site(site, findings):
    matched = []
    for f in findings:
        if f.get("method") != site.method:
            continue
        if spec_path_to_shape(f.get("path", "")) != site.path_shape:
            continue
        matched.append(f)
    return matched


def field_name(finding):
    """diff_engine.py's request_body_field_now_required findings carry the field
    name only inside the human-readable 'detail' string -- pull it back out."""
    if "field" in finding:
        return finding["field"]
    m = re.search(r"field '([^']+)'", finding.get("detail", ""))
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Applying the patch as text edits (keeps original formatting/comments intact
# everywhere except the lines actually being changed)
# ---------------------------------------------------------------------------

def indent_of_line(line):
    return line[:len(line) - len(line.lstrip(" "))]


def apply_patches(source, sites_with_findings):
    """sites_with_findings: list of (CallSite, auto_findings, flagged_findings).
    Returns (patched_source, changelog_lines)."""
    lines = source.splitlines(keepends=True)
    changelog = []

    # Collect all text insertions as (lineno, col, text) then apply bottom-to-top
    # so earlier edits don't shift the offsets later edits depend on.
    insertions = []

    for site, auto_findings, flagged_findings in sites_with_findings:
        funcdef = site.funcdef
        new_fields = []
        for finding in auto_findings:
            name = field_name(finding)
            if name and name.isidentifier() and name not in new_fields:
                new_fields.append(name)
        new_fields.sort()  # deterministic output regardless of set-iteration order upstream

        if new_fields and funcdef.args.args:
            last_arg = funcdef.args.args[-1]
            sig_text = "".join(f", {n}" for n in new_fields)
            insertions.append((last_arg.end_lineno, last_arg.end_col_offset, sig_text))
            changelog.append(
                f"{funcdef.name}(): added required parameter(s) {', '.join(new_fields)} "
                f"to signature (was optional, now required per spec)."
            )

        if new_fields and site.json_arg_node is not None:
            dict_node = site.json_arg_node
            close_line = dict_node.end_lineno
            existing_line = lines[close_line - 1]
            base_indent = indent_of_line(existing_line)
            # match the indent of an existing key if there is one, else fall back
            if dict_node.keys:
                first_key = dict_node.keys[0]
                key_line = lines[first_key.lineno - 1]
                entry_indent = indent_of_line(key_line)
            else:
                entry_indent = base_indent + "    "
            entry_text = "".join(
                f'{entry_indent}"{n}": {n},  # auto-patched by Tremor: now required\n'
                for n in new_fields
            )
            # Insert at column 0 of the closing-brace line, i.e. *before* that
            # line's own indentation -- so the brace's original indent stays
            # intact as a suffix rather than getting glued onto our new text.
            insertions.append((close_line, 0, entry_text))
            changelog.append(
                f"{funcdef.name}(): added {', '.join(new_fields)} to the outgoing JSON payload."
            )

        if flagged_findings:
            body_start = funcdef.body[0]
            indent = indent_of_line(lines[body_start.lineno - 1])
            comment_lines = [
                f"{indent}# --- Tremor: drift detected, needs manual review ---\n"
            ]
            for f in flagged_findings:
                comment_lines.append(f"{indent}# [{f['severity']}] {f.get('detail', f.get('kind'))}\n")
            comment_lines.append(f"{indent}# ---------------------------------------------------\n")
            insertions.append((body_start.lineno, 0, "".join(comment_lines)))
            changelog.append(
                f"{funcdef.name}(): flagged {len(flagged_findings)} finding(s) needing manual review "
                f"(not auto-patchable)."
            )

    insertions.sort(key=lambda t: (t[0], t[1]), reverse=True)
    for lineno, col, text in insertions:
        line = lines[lineno - 1]
        lines[lineno - 1] = line[:col] + text + line[col:]

    return "".join(lines), changelog


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(source_path, diff_path, response_diff_path, write):
    with open(source_path) as f:
        source = f.read()
    tree = ast.parse(source, filename=source_path)

    findings = load_findings(diff_path, response_diff_path)
    sites = find_call_sites(tree)

    if not sites:
        print("No requests.<method>(url, ...) calls with a statically-resolvable URL "
              "were found in this file -- nothing to match against the diff report.")
        return

    plan = []
    for site in sites:
        matched = findings_for_site(site, findings)
        auto = [f for f in matched if f["kind"] in AUTO_PATCHABLE_REQUEST_KINDS]
        flagged = [f for f in matched if f["kind"] in FLAGGED_REQUEST_KINDS
                   or f["kind"].startswith("response_field")]
        if auto or flagged:
            plan.append((site, auto, flagged))

    if not plan:
        print(f"Matched {len(sites)} API call(s) in {source_path} against "
              f"{len(findings)} findings -- none of them affect this file. Nothing to patch.")
        return

    patched_source, changelog = apply_patches(source, plan)

    print(f"Matched {len(sites)} API call(s) in {source_path}; "
          f"{len(plan)} of them are affected by the diff report.\n")
    print("Changelog:")
    for line in changelog:
        print(f"  - {line}")
    print()

    diff = difflib.unified_diff(
        source.splitlines(keepends=True),
        patched_source.splitlines(keepends=True),
        fromfile=source_path,
        tofile=source_path.replace(".py", "_patched.py"),
    )
    print("".join(diff))

    if write:
        out_path = source_path.replace(".py", "_patched.py")
        with open(out_path, "w") as f:
            f.write(patched_source)
        print(f"\nPatched file written to {out_path} (original left untouched).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Python source file to patch")
    parser.add_argument("--diff", default="reports/diff_report.json",
                         help="request-side diff report (from diff_engine.py)")
    parser.add_argument("--response-diff", default="reports/response_diff_report.json",
                         help="response-side diff report (from response_diff.py)")
    parser.add_argument("--write", action="store_true",
                         help="write <source>_patched.py instead of just printing the diff")
    args = parser.parse_args()
    run(args.source, args.diff, args.response_diff, args.write)
