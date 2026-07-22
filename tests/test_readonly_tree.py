"""Regression test: a report-only scan on a read-only tree must degrade, not crash.

The mypy pass writes _cap_obv_shadow_* files next to test files. On a
read-only checkout that write raises OSError; before the fix it propagated
uncaught, killing the whole scan (no JSON, no syntactic findings) — despite
SKILL.md promising graceful degradation to the syntactic categories.

Stdlib only — run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "skills", "captain-obvious", "scripts", "captain_obvious_py.py")

TEST_SRC = '''\
def get():
    return {"a": 1}

def test_probe_and_tautology():
    r = get()
    assert isinstance(r, dict)
    assert True
'''


class ReadOnlyTreeDegrades(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-ro-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.project = os.path.join(self.dir, "proj")
        os.makedirs(self.project)
        with open(os.path.join(self.project, "test_ro.py"), "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)
        os.chmod(self.project, stat.S_IRUSR | stat.S_IXUSR)  # read-only dir
        self.addCleanup(os.chmod, self.project, stat.S_IRWXU)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory permissions")
    def test_scan_degrades_to_syntactic_with_note(self):
        out = os.path.join(self.dir, "report.json")
        proc = subprocess.run([sys.executable, CLI, "--path", self.project, "--json", out],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         f"scan crashed on a read-only tree:\n{proc.stderr}")
        with open(out, encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertIn("shadow", (report["mypyNote"] or ""))
        cats = [f["category"] for f in report["findings"]]
        self.assertIn("constant-assert", cats,
                      "syntactic findings were lost in the degradation")


if __name__ == "__main__":
    unittest.main()
