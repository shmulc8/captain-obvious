"""Coverage promote/suppress across all three formats, both scanners.

A conditional-assert whose line ran 0 times is promoted to proven (--fix will
then delete it); a line that ran is suppressed (dropped). Both CLIs parse
coverage.py-json, istanbul-json and lcov — but the Python suite only ever fed
coverage.py-json, and the TS promote/suppress path had no self-test at all.
A parser bug on an unexercised format silently changes what --fix removes.

Each format encodes "assert line ran 0 times, other lines ran". The TS
conditional line is read back from a report-only run (STOP if it never fires).

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


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


def _conditional_findings(report):
    return [f for f in report["findings"] if f["category"] == "conditional-assert"]


class _CoverageMatrix:
    """Shared assertions; subclasses supply the CLI, fixture and assert line."""

    def _write_cov(self, content):
        cov_path = os.path.join(self.dir, "coverage.dat")
        with open(cov_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return cov_path

    # -- format builders ("assert line ran 0 times, other lines ran") ----
    def _coverage_py(self, hits):
        executed = [self.assert_line] if hits else [self.assert_line - 1]
        missing = [] if hits else [self.assert_line]
        return json.dumps({"files": {self.relfile:
                          {"executed_lines": executed, "missing_lines": missing}}})

    def _istanbul(self, hits):
        return json.dumps({self.absfile: {"path": self.absfile,
            "statementMap": {"0": {"start": {"line": self.assert_line}}},
            "s": {"0": hits}}})

    def _lcov(self, hits):
        return f"SF:{self.absfile}\nDA:{self.assert_line},{hits}\nend_of_record\n"

    # -- shared checks ---------------------------------------------------
    def _assert_promoted(self, report):
        self.assertEqual(report["coverage"]["conditionalAssertsPromoted"], 1)
        conds = _conditional_findings(report)
        self.assertEqual(len(conds), 1)
        self.assertEqual(conds[0]["level"], "proven")

    def _assert_suppressed(self, report):
        self.assertEqual(report["coverage"]["conditionalAssertsSuppressed"], 1)
        self.assertEqual(_conditional_findings(report), [])

    def test_coverage_py_json_promotes(self):
        self._assert_promoted(self.run_cov(self._coverage_py(hits=0)))

    def test_istanbul_json_promotes(self):
        self._assert_promoted(self.run_cov(self._istanbul(hits=0)))

    def test_lcov_promotes(self):
        self._assert_promoted(self.run_cov(self._lcov(hits=0)))

    def test_reached_line_is_suppressed(self):
        self._assert_suppressed(self.run_cov(self._coverage_py(hits=1)))


PY_COND_SRC = '''\
import sys
from app import compute

def test_conditional():
    result = compute()
    if sys.platform == "win32":
        assert result == 5
'''


class PythonCoverageFormats(_CoverageMatrix, unittest.TestCase):
    relfile = "test_app.py"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-covpy-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.absfile = os.path.join(self.dir, self.relfile)
        with open(self.absfile, "w", encoding="utf-8") as fh:
            fh.write(PY_COND_SRC)
        self.assert_line = 7  # the `assert result == 5` behind the platform guard

    def run_cov(self, cov_content):
        cov_path = self._write_cov(cov_content)
        out = os.path.join(self.dir, "report.json")
        subprocess.run([sys.executable, PY_CLI, "--path", self.dir, "--json", out,
                        "--no-types", "--coverage", cov_path],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)


TS_COND_SRC = '''\
import { compute } from "./app";
test("conditional", () => {
  const result = compute();
  if (process.platform === "win32") {
    expect(result).toBe(5);
  }
});
'''


@unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
class TsCoverageFormats(_CoverageMatrix, unittest.TestCase):
    relfile = "cond.test.ts"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-covts-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.absfile = os.path.join(self.dir, self.relfile)
        with open(self.absfile, "w", encoding="utf-8") as fh:
            fh.write(TS_COND_SRC)
        # Pre-check: read the reported line of the conditional-assert finding.
        # If it never fires, the TS detector's shape differs from this plan's
        # model — STOP (this assertion fails loudly rather than silently pass).
        report = self._ts_report()
        conds = _conditional_findings(report)
        self.assertEqual(len(conds), 1,
                         f"expected one conditional-assert finding, got {report['findings']}")
        self.assertEqual(conds[0]["level"], "advisory")
        self.assert_line = conds[0]["line"]

    def _ts_report(self, *extra):
        out = os.path.join(self.dir, "report.json")
        subprocess.run([NODE, TS_CLI, "--project", self.dir, "--json", out, *extra],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)

    def run_cov(self, cov_content):
        cov_path = self._write_cov(cov_content)
        return self._ts_report("--coverage", cov_path)


if __name__ == "__main__":
    unittest.main()
