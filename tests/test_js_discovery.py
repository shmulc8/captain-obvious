"""Scan plain-JavaScript test files for the syntactic detector categories.

Discovery + the write-time hook were TS-only; a `.test.js` project scanned as
"no test files". Widening the regexes and ScriptKind unlocks the JS/TS-family
syntactic detectors. Hard gate: `type-guaranteed` must NOT fire on JS (TS's
inference without `checkJs` is not a checked guarantee).

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

CONST_JS = 'test("truth", () => {\n  expect(true).toBe(true);\n});\n'
TYPED_TS = 'test("truth", () => {\n  expect(true).toBe(true);\n});\n'
JS_TYPEOF = ('function getCount() { return 1; }\n'
             'test("js typeof", () => {\n  const n = getCount();\n  expect(typeof n).toBe("number");\n});\n')


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


@unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
class JsDiscovery(unittest.TestCase):
    def _tmp(self, files: dict):
        d = tempfile.mkdtemp(prefix="capobv-js-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for name, content in files.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(content)
        return d

    def _report(self, d):
        out = os.path.join(d, "report.json")
        subprocess.run([NODE, CLI, "--project", d, "--json", out],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)

    def test_plain_js_project_is_scanned(self):
        d = self._tmp({"app.test.js": CONST_JS})
        rep = self._report(d)
        self.assertEqual(rep["testFilesScanned"], 1)
        hits = [f for f in rep["findings"]
                if f["test"] == "truth" and f["category"] == "constant-assert"
                and f["level"] == "proven"]
        self.assertEqual(len(hits), 1)

    def test_type_guaranteed_gated_off_for_js(self):
        d = self._tmp({
            "tsconfig.json": '{"compilerOptions": {"strict": true}}',
            "typed.test.ts": TYPED_TS,
            "js.test.js": JS_TYPEOF,
        })
        rep = self._report(d)
        self.assertEqual(rep["testFilesScanned"], 2)
        # the gate: no type-guaranteed finding on the JS file
        tg_js = [f for f in rep["findings"]
                 if f["test"] == "js typeof" and f["category"] == "type-guaranteed"]
        self.assertEqual(tg_js, [])
        # the .ts file is unaffected — still proven constant-assert
        ts_hits = [f for f in rep["findings"]
                   if f["test"] == "truth" and f["category"] == "constant-assert"
                   and f["level"] == "proven"]
        self.assertEqual(len(ts_hits), 1)

    def test_single_file_js_stdin(self):
        d = self._tmp({"app.test.js": CONST_JS})
        proc = subprocess.run([NODE, CLI, "--file", os.path.join(d, "app.test.js"), "--stdin"],
                              input=CONST_JS, capture_output=True, text=True, check=True)
        report = json.loads(proc.stdout)
        cats = [f["category"] for f in report["findings"] if f["test"] == "truth"]
        self.assertIn("constant-assert", cats)


if __name__ == "__main__":
    unittest.main()
