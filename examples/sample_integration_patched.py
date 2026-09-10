"""
A stand-in for a real customer codebase file -- written independently against
the OLD (v1.0.0) GitHub API spec, with no knowledge of what changed later.
Used to test patch_generator.py end-to-end: can it find this function, match
it to the right diff findings, and patch it automatically, with no hints
about which function or which endpoint to look at?
"""
import requests


def add_pr_review_comment(owner, repo, pull_number, body, path, position, token, commit_id, line):
    """Add a review comment to a pull request."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/comments"
    payload = {
        "body": body,
        "path": path,
        "position": position,
        "commit_id": commit_id,  # auto-patched by Tremor: now required
        "line": line,  # auto-patched by Tremor: now required
    }
    resp = requests.post(url, json=payload, headers={"Authorization": f"token {token}"})
    resp.raise_for_status()
    return resp.json()


def get_meta(token):
    # --- Tremor: drift detected, needs manual review ---
    # [BREAKING] GET /meta: Response field 'github_services_sha' no longer appears in the response body.
    # [BREAKING] GET /meta: Response field 'installed_version' no longer appears in the response body.
    # ---------------------------------------------------
    """Read GitHub's /meta endpoint."""
    url = "https://api.github.com/meta"
    resp = requests.get(url, headers={"Authorization": f"token {token}"})
    resp.raise_for_status()
    return resp.json()
