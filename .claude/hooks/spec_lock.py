#!/usr/bin/env python3
"""PreToolUse hook: deny Edit/Write/MultiEdit on the spec unless the task unlocked it.

Locked: spec/** and .github/workflows/spec.yml. Unlocked while a .spec-unlock file
exists at the repository root (gitignored, so each checkout unlocks on its own).
See docs/TESTING.md, "Spec lock".
"""
import json
import os
import sys

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LOCKED_DIRS = ("spec",)
LOCKED_FILES = (".github/workflows/spec.yml",)
UNLOCK_FILE = ".spec-unlock"


def locked_path(file_path, cwd):
    """Repo-relative path if file_path is locked, else None. Case-insensitive, for macOS."""
    full = os.path.realpath(os.path.join(cwd, file_path))
    rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        return None
    low = rel.lower()
    if low in LOCKED_FILES or any(low == d or low.startswith(d + "/") for d in LOCKED_DIRS):
        return rel
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path:
        return 0
    rel = locked_path(file_path, payload.get("cwd") or os.getcwd())
    if rel is None or os.path.exists(os.path.join(ROOT, UNLOCK_FILE)):
        return 0
    reason = (f"The spec is locked: {rel} is part of the checked spec (spec/** and "
              ".github/workflows/spec.yml). A task may unlock it only when its instructions "
              "explicitly say so; see docs/TESTING.md, \"Spec lock\".")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
