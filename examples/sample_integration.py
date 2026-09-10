"""
A stand-in for a real customer codebase file -- written independently against
the OLD (v1.0.0) GitHub API spec, with no knowledge of what changed later.
Used to test patch_generator.py end-to-end: can it find this function, match
it to the right diff findings, and patch it automatically, with no hints
about which function or which endpoint to look at?
"""
import requests


def add_pr_review_comment(owner, repo, pull_number, body, path, position, token):
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


def get_meta(token):
    """Read GitHub's /meta endpoint."""
    url = "https://api.github.com/meta"
    resp = requests.get(url, headers={"Authorization": f"token {token}"})
    resp.raise_for_status()
    return resp.json()
