"""
Worked example: what the auto-patcher produces for one real breaking change
detected between GitHub REST API spec v1.0.0 and v2.1.0.

Detected change:
  POST /repos/{owner}/{repo}/pulls/{pull_number}/comments
  Request body fields 'line' and 'commit_id' went from optional to REQUIRED.

Effect on an existing integration: any code written against the old spec
that omits these fields used to work and now gets a 422 from GitHub with
no warning until it happens in production.
"""

# ============================================================
# BEFORE — integration code as it would have been written
# against the OLD (v1.0.0) spec. Compiles fine, worked fine,
# and will start failing the moment GitHub's new requirement
# ships, with zero warning at the code level.
# ============================================================

import requests


def add_pr_review_comment_OLD(owner, repo, pull_number, body, path, position, token):
    """Add a review comment to a pull request."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/comments"
    payload = {
        "body": body,
        "path": path,
        "position": position,
    }
    resp = requests.post(url, json=payload, headers={"Authorization": f"token {token}"})
    resp.raise_for_status()
    return resp.json()


# ============================================================
# AFTER — patch generated automatically from the diff_engine
# finding. The generator:
#   1. reads the diff_report.json entry for this endpoint
#   2. adds the newly-required fields to the function signature
#      (no default, so a missing value is a clear TypeError at
#      the call site instead of a confusing 422 from GitHub)
#   3. adds them to the request payload
#   4. leaves everything else untouched
# ============================================================

def add_pr_review_comment_PATCHED(owner, repo, pull_number, body, path, position, token,
                                   commit_id, line):
    """Add a review comment to a pull request.

    Auto-patched: GitHub's API now requires 'commit_id' and 'line' in the
    request body (previously optional). See diff_report.json entry for
    POST /repos/{owner}/{repo}/pulls/{pull_number}/comments.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/comments"
    payload = {
        "body": body,
        "path": path,
        "position": position,
        "commit_id": commit_id,   # <-- added by auto-patch (now required)
        "line": line,             # <-- added by auto-patch (now required)
    }
    resp = requests.post(url, json=payload, headers={"Authorization": f"token {token}"})
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    import inspect
    print("OLD signature:    ", inspect.signature(add_pr_review_comment_OLD))
    print("PATCHED signature:", inspect.signature(add_pr_review_comment_PATCHED))
    print()
    print("Calling OLD code against the NEW API would now raise a 422 from GitHub")
    print("at request time, with no clue in the code why. Calling PATCHED code")
    print("either works, or raises a clear Python TypeError at the call site if")
    print("commit_id/line aren't supplied -- caught in development, not production.")
