"""unittest.TestCase assert-methods get redundancy findings — report-only.

self.assertTrue(True) / assertEqual(1, 1) / assertEqual(x, x) map to the same
proven constant-assert the bare-assert form would, but this stage forces
deletable="report-only" so --fix never removes an Expr(Call) statement (no
safe-removal support for those yet).

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

SUFFIX = "(unittest assert-method — auto-fix not yet supported)"
TEST_SRC = '''\
import unittest
from app import compute

class TestApp(unittest.TestCase):
    def test_const(self):
        self.assertTrue(True)

    def test_eq_literal(self):
        self.assertEqual(1, 1)

    def test_self_eq(self):
        x = compute()
        self.assertEqual(x, x)

    def test_real(self):
        self.assertEqual(compute(), "expected")
'''


class UnittestAsserts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-ut-")
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

    def by_test(self):
        return {f["test"]: f for f in self.report()["findings"]}

    def test_const_is_report_only_constant_assert(self):
        f = self.by_test()["test_const"]
        self.assertEqual(f["category"], "constant-assert")
        self.assertEqual(f["level"], "proven")
        self.assertEqual(f["deletable"], "report-only")
        self.assertTrue(f["reason"].endswith(SUFFIX), f["reason"])

    def test_eq_literal_is_report_only(self):
        f = self.by_test()["test_eq_literal"]
        self.assertEqual(f["category"], "constant-assert")
        self.assertEqual(f["level"], "proven")
        self.assertEqual(f["deletable"], "report-only")

    def test_self_eq_is_report_only(self):
        f = self.by_test()["test_self_eq"]
        self.assertEqual(f["category"], "constant-assert")
        self.assertEqual(f["deletable"], "report-only")

    def test_real_has_no_finding(self):
        self.assertNotIn("test_real", self.by_test())

    def test_plan_is_empty(self):
        plan = self.report()["plan"]
        self.assertEqual(plan["testsToRemove"], [])
        self.assertEqual(plan["assertionsToRemove"], 0)

    def test_fix_is_inert(self):
        before = open(self.test_file, "rb").read()
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--fix", "--force",
                        "--no-types"],
                       capture_output=True, text=True, check=True)
        after = open(self.test_file, "rb").read()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
