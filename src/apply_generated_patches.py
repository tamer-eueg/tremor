#!/usr/bin/env python3
"""Apply only patches generated during the current clean GitHub Actions run.

This prepares reviewed source changes in the ephemeral runner working tree. It
does not commit, push, open, approve, or merge anything.
"""
from __future__ import annotations

import os
import subprocess
import sys


def git(repo_root, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=False, check=check
    )


def inside(root, path):
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def discover_new_patches(repo_root, patches_dir):
    repo_root = os.path.abspath(repo_root)
    patches_dir = os.path.abspath(patches_dir)
    if not inside(repo_root, patches_dir):
        raise ValueError("patches directory must stay inside the repository")
    relative_dir = os.path.relpath(patches_dir, repo_root)
    result = git(
        repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        "--", relative_dir,
    )
    patches = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        decoded = record.decode("utf-8", errors="strict")
        if len(decoded) < 4:
            continue
        status, rel_path = decoded[:2], decoded[3:]
        if status == "!!" or not rel_path.endswith(".patch"):
            continue
        absolute = os.path.abspath(os.path.join(repo_root, rel_path))
        if inside(patches_dir, absolute):
            patches.append(rel_path)
    return sorted(set(patches))


def apply_patches(repo_root, patch_paths):
    applied = []
    try:
        for patch_path in patch_paths:
            check = git(repo_root, "apply", "--check", "--", patch_path, check=False)
            if check.returncode:
                message = check.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"patch does not apply cleanly: {patch_path}: {message}")
            result = git(repo_root, "apply", "--", patch_path, check=False)
            if result.returncode:
                message = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"could not apply patch: {patch_path}: {message}")
            applied.append(patch_path)
    except Exception:
        for patch_path in reversed(applied):
            git(repo_root, "apply", "--reverse", "--", patch_path, check=False)
        raise
    return len(applied)


def main():
    repo_root = os.path.abspath(os.environ.get("TREMOR_REPO_ROOT", os.getcwd()))
    patches_dir = os.path.abspath(os.environ.get(
        "TREMOR_PATCHES_DIR", os.path.join(repo_root, ".tremor", "runs", "patches")
    ))
    try:
        patches = discover_new_patches(repo_root, patches_dir)
        count = apply_patches(repo_root, patches)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Tremor could not prepare review patches: {exc}", file=sys.stderr)
        return 2
    print(count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
