"""TS Any-laundering guard must be transitive across return-call chains.

`inner()` launders an `any` (`JSON.parse(...) as Foo`); `outer()` returns
`inner()`. A type-guaranteed assertion on `outer()`'s result is REAL coverage,
so it must not be proven/safe (auto-deleted). The Python side already
propagates laundering transitively; this pins TS parity.

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

TSCONFIG = '{ "compilerOptions": { "strict": true, "target": "es2020" } }'
TEST_SRC = '''\
interface Foo { a: number }
function inner(): Foo {
  return JSON.parse("{}") as Foo;
}
function outer(): Foo {
  return inner();
}
test("direct launder", () => {
  const d = inner();
  expect(d).toBeDefined();
});
test("indirect launder", () => {
  const v = outer();
  expect(v).toBeDefined();
});
'''


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


@unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
class LaunderingTransitive(unittest.TestCase):
    def _findings(self):
        d = tempfile.mkdtemp(prefix="capobv-launder-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "tsconfig.json"), "w", encoding="utf-8") as fh:
            fh.write(TSCONFIG)
        with open(os.path.join(d, "launder.test.ts"), "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)
        out = os.path.join(d, "report.json")
        subprocess.run([NODE, CLI, "--project", d, "--json", out],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)["findings"]

    def test_direct_launder_not_proven(self):
        proven = [f for f in self._findings()
                  if f["test"] == "direct launder" and f["level"] == "proven"]
        self.assertEqual(proven, [])

    def test_indirect_launder_not_proven(self):
        proven = [f for f in self._findings()
                  if f["test"] == "indirect launder" and f["level"] == "proven"]
        self.assertEqual(proven, [])


if __name__ == "__main__":
    unittest.main()
