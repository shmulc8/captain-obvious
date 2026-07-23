"""Cross-language parity corpus for syntactic detectors.

The same logical test, written in Python and TypeScript, must earn the same
(category, level) from both engines. This is the mechanical half of the
CLAUDE.md parity rule. Type-dependent categories are excluded (they need
mypy/tsconfig — plans 003/008 cover those).

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
PY_CLI = os.path.join(SCRIPTS, "captain_obvious_py.py")
TS_CLI = os.path.join(SCRIPTS, "captain_obvious_ts.mjs")
NODE = shutil.which("node")

PY_HEADER = "from app import compute\n\n"

# (name, py_src, py_target, ts_src, ts_target, category, level)
CORPUS = [
    (
        "constant",
        "def test_corpus():\n    assert True\n",
        "test_corpus",
        'test("corpus", () => {\n  expect(true).toBe(true);\n});\n',
        "corpus",
        "constant-assert", "proven",
    ),
    (
        "boundary",
        "def test_corpus():\n    data = compute()\n    assert len(data) >= 0\n",
        "test_corpus",
        'function compute(n) { return n; }\n'
        'test("corpus", () => {\n  const d = compute();\n  expect(d.length).toBeGreaterThanOrEqual(0);\n});\n',
        "corpus",
        "boundary-tautology", "proven",
    ),
    (
        "const-echo",
        "def test_corpus():\n    x = 5\n    assert x == 5\n",
        "test_corpus",
        'test("corpus", () => {\n  const x = 5;\n  expect(x).toBe(5);\n});\n',
        "corpus",
        "local-const-echo", "proven",
    ),
    (
        "self-compare",
        "def test_corpus():\n    assert compute(1) == compute(1)\n",
        "test_corpus",
        'function compute(n) { return n; }\n'
        'test("corpus", () => {\n  expect(compute(1)).toEqual(compute(1));\n});\n',
        "corpus",
        "self-compare-call", "advisory",
    ),
    (
        # a live assert first, so the test is not itself never-asserts and the
        # dead assert after `return` is isolated (both engines then agree on
        # dead-assert; a dead-only test collapses to never-asserts on both,
        # which the engines label with differing granularity)
        "dead-assert",
        "def test_corpus():\n    assert compute(2) == 3\n    return\n    assert compute()\n",
        "test_corpus",
        'function compute(n) { return n; }\n'
        'test("corpus", () => {\n  expect(compute(2)).toBe(3);\n  return;\n  expect(compute()).toBe(1);\n});\n',
        "corpus",
        "dead-assert", "proven",
    ),
    (
        "duplicate",
        "def test_dup_one():\n    assert compute('payload-here') == 'result-here'\n\n"
        "def test_dup_two():\n    assert compute('payload-here') == 'result-here'\n",
        "test_dup_two",
        'function compute(x) { return x; }\n'
        'test("dup one", () => {\n  expect(compute("payload-here")).toEqual("result-here");\n});\n'
        'test("dup two", () => {\n  expect(compute("payload-here")).toEqual("result-here");\n});\n',
        "dup two",
        "duplicate-test", "proven",
    ),
]


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


def _pairs_for(findings, target):
    return [(f["category"], f["level"]) for f in findings if f["test"] == target]


class ParityCorpus(unittest.TestCase):
    def test_python_side(self):
        for name, py_src, py_target, _ts, _tt, cat, level in CORPUS:
            with self.subTest(entry=name, lang="py"):
                d = tempfile.mkdtemp(prefix="capobv-parity-py-")
                self.addCleanup(shutil.rmtree, d, ignore_errors=True)
                with open(os.path.join(d, "test_corpus.py"), "w", encoding="utf-8") as fh:
                    fh.write(PY_HEADER + py_src)
                out = os.path.join(d, "report.json")
                subprocess.run([sys.executable, PY_CLI, "--path", d, "--json", out,
                                "--no-types"], capture_output=True, text=True, check=True)
                findings = json.load(open(out, encoding="utf-8"))["findings"]
                self.assertIn((cat, level), _pairs_for(findings, py_target))

    @unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
    def test_ts_side(self):
        for name, _py, _pt, ts_src, ts_target, cat, level in CORPUS:
            with self.subTest(entry=name, lang="ts"):
                d = tempfile.mkdtemp(prefix="capobv-parity-ts-")
                self.addCleanup(shutil.rmtree, d, ignore_errors=True)
                with open(os.path.join(d, "corpus.test.ts"), "w", encoding="utf-8") as fh:
                    fh.write(ts_src)
                out = os.path.join(d, "report.json")
                subprocess.run([NODE, TS_CLI, "--project", d, "--json", out],
                               capture_output=True, text=True, check=True)
                findings = json.load(open(out, encoding="utf-8"))["findings"]
                self.assertIn((cat, level), _pairs_for(findings, ts_target))


if __name__ == "__main__":
    unittest.main()
