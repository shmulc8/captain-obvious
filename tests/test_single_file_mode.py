"""Tests for --file single-file mode (the write-time prevention scan path).

Single-file mode must be a pure parse: no discovery, no gitguard, no mypy
subprocess, no _cap_obv_shadow_* files — and must never crash on content an
agent is mid-writing (syntax errors, empty files).

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

TAUTOLOGY = '''\
def test_math_still_works():
    assert 1 == 1
'''

PROBE_ONLY = '''\
from app import fetch

def test_fetch():
    r = fetch()
    assert isinstance(r, dict)
'''

BROKEN = '''\
def test_unfinished(:
    assert
'''


class SingleFileMode(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-single-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def scan(self, content, name="test_x.py", stdin=True, extra_env=None):
        path = os.path.join(self.dir, name)
        argv = [sys.executable, CLI, "--file", path]
        if stdin:
            argv.append("--stdin")
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        env = dict(os.environ, **(extra_env or {}))
        proc = subprocess.run(argv, input=content if stdin else None,
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_tautology_is_proven_via_stdin(self):
        report = self.scan(TAUTOLOGY)
        self.assertEqual(report["mode"], "single-file")
        cats = {f["category"]: f["level"] for f in report["findings"]}
        self.assertEqual(cats.get("constant-assert"), "proven")

    def test_type_probes_never_promote_without_mypy(self):
        """isinstance probes must resolve through the no-types path — no
        type-guaranteed findings, no subprocess."""
        report = self.scan(PROBE_ONLY)
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn("type-guaranteed", cats)

    def test_syntax_error_is_fail_soft(self):
        report = self.scan(BROKEN)
        self.assertIn("parse failed", report["note"])
        self.assertEqual(report["findings"], [])

    def test_no_shadow_files_written(self):
        self.scan(PROBE_ONLY, stdin=False)
        leftovers = [f for f in os.listdir(self.dir) if f.startswith("_cap_obv_shadow_")]
        self.assertEqual(leftovers, [])

    def test_runs_without_mypy_on_path(self):
        """PATH without mypy/uv must not matter — single-file mode spawns nothing."""
        report = self.scan(TAUTOLOGY, extra_env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(len(report["findings"]), 1)

    def test_same_file_duplicates_still_detected(self):
        dup = ('def test_a():\n    assert compute("value-payload") == "expected-result"\n\n'
               'def test_b():\n    assert compute("value-payload") == "expected-result"\n')
        report = self.scan("from app import compute\n\n" + dup)
        dups = [f for f in report["findings"] if f["category"] == "duplicate-test"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["level"], "proven")

    def test_fix_is_rejected(self):
        proc = subprocess.run([sys.executable, CLI, "--file", "x.py", "--fix"],
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
