"""Characterization tests for the partial-removal path of Python --fix.

No test in tests/ exercised assertionsToRemove > 0 before this file. Here a
test keeps one real assert while a proven boundary-tautology is removed, and
the orphaned `resp = compute()` binding is rewritten to a bare `compute()`
call (the _dangling_edits path).

Stdlib only — run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "skills", "captain-obvious", "scripts", "captain_obvious_py.py")

TEST_SRC = '''\
from app import compute

def test_mixed():
    result = compute()
    resp = compute()
    assert len(resp) >= 0
    assert result == "expected"
'''


class FixerPartial(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-partial-")
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

    def test_plan_removes_one_assert_keeps_test(self):
        plan = self.report()["plan"]
        self.assertEqual(plan["assertionsToRemove"], 1)
        self.assertEqual(plan["testsToRemove"], [])

    def test_fix_keeps_test_rewrites_dangling(self):
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--fix", "--force",
                        "--no-types"],
                       capture_output=True, text=True, check=True)
        src = open(self.test_file, encoding="utf-8").read()
        self.assertIn("def test_mixed", src)
        self.assertIn('assert result == "expected"', src)
        self.assertNotIn("len(resp)", src)
        self.assertNotIn("resp =", src)
        # dangling binding collapsed to a bare call, not deleted outright
        self.assertRegex(src, re.compile(r"^\s*compute\(\)\s*$", re.M))

    def test_rewritten_file_reparses(self):
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--fix", "--force",
                        "--no-types"],
                       capture_output=True, text=True, check=True)
        ast.parse(open(self.test_file, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
