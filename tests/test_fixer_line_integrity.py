"""Regression tests for two Python --fix line-integrity bugs.

(a) Control-byte desync: str.splitlines() splits on \\f (and other bytes) that
    ast does not count as line terminators, so ast line numbers index the wrong
    entries and --fix deletes the wrong lines.
(b) CRLF destruction: text-mode read/write rewrites CRLF files as all-LF.

Both classes FAIL on unmodified code (the bugs reproduce) and pass after the
fix. Stdlib only — run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "skills", "captain-obvious", "scripts", "captain_obvious_py.py")

# The \f lives inside a string literal in test_keep, above the deletion target.
CTRL_SRC = 'def test_keep():\n    x = "a\fb"\n    assert x\n\ndef test_dead():\n    assert True\n'


class ControlByteDesync(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-ctrl-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.test_file = os.path.join(self.dir, "test_app.py")
        with open(self.test_file, "w", encoding="utf-8", newline="") as fh:
            fh.write(CTRL_SRC)

    def test_deletes_the_right_lines(self):
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--fix", "--force",
                        "--no-types"],
                       capture_output=True, text=True, check=True)
        src = open(self.test_file, encoding="utf-8", newline="").read()
        self.assertNotIn("def test_dead", src)
        self.assertNotIn("assert True", src)
        self.assertIn("def test_keep", src)
        self.assertIn('x = "a\fb"', src)
        ast.parse(src)


class CRLFPreservation(unittest.TestCase):
    LINES = [
        "from app import compute",
        "",
        "def test_duplicate_one():",
        '    assert compute("payload-value-here") == "expected-result-here"',
        "",
        "def test_duplicate_two():",
        '    assert compute("payload-value-here") == "expected-result-here"',
        "",
        "def test_real():",
        '    assert compute("other-payload") == "other-result"',
        "",
    ]

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-crlf-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.test_file = os.path.join(self.dir, "test_app.py")
        with open(self.test_file, "w", encoding="utf-8", newline="") as fh:
            fh.write("\r\n".join(self.LINES))

    def test_crlf_survives_fix(self):
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--fix", "--force",
                        "--no-types"],
                       capture_output=True, text=True, check=True)
        content = open(self.test_file, encoding="utf-8", newline="").read()
        self.assertIn("\r\n", content)
        self.assertEqual(content.count("\n"), content.count("\r\n"))
        self.assertNotIn("test_duplicate_two", content)
        self.assertIn("test_duplicate_one", content)


if __name__ == "__main__":
    unittest.main()
