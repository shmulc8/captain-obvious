"""Regression tests for mypy-pass failure handling and shadow-file hygiene.

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
SCRIPTS = os.path.join(REPO, "skills", "captain-obvious", "scripts")
CLI = os.path.join(SCRIPTS, "captain_obvious_py.py")

sys.path.insert(0, SCRIPTS)
from co_py.discovery import find_test_files, SHADOW_PREFIX  # noqa: E402

TEST_SRC = '''\
def get_name() -> str:
    return "x"


def test_name_is_str():
    n = get_name()
    assert isinstance(n, str)
'''

FAKE_MYPY_EXIT_2 = '''\
import sys
sys.stderr.write("mypy.ini: [mypy]: Unrecognized option: nonsense = 1\\n")
sys.exit(2)
'''

FAKE_MYPY_NO_MODULE = '''\
import sys
sys.stderr.write("/path/to/python: No module named mypy\\n")
sys.exit(1)
'''


class MypyFailureIsReported(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-mypy-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        with open(os.path.join(self.dir, "test_types.py"), "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)
        self.fake_mypy = os.path.join(self.dir, "fake_mypy.py")
        with open(self.fake_mypy, "w", encoding="utf-8") as fh:
            fh.write(FAKE_MYPY_EXIT_2)

    def test_exit_code_2_surfaces_a_note(self):
        """mypy that runs but exits >=2 must not silently drop the
        type-guaranteed category — the probes all keep revealed=None and get
        reclassified as nonredundant, so the user needs to be told."""
        out = os.path.join(self.dir, "report.json")
        subprocess.run(
            [sys.executable, CLI, "--path", self.dir, "--json", out,
             "--mypy", f"{sys.executable} {self.fake_mypy}"],
            capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            note = json.load(fh)["mypyNote"]
        self.assertIsNotNone(note, "mypy exited 2 but no note was reported")
        self.assertIn("mypy failed", note)

    def test_no_mypy_module_found_exits_1_surfaces_a_note(self):
        """If sys.executable -m mypy exits with 1 but stderr says No module named mypy,
        it must show that mypy is not runnable."""
        fake_missing = os.path.join(self.dir, "fake_missing.py")
        with open(fake_missing, "w", encoding="utf-8") as fh:
            fh.write(FAKE_MYPY_NO_MODULE)
        out = os.path.join(self.dir, "report.json")
        subprocess.run(
            [sys.executable, CLI, "--path", self.dir, "--json", out,
             "--mypy", f"{sys.executable} {fake_missing}"],
            capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            note = json.load(fh)["mypyNote"]
        self.assertIsNotNone(note, "mypy was not found but no note was reported")
        self.assertIn("mypy not runnable", note)


class ShadowFilesAreNotTests(unittest.TestCase):
    def test_stranded_shadow_file_is_not_collected(self):
        """A shadow copy stranded by a SIGKILL is named
        _cap_obv_shadow_<orig>; when <orig> ends in _test.py the stranded file
        matches the discovery glob and would be scanned as a real test."""
        d = tempfile.mkdtemp(prefix="capobv-shadow-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        real = os.path.join(d, "thing_test.py")
        stranded = os.path.join(d, f"{SHADOW_PREFIX}thing_test.py")
        for p in (real, stranded):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(TEST_SRC)
        found = find_test_files(d)
        self.assertIn(real, found)
        self.assertNotIn(stranded, found)


if __name__ == "__main__":
    unittest.main()
