"""Regression tests: coverage mode must warn when it cannot see test files.

Findings are keyed by test-file lines. Standard coverage configs measure
only src/, so every lookup misses and the mode silently reports a
reassuring "0 confirmed rotten". That silence must become a warning.

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

TEST_SRC = '''\
import sys
from app import compute

def test_conditional():
    result = compute()
    if sys.platform == "win32":
        assert result == 5
'''
ASSERT_LINE = 7

SRC_ONLY_COVERAGE = {"files": {"app.py": {"executed_lines": [1, 2], "missing_lines": []}}}


class CoverageInertWarning(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-cov-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        with open(os.path.join(self.dir, "test_app.py"), "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)

    def report(self, coverage_data):
        cov_path = os.path.join(self.dir, "coverage.json")
        with open(cov_path, "w", encoding="utf-8") as fh:
            json.dump(coverage_data, fh)
        out = os.path.join(self.dir, "report.json")
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--json", out,
                        "--no-types", "--coverage", cov_path],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)

    def test_src_only_coverage_warns(self):
        report = self.report(SRC_ONLY_COVERAGE)
        warn = report["coverage"]["warning"]
        self.assertIsNotNone(warn, "src-only coverage silently confirmed nothing")
        self.assertIn("test_app.py", warn)
        self.assertEqual(report["coverage"]["conditionalAssertsPromoted"], 0)

    def test_full_coverage_promotes_without_warning(self):
        """Control: when test-file lines are present, promotion works and no
        warning is emitted."""
        full = {"files": {"test_app.py": {
            "executed_lines": [1, 2, 4, 5, 6], "missing_lines": [ASSERT_LINE]}}}
        report = self.report(full)
        self.assertIsNone(report["coverage"]["warning"])
        self.assertEqual(report["coverage"]["conditionalAssertsPromoted"], 1)
        cats = {f["category"]: f["level"] for f in report["findings"]}
        self.assertEqual(cats.get("conditional-assert"), "proven")


if __name__ == "__main__":
    unittest.main()
