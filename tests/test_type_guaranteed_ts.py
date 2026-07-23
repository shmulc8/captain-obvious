"""TS type-guaranteed classifier: proven verdict + its over-deletion guards.

Pins the compiler-driven "ask the checker" path and the three guards that
keep --fix from auto-deleting real tests:
  - `as` cast in the operand suppresses the finding entirely
  - a property from an index signature stays advisory (the type is a promise)
  - unchecked indexed access stays advisory (the type can lie)

Requires a tsconfig with strict:true so typeChecksEnabled is on — otherwise
the whole fixture silently degrades to syntactic-only and proves nothing.

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


TSCONFIG = '{ "compilerOptions": { "strict": true, "target": "es2020", "module": "esnext" } }'

TEST_SRC = '''\
interface Bag { [k: string]: string }
declare function getCount(): number;

test("tg proven", () => {
  const n = getCount();
  expect(typeof n).toBe("number");
});
test("tg cast guard", () => {
  expect(typeof (getCount() as any)).toBe("number");
});
test("tg index signature", () => {
  const b: Bag = { x: "1" };
  expect(b.x).toBeDefined();
});
test("tg element access", () => {
  const arr: string[] = ["a"];
  expect(arr[0]).toBeDefined();
});
'''


@unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
class TypeGuaranteedVerdicts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-tg-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        with open(os.path.join(self.dir, "tsconfig.json"), "w", encoding="utf-8") as fh:
            fh.write(TSCONFIG)
        with open(os.path.join(self.dir, "types.test.ts"), "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)
        out = os.path.join(self.dir, "report.json")
        subprocess.run([NODE, CLI, "--project", self.dir, "--json", out],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            self.report = json.load(fh)
        self.by_test = {f["test"]: f for f in self.report["findings"]}

    def test_type_checks_are_enabled(self):
        # If this is false the tsconfig wasn't discovered and every other
        # assertion here is vacuous — fix the fixture, not the scanner.
        self.assertTrue(self.report["typeChecksEnabled"],
                        "tsconfig not discovered — fixture degraded to syntactic-only")

    def test_proven_typeof(self):
        f = self.by_test["tg proven"]
        self.assertEqual((f["category"], f["level"], f["deletable"]),
                         ("type-guaranteed", "proven", "safe"))

    def test_cast_suppresses_finding(self):
        # the `as any` cast must make the classifier bail out entirely
        self.assertNotIn("tg cast guard", self.by_test)

    def test_index_signature_stays_advisory(self):
        f = self.by_test["tg index signature"]
        self.assertEqual((f["category"], f["level"]), ("type-guaranteed", "advisory"))
        self.assertIn("index signature", f["reason"])

    def test_element_access_stays_advisory(self):
        f = self.by_test["tg element access"]
        self.assertEqual((f["category"], f["level"]), ("type-guaranteed", "advisory"))
        self.assertIn("ndexed access", f["reason"])


if __name__ == "__main__":
    unittest.main()
