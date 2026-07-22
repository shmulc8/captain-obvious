#!/usr/bin/env python3
"""captain-obvious write-time prevention hook (PreToolUse on Write/Edit).

Blocks a Write or Edit that would introduce PROVEN can-never-fail test
patterns, with a reason the agent can act on immediately — so a dead test
is never written instead of being cleaned up later.

Design rules:
- fail open, always: a broken scanner, missing runtime, timeout, or any
  unexpected input must never block a write
- proven findings only: the syntactic single-file scan cannot produce
  type-guaranteed findings, so everything it proves is true by construction
- newly-introduced only: findings already present in the file's previous
  content never block an unrelated edit
- modes via CAPTAIN_OBVIOUS_HOOK: block (default) | warn | off
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, os.pardir, "skills", "captain-obvious", "scripts")
PY_CLI = os.path.join(SCRIPTS, "captain_obvious_py.py")
TS_CLI = os.path.join(SCRIPTS, "captain_obvious_ts.mjs")

# mirror the scanners' own discovery rules
PY_TEST_RE = re.compile(r"(^|[\\/])(test_[^\\/]*\.py|[^\\/]*_test\.py)$")
TS_TEST_RE = re.compile(r"\.(test|spec)\.(ts|tsx|mts|cts)$|[\\/]__tests__[\\/][^\\/]*\.(ts|tsx)$")
SHADOW_PREFIX = "_cap_obv_shadow_"
MAX_BYTES = 1_000_000
SCAN_TIMEOUT = 5
MAX_LISTED = 10


def scan(path: str, content: str) -> dict | None:
    """Single-file syntactic scan; None means 'no information — fail open'."""
    if path.endswith(".py"):
        cmd = [sys.executable, PY_CLI, "--file", path, "--stdin"]
    else:
        node = shutil.which("node")
        if not node:
            return None
        cmd = [node, TS_CLI, "--file", path, "--stdin"]
    proc = subprocess.run(cmd, input=content, capture_output=True, text=True,
                          timeout=SCAN_TIMEOUT)
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout)


def proven_findings(report: dict) -> list[dict]:
    return [f for f in report.get("findings", []) if f.get("level") == "proven"]


def compose_new_content(tool: str, ti: dict, old: str) -> str | None:
    """Apply the pending tool call to the old content; None = fail open
    (the tool itself will surface the real error)."""
    if tool == "Write":
        return ti.get("content")
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    if not old or not isinstance(edits, list):
        return None
    new = old
    for e in edits:
        o, n = e.get("old_string"), e.get("new_string")
        if not o or n is None or o not in new:
            return None
        new = new.replace(o, n) if e.get("replace_all") else new.replace(o, n, 1)
    return new


def main() -> None:
    data = json.load(sys.stdin)
    mode = os.environ.get("CAPTAIN_OBVIOUS_HOOK", "block").strip().lower()
    if mode not in ("block", "warn"):
        return

    ti = data.get("tool_input") or {}
    tool = data.get("tool_name")
    path = ti.get("file_path") or ""
    if os.path.basename(path).startswith(SHADOW_PREFIX):
        return
    if not (PY_TEST_RE.search(path) or TS_TEST_RE.search(path)):
        return  # fast path: not a test file

    old = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    new = compose_new_content(tool, ti, old)
    if new is None or len(new) > MAX_BYTES or len(old) > MAX_BYTES:
        return

    report = scan(path, new)
    if report is None:
        return
    fresh = proven_findings(report)
    if fresh and old:
        old_report = scan(path, old)
        if old_report is None:
            return
        # line numbers shift across edits — key on (category, test name)
        seen = {(f["category"], f["test"]) for f in proven_findings(old_report)}
        fresh = [f for f in fresh if (f["category"], f["test"]) not in seen]
    if not fresh:
        return

    lines = [f'  - "{f["test"]}" line {f["line"]} [{f["category"]}]: {f["reason"]}'
             for f in fresh[:MAX_LISTED]]
    if len(fresh) > MAX_LISTED:
        lines.append(f"  ... and {len(fresh) - MAX_LISTED} more")
    reason = (
        f"captain-obvious: this {tool} introduces {len(fresh)} test(s) that can never fail:\n"
        + "\n".join(lines)
        + "\nRewrite them to assert real behavior of the code under test — do not re-add the "
          "flagged assertions, and do not delete unrelated tests to work around this. "
          "(Humans: set CAPTAIN_OBVIOUS_HOOK=warn or =off to downgrade this check.)"
    )
    if mode == "warn":
        print(json.dumps({"systemMessage": reason.replace(
            "captain-obvious:", "captain-obvious (warn):", 1)}))
    else:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: never block a write because the guard itself broke
    sys.exit(0)
