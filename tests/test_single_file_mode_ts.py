"""Tests for the TS scanner's --file single-file mode.

Skipped when node or the typescript package is unavailable (CI installs
typescript at the repo root; the scanner's fallback import resolves it from
the script's own directory upward).

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
CLI = os.path.join(SCRIPTS, "captain_obvious_ts.mjs")

NODE = shutil.which("node")


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


HAVE_TS = _ts_resolvable()

TAUTOLOGY = '''\
import { test, expect } from "vitest";
const x = computeThing();
test("x equals itself", () => {
  expect(x).toBe(x);
});
'''

BROKEN = '''\
test("unfinished", () => {
  expect(1).toBe(
'''


@unittest.skipUnless(HAVE_TS, "node + typescript not available")
class TsSingleFileMode(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-ts-single-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def scan(self, content, name="example.test.ts"):
        path = os.path.join(self.dir, name)
        proc = subprocess.run([NODE, CLI, "--file", path, "--stdin"],
                              input=content, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_tautology_is_proven_via_stdin(self):
        report = self.scan(TAUTOLOGY)
        self.assertEqual(report["mode"], "single-file")
        self.assertFalse(report["typeChecksEnabled"])
        cats = {f["category"]: f["level"] for f in report["findings"]}
        self.assertEqual(cats.get("constant-assert"), "proven")

    def test_broken_syntax_is_fail_soft(self):
        """The TS parser is tolerant — mid-write garbage must not crash."""
        report = self.scan(BROKEN)
        self.assertEqual(report["testsScanned"], 1)

    def test_fix_is_rejected(self):
        proc = subprocess.run([NODE, CLI, "--file", "x.test.ts", "--fix"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
