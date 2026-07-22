"""Tests for the Python report's plan block (dry-run parity with the TS scanner).

The TS report has always carried plan.testsToRemove so an agent can preview
exactly what plain --fix will delete; the Python report only had per-category
counts, mixing advisories (never deleted) with proven items (deleted).

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
from app import compute

def test_duplicate_one():
    assert compute("payload-value-here") == "expected-result-here"

def test_duplicate_two():
    assert compute("payload-value-here") == "expected-result-here"

def test_real():
    assert compute("other-payload") == "other-result"
'''


class FixPlanParity(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-plan-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.test_file = os.path.join(self.dir, "test_app.py")
        with open(self.test_file, "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)

    def report(self, *extra):
        out = os.path.join(self.dir, "report.json")
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--json", out,
                        "--no-types", *extra],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)

    def test_report_only_carries_the_plan(self):
        plan = self.report()["plan"]
        self.assertEqual([t["test"] for t in plan["testsToRemove"]],
                         ["test_duplicate_two"])
        self.assertEqual(plan["testsToRemove"][0]["file"], "test_app.py")

    def test_fix_deletes_exactly_the_planned_set(self):
        plan = self.report()["plan"]
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--fix", "--force"],
                       capture_output=True, text=True, check=True)
        src = open(self.test_file, encoding="utf-8").read()
        for t in plan["testsToRemove"]:
            self.assertNotIn(t["test"], src)
        self.assertIn("test_duplicate_one", src)
        self.assertIn("test_real", src)


if __name__ == "__main__":
    unittest.main()
