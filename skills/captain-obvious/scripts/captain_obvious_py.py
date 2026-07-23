#!/usr/bin/env python3
"""
captain-obvious — Python detector

Deterministically finds pytest tests that can never fail or check nothing,
and optionally deletes them.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

from co_py.discovery import find_test_files
from co_py.analyzer import analyze_file
from co_py.models import Probe, TestRecord
from co_py.mypy_pass import run_mypy_probes, resolve_probes
from co_py.duplicates import mark_duplicates
from co_py.coverage import load_coverage
from co_py.fixer import apply_fix, plan_removals
from co_py.gitguard import fix_blocker


def single_file(args) -> int:
    """Syntactic-only scan of one file, JSON to stdout.

    Built for write-time hooks: no discovery, no gitguard, no mypy, no
    subprocess, no shadow files — a pure parse of the given content.
    """
    path = os.path.abspath(args.file)
    root = os.path.dirname(path)
    report = {"tool": "captain-obvious/py", "file": path, "mode": "single-file",
              "mypyNote": "type checks skipped (single-file mode is syntactic only)",
              "note": None, "testsScanned": 0, "findings": [], "summary": {}}
    try:
        src = sys.stdin.read() if args.stdin else open(path, encoding="utf-8").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as e:
        report["note"] = f"parse failed — skipped ({e})"
        print(json.dumps(report, indent=2))
        return 0

    probes: list[Probe] = []
    records: list[TestRecord] = []
    analyze_file(path, src, tree, root, probes, records)
    for p in probes:
        p.revealed = None            # the existing --no-types degradation path
    resolve_probes(probes, records, root)
    mark_duplicates(records)

    findings = [f for r in records for f in r.findings]
    for f in findings:
        report["summary"].setdefault(f.category, {"proven": 0, "advisory": 0})[f.level] += 1
    report["testsScanned"] = len(records)
    report["findings"] = [f.to_dict(root) for f in findings]
    print(json.dumps(report, indent=2))
    return 0


def main():
    if sys.version_info < (3, 9):
        print("captain-obvious: requires Python 3.9+ (this is Python "
              f"{sys.version_info.major}.{sys.version_info.minor}; ast.unparse is missing below 3.9)",
              file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=".")
    ap.add_argument("--file", help="scan a single file (syntactic categories only; "
                                   "JSON to stdout; no mypy, no shadow files)")
    ap.add_argument("--stdin", action="store_true",
                    help="with --file: read the file's content from stdin "
                         "(the path is used for naming only)")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--mypy", help='mypy command, e.g. "uv run mypy"')
    ap.add_argument("--no-types", action="store_true", help="skip the mypy pass")
    ap.add_argument("--coverage", help="coverage file (coverage.py json / lcov / istanbul json): "
                                       "confirm conditional-assert findings against real line coverage")
    ap.add_argument("--force", action="store_true",
                    help="allow --fix on a dirty or non-git tree (no undo path)")
    args = ap.parse_args()

    if args.stdin and not args.file:
        ap.error("--stdin requires --file")
    if args.file:
        if args.fix:
            ap.error("--fix is not supported with --file (single-file mode is report-only)")
        return single_file(args)

    root = os.path.abspath(args.path)

    if args.fix and not args.force:
        blocker = fix_blocker(root)
        if blocker:
            print(f"captain-obvious: refusing to --fix — {blocker}.\n"
                  "  --fix rewrites test files in place with no backup. Commit or stash\n"
                  "  first so `git checkout -- <files>` can undo it, or pass --force.",
                  file=sys.stderr)
            return 2
    files = find_test_files(root)
    if not files:
        print(f"captain-obvious: no test files (test_*.py / *_test.py) under {root}")
        return 0

    probes: list[Probe] = []
    records: list[TestRecord] = []
    for f in files:
        try:
            src = open(f, encoding="utf-8").read()
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"  skipping {f}: {e}", file=sys.stderr)
            continue
        analyze_file(f, src, tree, root, probes, records)

    mypy_note = None
    laundering: set[str] = set()
    laundering_visible = True
    if args.no_types:
        for p in probes:
            p.revealed = None
        mypy_note = "type checks skipped (--no-types)"
    else:
        mypy_note, laundering, laundering_visible = run_mypy_probes(
            probes, root, args.mypy.split() if args.mypy else None)
    resolve_probes(probes, records, root, laundering, laundering_visible)

    mark_duplicates(records)

    findings = [f for r in records for f in r.findings]

    # coverage confirmation: turn the static conditional-assert guess into a fact
    cov = load_coverage(args.coverage, root) if args.coverage else None
    cov_promoted, cov_suppressed = 0, 0
    cov_note = None
    cov_warn = None
    if args.coverage and cov is None:
        cov_note = "could not parse coverage (expected coverage.py json / lcov / istanbul json)"
    elif cov is not None:
        covered_files = {f for f, _ in cov}
        inert_files: set[str] = set()
        kept = []
        for f in findings:
            if f.category == "conditional-assert":
                rel = os.path.relpath(f.file, root).replace(os.sep, "/")
                if rel not in covered_files:
                    inert_files.add(rel)
                hits = cov.get((rel, f.line))
                if hits == 0:
                    f.level = "proven"
                    f.reason += (" — coverage confirms it ran 0 times: rotten (ICSE'19). "
                                 "Fix the guard so it fires, or remove it")
                    cov_promoted += 1
                elif hits is not None and hits > 0:
                    cov_suppressed += 1
                    continue  # demonstrably executes — not rotten, drop it
            kept.append(f)
        findings = kept
        if inert_files:
            # coverage configs usually measure only src/ — then test-file lines
            # are absent and this whole mode silently confirms nothing
            cov_warn = (f"coverage data has no lines for {len(inert_files)} test file(s) "
                        f"({', '.join(sorted(inert_files)[:3])}...) — coverage mode is inert "
                        "for them; include test files in coverage collection "
                        "(e.g. run coverage over the whole repo, not just src/)")

    summary: dict[str, dict[str, int]] = {}
    for f in findings:
        summary.setdefault(f.category, {"proven": 0, "advisory": 0})[f.level] += 1

    _, plan = plan_removals(records, root)
    fixed = apply_fix(records, root) if args.fix else None

    report = {
        "tool": "captain-obvious/py",
        "project": root,
        "mypyNote": mypy_note,
        "testFilesScanned": len(files),
        "testsScanned": len(records),
        "findings": [f.to_dict(root) for f in findings],
        "summary": summary,
        "coverage": ({"file": args.coverage, "conditionalAssertsPromoted": cov_promoted,
                      "conditionalAssertsSuppressed": cov_suppressed,
                      "warning": cov_warn} if cov is not None
                     else ({"file": args.coverage, "error": cov_note} if args.coverage else None)),
        "plan": plan,
        "fixed": fixed,
    }
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)

    print(f"\ncaptain-obvious (py) — {len(records)} tests scanned in {len(files)} files")
    if mypy_note:
        print(f"  note: {mypy_note}")
    print()
    for cat, c in summary.items():
        print(f"  {cat:<20} proven: {c['proven']}  advisory: {c['advisory']}")
    if plan["testsToRemove"] or plan["assertionsToRemove"]:
        print(f"\n  tests fully removable: {len(plan['testsToRemove'])}")
        print(f"  individual assertions removable: {plan['assertionsToRemove']}")
    if cov is not None:
        print(f"  coverage: {cov_promoted} conditional-assert(s) confirmed rotten, "
              f"{cov_suppressed} confirmed reached (dropped)")
        if cov_warn:
            print(f"  coverage warning: {cov_warn}")
    elif cov_note:
        print(f"  coverage: {cov_note}")
    if findings:
        print("\nFindings:")
        for f in findings:
            tag = "PROVEN  " if f.level == "proven" else "ADVISORY"
            print(f"  [{tag}] {os.path.relpath(f.file, root)}:{f.line} ({f.category}) \"{f.test}\"")
            print(f"             {f.reason}")
    if fixed:
        print(f"\nFixed: removed {fixed['testsRemoved']} tests and {fixed['assertionsRemoved']} assertions "
              f"across {fixed['filesChanged']} files.")
        print("Re-run your typechecker and test suite now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
