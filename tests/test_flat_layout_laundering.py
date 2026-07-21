"""Regression tests for the Any-laundering guard on flat / namespace layouts.

The laundering guard depends on mypy seeing the source tree ([no-any-return]
is only reported for explicitly-listed targets). Before this fix, a flat
layout (client.py next to test_client.py, no src/, no package dir) produced
src_targets == [], the guard silently disabled, and `assert isinstance(r, dict)`
on an Any-laundering result was classified proven/safe and auto-deleted.

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

LAUNDERING_SRC = '''\
import json

def fetch() -> dict:
    return json.loads('{"a": 1}')
'''

SOLID_SRC = '''\
def fetch() -> dict:
    return {"a": 1}
'''

TEST_SRC = '''\
from {mod} import fetch

def test_fetch_returns_dict():
    r = fetch()
    assert isinstance(r, dict)
'''

# Deterministic stand-in for mypy. Mirrors the behavior the real tool has:
# reveal_type() notes for shadow files it is given, and [no-any-return] ONLY
# for source files explicitly listed in argv (never for unlisted files).
FAKE_MYPY = '''\
import sys

targets = [a for a in sys.argv[1:] if not a.startswith("-")]
for t in targets:
    base = t.replace("\\\\", "/").rsplit("/", 1)[-1]
    if base.startswith("_cap_obv_shadow_"):
        with open(t, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if "reveal_type((" in line:
                    print(f'{t}:{i}: note: Revealed type is "builtins.dict[builtins.str, builtins.int]"')
    elif base == "client.py":
        with open(t, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if "return " in line:
                    print(f'{t}:{i}: error: Returning Any from function declared '
                          f'to return "dict"  [no-any-return]')
sys.exit(0)
'''


class FlatLayoutLaundering(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-flat-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.fake_mypy = os.path.join(self.dir, "fake_mypy_helper.txt")
        with open(self.fake_mypy, "w", encoding="utf-8") as fh:
            fh.write(FAKE_MYPY)

    def scan(self, project):
        out = os.path.join(self.dir, "report.json")
        subprocess.run(
            [sys.executable, CLI, "--path", project, "--json", out,
             "--mypy", f"{sys.executable} {self.fake_mypy}"],
            capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)

    def write(self, project, rel, content):
        path = os.path.join(project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_flat_layout_laundering_source_suppresses_finding(self):
        """Flat layout: client.py must be handed to mypy so [no-any-return]
        marks fetch() as laundering and the isinstance probe is cleared."""
        project = os.path.join(self.dir, "flat")
        self.write(project, "client.py", LAUNDERING_SRC)
        self.write(project, "test_client.py", TEST_SRC.format(mod="client"))
        report = self.scan(project)
        cats = [f["category"] for f in report["findings"]]
        self.assertNotIn("type-guaranteed", cats,
                         "isinstance on an Any-laundering result was flagged — "
                         "flat-layout source was not visible to mypy")

    def test_flat_layout_solid_source_still_flags_proven(self):
        """Control: same flat layout with a genuinely-typed source must still
        produce the proven type-guaranteed finding (probes are alive)."""
        project = os.path.join(self.dir, "solid")
        self.write(project, "solid.py", SOLID_SRC)
        self.write(project, "test_solid.py", TEST_SRC.format(mod="solid"))
        report = self.scan(project)
        tg = [f for f in report["findings"] if f["category"] == "type-guaranteed"]
        self.assertEqual(len(tg), 1)
        self.assertEqual(tg[0]["level"], "proven")

    def test_no_source_targets_demotes_and_notes(self):
        """Namespace layout (no __init__.py, no top-level modules): the guard
        cannot run, so type-guaranteed findings must be demoted to advisory
        and a note must say why."""
        project = os.path.join(self.dir, "ns")
        self.write(project, os.path.join("pkg", "mod.py"), LAUNDERING_SRC)
        self.write(project, "test_mod.py", TEST_SRC.format(mod="pkg.mod"))
        report = self.scan(project)
        tg = [f for f in report["findings"] if f["category"] == "type-guaranteed"]
        self.assertEqual(len(tg), 1)
        self.assertEqual(tg[0]["level"], "advisory")
        self.assertEqual(tg[0]["deletable"], "report-only")
        self.assertIn("laundering guard", report["mypyNote"] or "")


if __name__ == "__main__":
    unittest.main()
