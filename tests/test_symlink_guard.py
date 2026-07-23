"""--fix and the mypy shadow pass must never write through a symlinked test.

Discovery lists symlinked test *files*; a plain write follows the link and
can clobber a file outside the scanned tree (unrecoverable — git status was
clean). The fixer must refuse. Covers the Python and TS CLIs.

Stdlib only — run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "skills", "captain-obvious", "scripts")
PY_CLI = os.path.join(SCRIPTS, "captain_obvious_py.py")
TS_CLI = os.path.join(SCRIPTS, "captain_obvious_ts.mjs")
NODE = shutil.which("node")

SKIP_MSG = "symlinked test files are never rewritten"

PY_TARGET = "def test_dead():\n    assert True\n"
TS_TARGET = 'test("truth", () => {\n  expect(true).toBe(true);\n});\n'


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
class PySymlinkGuard(unittest.TestCase):
    def _fixture(self):
        d = tempfile.mkdtemp(prefix="capobv-symp-")
        outside = tempfile.mkdtemp(prefix="capobv-symp-out-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        target = os.path.join(outside, "target.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(PY_TARGET)
        os.symlink(target, os.path.join(d, "test_link.py"))
        return d, target

    def test_py_fix_skips_symlink(self):
        d, target = self._fixture()
        proc = subprocess.run([sys.executable, PY_CLI, "--path", d,
                               "--no-types", "--fix", "--force"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(SKIP_MSG, proc.stderr)
        self.assertEqual(open(target, encoding="utf-8").read(), PY_TARGET)


@unittest.skipUnless(hasattr(os, "symlink") and _ts_resolvable(),
                     "symlinks or node+typescript unavailable")
class TsSymlinkGuard(unittest.TestCase):
    def _fixture(self):
        d = tempfile.mkdtemp(prefix="capobv-symt-")
        outside = tempfile.mkdtemp(prefix="capobv-symt-out-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        target = os.path.join(outside, "lit.test.ts")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(TS_TARGET)
        os.symlink(target, os.path.join(d, "link.test.ts"))
        return d, target

    def test_ts_fix_skips_symlink(self):
        d, target = self._fixture()
        proc = subprocess.run([NODE, TS_CLI, "--project", d, "--fix", "--force"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(SKIP_MSG, proc.stderr)
        self.assertEqual(open(target, encoding="utf-8").read(), TS_TARGET)


if __name__ == "__main__":
    unittest.main()
