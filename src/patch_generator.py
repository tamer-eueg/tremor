#!/usr/bin/env python3
"""
Automated patch generation (phase 3).

Takes diff findings (from diff_engine.py / response_diff.py's output) and a
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

What it patches automatically:
  - request_body_field_now_required -- a field in the JSON body went from
    optional to required. Added to the function signature (no default -- a
    missing value should be a loud TypeError at the call site, not a silent
    422) and to the `json=` dict.
  - parameter_now_required, when the parameter lives in the query string or
    a header -- same treatment, targeting the `params=` or `headers=` dict.
    (A "required" *path* parameter isn't something OpenAPI allows to be
    optional in the first place, so that combination shouldn't occur; if it
    somehow does, or the location is a cookie, it's flagged rather than
    guessed at.)

Either kind only gets auto-patched at a given call site if that call
actually passes a literal dict for the relevant keyword (json=/params=/
headers=) -- if the code builds it some other way (e.g. a query string
concatenated by hand), the finding is flagged instead of guessed at.

What it flags but does NOT try to rewrite (response-side findings, and
request-side kinds it doesn't have a safe automatic rewrite for --
endpoint_removed, method_removed, parameter_removed, parameter_type_changed,
plus any auto-patchable-in-principle finding whose call site doesn't have a
matching dict to edit): a comment block is inserted at the top of the
function body citing the exact findings, so a human (or a later pass) knows
exactly what to check. Rewriting arbitrary response-field-access call sites
safely requires knowing every place the return value is read, which is
outside what a single function's source can tell us -- honest scope, not
attempted here.

Safety: never edits a file in place. Writes '<name>_patched.py' next to the
original, or prints a unified diff to stdout if --write isn't passed.
"""
import argparse
import ast
import difflib
import json
import re
from collections import defaultdict

REQUEST_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
DICT_KWARGS = ("json", "params", "headers")

AUTO_PATCHABLE_BODY_KIND = "request_body_field_now_required"
AUTO_PATCHABLE_PARAM_KIND = "parameter_now_required"
PARAM_LOC_TO_KWARG = {"query": "params", "header": "headers"}

# Every kind this tool knows how to talk about at all -- auto-patched when
# possible, otherwise surfaced as a flagged, cite-the-spec comment.
TRACKED_REQUEST_KINDS = {
    AUTO_PATCHABLE_BODY_KIND, AUTO_PATCHABLE_PARAM_KIND,
    "endpoint_removed", "method_removed", "parameter_removed", "parameter_type_changed",
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
    def __init__(self, funcdef, call_node, method, path_shape, kwarg_dicts):
        self.funcdef = funcdef
        self.call_node = call_node
        self.method = method
        self.path_shape = path_shape
        self.kwarg_dicts = kwarg_dicts  # {"json"/"params"/"headers": ast.Dict node or None}


def find_call_sites(tree):
    """Walk every function in the module, return a CallSite for each requests.* call found."""
    sites = []
    for funcdef in ast.walk(tree):
        if not isinstance(funcdef, ast.FunctionDef):
            continue

        # Map local variable name -> the JoinedStr/string-constant AST node assigned to
        # it, and separately -> a Dict node, so `url = f"..."` / `payload = {...}` can
        # be resolved when referenced later by name in the requests.* call.
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
            if not isinstance(func.value, ast.Name):
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

            kwarg_dicts = {k: None for k in DICT_KWARGS}
            for kw in node.keywords:
                if kw.arg not in DICT_KWARGS:
                    continue
                if isinstance(kw.value, ast.Dict):
                    kwarg_dicts[kw.arg] = kw.value
                elif isinstance(kw.value, ast.Name) and kw.value.id in dict_vars:
                    kwarg_dicts[kw.arg] = dict_vars[kw.value.id]

            sites.append(CallSite(funcdef, node, func.attr.upper(), path_shape, kwarg_dicts))
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
    """request_body_field_now_required findings: prefer the explicit 'field' key
    (added once this tool needed it); fall back to parsing the old detail string
    for reports generated before that key existed."""
    if "field" in finding:
        return finding["field"]
    m = re.search(r"field '([^']+)'", finding.get("detail", ""))
    return m.group(1) if m else None


def param_name_and_loc(finding):
    """parameter_* findings: prefer the explicit 'param'/'in' keys; fall back to
    parsing "Parameter 'name' (loc)" out of detail for older reports."""
    if "param" in finding and "in" in finding:
        return finding["param"], finding["in"]
    m = re.search(r"Parameter '([^']+)' \(([^)]+)\)", finding.get("detail", ""))
    return (m.group(1), m.group(2)) if m else (None, None)


def classify_finding(finding, site):
    """Returns (target_kwarg, field_name) if this finding can be auto-patched at
    this call site, or (None, None) if it can't (wrong kind, no matching dict
    argument to edit, or an unhandled parameter location)."""
    kind = finding.get("kind")
    if kind == AUTO_PATCHABLE_BODY_KIND:
        name = field_name(finding)
        if name and name.isidentifier() and site.kwarg_dicts.get("json") is not None:
            return "json", name
    elif kind == AUTO_PATCHABLE_PARAM_KIND:
        name, loc = param_name_and_loc(finding)
        kwarg = PARAM_LOC_TO_KWARG.get(loc)
        if kwarg and name and name.isidentifier() and site.kwarg_dicts.get(kwarg) is not None:
            return kwarg, name
    return None, None


# ---------------------------------------------------------------------------
# Applying the patch as text edits (keeps original formatting/comments intact
# everywhere except the lines actually being changed)
# ---------------------------------------------------------------------------

def indent_of_line(line):
    return line[:len(line) - len(line.lstrip(" "))]


def close_brace_alone_on_line(line):
    """True if this line contains nothing but the dict's closing brace (and
    maybe a trailing comma) -- i.e. it's safe to insert whole new lines above
    it without disturbing anything else on that line."""
    return line.strip() in ("}", "},")


def apply_patches(source, sites_with_findings):
    """sites_with_findings: list of (CallSite, {kwarg: [new field names]}, flagged_findings).
    Returns (patched_source, changelog_lines)."""
    lines = source.splitlines(keepends=True)
    changelog = []

    # Collect all text insertions as (lineno, col, text) then apply bottom-to-top
    # so earlier edits don't shift the offsets later edits depend on.
    insertions = []

    for site, auto_by_kwarg, flagged_findings in sites_with_findings:
        funcdef = site.funcdef

        all_new_names = sorted({n for names in auto_by_kwarg.values() for n in names})
        if all_new_names and funcdef.args.args:
            last_arg = funcdef.args.args[-1]
            sig_text = "".join(f", {n}" for n in all_new_names)
            insertions.append((last_arg.end_lineno, last_arg.end_col_offset, sig_text))
            changelog.append(
                f"{funcdef.name}(): added required parameter(s) {', '.join(all_new_names)} "
                f"to signature (was optional, now required per spec)."
            )

        for kwarg, names in auto_by_kwarg.items():
            if not names:
                continue
            names_sorted = sorted(set(names))
            dict_node = site.kwarg_dicts[kwarg]
            close_line_no = dict_node.end_lineno
            close_line_text = lines[close_line_no - 1]
            dict_label = "JSON payload" if kwarg == "json" else f"'{kwarg}' dict"

            if dict_node.keys and close_brace_alone_on_line(close_line_text):
                # Multi-line dict, one key per line, closing brace alone on its own
                # line -- insert clean new lines above it, matching the existing
                # entries' indentation, each with an explanatory comment. This is
                # the common, readable case (matches examples/example_patch.py's style).
                first_key = dict_node.keys[0]
                entry_indent = indent_of_line(lines[first_key.lineno - 1])
                entry_text = "".join(
                    f'{entry_indent}"{n}": {n},  # auto-patched by Tremor: now required\n'
                    for n in names_sorted
                )
                insertions.append((close_line_no, 0, entry_text))
            else:
                # Empty dict (`{}`) or a single-line dict sharing its line with other
                # code (e.g. `params={}, data=data,`) -- inserting whole new lines
                # here would land outside the dict's braces and break syntax. Insert
                # inline, right before the closing brace, with no comment (a comment
                # would swallow whatever follows on the same line).
                close_col = dict_node.end_col_offset - 1  # column of the '}' itself
                prefix = ", " if dict_node.keys else ""
                entry_text = prefix + ", ".join(f'"{n}": {n}' for n in names_sorted)
                insertions.append((close_line_no, close_col, entry_text))

            changelog.append(
                f"{funcdef.name}(): added {', '.join(names_sorted)} to the outgoing {dict_label}."
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
        auto_by_kwarg = defaultdict(list)
        flagged = []
        for f in matched:
            kwarg, name = classify_finding(f, site)
            if kwarg:
                if name not in auto_by_kwarg[kwarg]:
                    auto_by_kwarg[kwarg].append(name)
            elif f.get("kind") in TRACKED_REQUEST_KINDS or f.get("kind", "").startswith("response_field"):
                flagged.append(f)
        if auto_by_kwarg or flagged:
            plan.append((site, dict(auto_by_kwarg), flagged))

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
