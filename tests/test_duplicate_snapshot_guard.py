"""Regression tests: duplicate-test must not flag snapshot/baseline tests.

Snapshot tests (syrupy, pytest-regressions, approvaltests) have identical
bodies by design — each is keyed to a distinct stored baseline by test name.
Auto-deleting an apparent duplicate orphans its baseline, and suites running
with unused-snapshot checks then fail.

Stdlib only — run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "skills", "captain-obvious", "scripts", "captain_obvious_py.py")

SNAPSHOT_TESTS = '''\
from app import render_a, render_b

def test_render_a(snapshot):
    assert render_a("fixture-payload-value") == snapshot

def test_render_b(snapshot):
    assert render_a("fixture-payload-value") == snapshot
'''

PLAIN_DUPLICATES = '''\
from app import render_a

def test_render_first():
    assert render_a("fixture-payload-value") == "expected-rendered-output"

def test_render_second():
    assert render_a("fixture-payload-value") == "expected-rendered-output"
'''


class DuplicateSnapshotGuard(unittest.TestCase):
    def scan(self, test_src):
        d = tempfile.mkdtemp(prefix="capobv-dup-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "test_render.py"), "w", encoding="utf-8") as fh:
            fh.write(test_src)
        out = os.path.join(d, "report.json")
        subprocess.run([sys.executable, CLI, "--path", d, "--json", out, "--no-types"],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)["findings"]

    def test_identical_snapshot_tests_are_not_duplicates(self):
        findings = self.scan(SNAPSHOT_TESTS)
        self.assertEqual([f for f in findings if f["category"] == "duplicate-test"], [],
                         "snapshot tests with identical bodies were flagged as duplicates")

    def test_identical_plain_tests_are_still_duplicates(self):
        """Control: the guard must not swallow genuine duplicates."""
        dups = [f for f in self.scan(PLAIN_DUPLICATES) if f["category"] == "duplicate-test"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["level"], "proven")


if __name__ == "__main__":
    unittest.main()
