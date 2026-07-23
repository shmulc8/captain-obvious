"""Python auto-delete detectors: mock-echo, boundary-tautology, local-const-echo.

These all emit deletable:safe on the proven side, so --fix removes them with
no human in the loop, yet none had a self-test. One fixture file, one method
per case, asserting the (category, level, deletable) verdict contract.

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
from unittest.mock import MagicMock
from app import compute

def test_boundary():
    data = compute()
    assert len(data) >= 0

def test_const_echo():
    x = 5
    assert x == 5

def test_mock_echo_direct():
    m = MagicMock()
    m.return_value = 5
    assert m() == 5

def test_mock_echo_indirect():
    m = MagicMock()
    m.return_value = 5
    result = compute(m)
    assert result == 5
'''


class PythonProvenDetectors(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-prov-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        with open(os.path.join(self.dir, "test_app.py"), "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)
        out = os.path.join(self.dir, "report.json")
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--json", out, "--no-types"],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            self.findings = json.load(fh)["findings"]
        self.by_test = {f["test"]: f for f in self.findings}

    def verdict(self, test):
        f = self.by_test[test]
        return (f["category"], f["level"], f["deletable"])

    def test_boundary_tautology(self):
        self.assertEqual(self.verdict("test_boundary"),
                         ("boundary-tautology", "proven", "safe"))

    def test_local_const_echo(self):
        self.assertEqual(self.verdict("test_const_echo"),
                         ("local-const-echo", "proven", "safe"))

    def test_mock_echo_direct(self):
        self.assertEqual(self.verdict("test_mock_echo_direct"),
                         ("mock-echo", "proven", "safe"))

    def test_mock_echo_indirect_stays_advisory(self):
        self.assertEqual(self.verdict("test_mock_echo_indirect"),
                         ("mock-echo", "advisory", "report-only"))


if __name__ == "__main__":
    unittest.main()
