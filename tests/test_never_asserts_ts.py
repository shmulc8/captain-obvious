"""Regression tests for the TS-side never-asserts whole-test removal guard.

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


FIXTURE = '''
function doWork() {
  return 1;
}

test("test_unreachable_assert_with_live_call", () => {
  doWork();
  return;
  expect(false).toBe(true);
});

test("test_swallowed_everything", () => {
  try {
    const r = doWork();
    expect(r).toBe(2);
  } catch (e) {
    console.log(e);
  }
});

test("test_swallowed_and_truly_inert", () => {
  try {
    expect(1).toBe(2);
  } catch (e) {
    console.log(e);
  }
});

test("test_unreachable_call_after_return", () => {
  return;
  doWork();
  expect(false).toBe(true);
});

test("test_logger_noise_call", () => {
  console.log("x");
  return;
  expect(false).toBe(true);
});

test("test_call_inside_assertion_argument", () => {
  expect.assertions(doWork());
  return;
  expect(1).toBe(2);
});

test("test_silent_smoke_with_call", () => {
  try {
    doWork();
  } catch (e) {
    // silent catch
  }
});

test("test_silent_smoke_empty", () => {
});
'''


@unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
class NeverAssertsTsGuard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-ts-")
        self.test_file = os.path.join(self.dir, "smoke.test.ts")
        with open(self.test_file, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _scan(self):
        out = os.path.join(self.dir, "report.json")
        subprocess.run([NODE, CLI, "--project", self.dir, "--json", out],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            report = json.load(fh)
        return {f["test"]: f for f in report["findings"]}

    def test_unguarded_call_makes_it_advisory(self):
        found = self._scan()["test_unreachable_assert_with_live_call"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "advisory")
        self.assertEqual(found["deletable"], "report-only")

    def test_swallowed_everything_stays_proven(self):
        found = self._scan()["test_swallowed_everything"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_truly_inert_test_stays_proven(self):
        found = self._scan()["test_swallowed_and_truly_inert"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_unreachable_call_after_return_stays_proven(self):
        found = self._scan()["test_unreachable_call_after_return"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_logger_noise_call_stays_proven(self):
        found = self._scan()["test_logger_noise_call"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_call_inside_assertion_argument_makes_it_advisory(self):
        found = self._scan()["test_call_inside_assertion_argument"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "advisory")
        self.assertEqual(found["deletable"], "report-only")

    def test_silent_smoke_with_call_is_proven(self):
        found = self._scan()["test_silent_smoke_with_call"]
        self.assertEqual(found["category"], "silent-smoke")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_silent_smoke_empty_is_proven(self):
        found = self._scan()["test_silent_smoke_empty"]
        self.assertEqual(found["category"], "silent-smoke")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_fix_keeps_only_the_test_that_can_still_fail(self):
        for args in (["init", "-q", "."], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "init"]):
            subprocess.run(["git"] + args, cwd=self.dir, capture_output=True, text=True)
        subprocess.run([NODE, CLI, "--project", self.dir, "--fix"],
                       capture_output=True, text=True, check=True)
        with open(self.test_file, encoding="utf-8") as fh:
            remaining = fh.read()
        self.assertIn("test_unreachable_assert_with_live_call", remaining)
        self.assertIn("test_call_inside_assertion_argument", remaining)
        self.assertNotIn("test_swallowed_everything", remaining)
        self.assertNotIn("test_swallowed_and_truly_inert", remaining)
        self.assertNotIn("test_unreachable_call_after_return", remaining)
        self.assertNotIn("test_logger_noise_call", remaining)
        self.assertNotIn("test_silent_smoke_with_call", remaining)
        self.assertNotIn("test_silent_smoke_empty", remaining)


if __name__ == "__main__":
    unittest.main()
