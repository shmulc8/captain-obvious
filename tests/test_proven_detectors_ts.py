"""TS syntactic auto-delete detectors + mock-echo + same-file duplicate.

No tsconfig on purpose — these fire from syntax alone, which also locks that
they don't quietly depend on type info. markDuplicates on the TS side was
only ever exercised through the Python CLI; the trailing duplicate pair pins
it end-to-end through the TS CLI.

Stdlib only — run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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


TEST_SRC = '''\
import { compute } from "./app";

test("boundary", () => {
  const arr: number[] = [];
  expect(arr.length).toBeGreaterThanOrEqual(0);
});
test("const echo", () => {
  const expected = 5;
  expect(expected).toBe(5);
});
test("mock echo stub", () => {
  const m = jest.fn();
  m.mockReturnValue(5);
  expect(m()).toBe(5);
});
test("mock echo called", () => {
  const m = jest.fn();
  m();
  expect(m).toHaveBeenCalled();
});
test("dup one", () => {
  expect(compute("a")).toEqual("b");
});
test("dup two", () => {
  expect(compute("a")).toEqual("b");
});
'''


@unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
class TsProvenDetectors(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-ts-prov-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        with open(os.path.join(self.dir, "prov.test.ts"), "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)
        out = os.path.join(self.dir, "report.json")
        subprocess.run([NODE, CLI, "--project", self.dir, "--json", out],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            self.findings = json.load(fh)["findings"]
        self.by_test = {f["test"]: f for f in self.findings}

    def verdict(self, test):
        f = self.by_test[test]
        return (f["category"], f["level"], f["deletable"])

    def test_boundary_tautology(self):
        self.assertEqual(self.verdict("boundary"),
                         ("boundary-tautology", "proven", "safe"))

    def test_local_const_echo(self):
        self.assertEqual(self.verdict("const echo"),
                         ("local-const-echo", "proven", "safe"))

    def test_mock_echo_stub(self):
        self.assertEqual(self.verdict("mock echo stub"),
                         ("mock-echo", "proven", "safe"))

    def test_mock_echo_called(self):
        self.assertEqual(self.verdict("mock echo called"),
                         ("mock-echo", "proven", "safe"))

    def test_same_file_duplicate(self):
        # the second identical body in the same suite is a proven-safe duplicate
        self.assertEqual(self.verdict("dup two"),
                         ("duplicate-test", "proven", "safe"))


if __name__ == "__main__":
    unittest.main()
