"""--check --base <ref>: report-only CI gate for newly-introduced proven findings.

Exit-code contract: 1 = a proven *syntactic* finding is newly introduced vs the
base ref in a changed file; 0 = clean OR any fail-open case; 2 = arg/refusal.

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

# real code under test, no findings vs a dead tautology
CLEAN = "def test_real():\n    assert compute(1) == 2\n"
DEAD = "def test_dead():\n    assert True\n"          # -> constant-assert, proven
TS_CLEAN = 'test("real", () => {\n  expect(add(1, 2)).toBe(3);\n});\n'
TS_DEAD = 'test("dead", () => {\n  expect(true).toBe(true);\n});\n'  # -> constant-assert

# a conditional-assert (advisory) that --coverage promotes to proven; the
# `assert` is on line 7 behind the platform guard
PY_COND_SRC = ('import sys\nfrom app import compute\n\n'
               'def test_conditional():\n    result = compute()\n'
               '    if sys.platform == "win32":\n        assert result == 5\n')


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


class _Base(unittest.TestCase):
    def repo(self) -> str:
        d = tempfile.mkdtemp(prefix="capobv-check-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _git(["init", "-q", "."], d)
        _git(["config", "user.email", "t@t"], d)
        _git(["config", "user.name", "t"], d)
        return d

    def write(self, d, name, content):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(content)

    def commit(self, d, msg="c") -> str:
        _git(["add", "-A"], d)
        _git(["commit", "-qm", msg], d)
        return _git(["rev-parse", "HEAD"], d).stdout.strip()

    def check(self, d, base, *, cli=PY_CLI, extra=()):
        if cli == PY_CLI:
            cmd = [sys.executable, cli, "--path", d, "--no-types", "--check", "--base", base, *extra]
        else:
            cmd = [NODE, cli, "--project", d, "--check", "--base", base, *extra]
        return subprocess.run(cmd, capture_output=True, text=True)


class CheckGate(_Base):
    def test_new_dead_test_in_changed_file_fails(self):
        d = self.repo()
        self.write(d, "test_a.py", CLEAN)
        base = self.commit(d)
        self.write(d, "test_a.py", CLEAN + "\n" + DEAD)  # second commit (A...HEAD is commit-range)
        self.commit(d)
        r = self.check(d, base)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("constant-assert", r.stderr)

    def test_preexisting_finding_in_changed_file_passes(self):
        # finding lives on BOTH sides of a *changed* file -> not newly introduced,
        # even though its LINE moves. Prepending the touch line shifts the dead
        # test to a different line in HEAD vs base, so this genuinely exercises
        # the line-independent (category, test) seen-set — the whole point of it.
        d = self.repo()
        self.write(d, "test_a.py", DEAD)
        base = self.commit(d)
        self.write(d, "test_a.py", "# touched — shifts the finding's line\n" + DEAD)
        self.commit(d)
        r = self.check(d, base)
        self.assertEqual(r.returncode, 0, r.stderr)
        # exact clean sentinel — NOT the substring "clean", which also appears in
        # the fail-open message ("treating as clean (fail-open)")
        self.assertIn("no newly-introduced", r.stderr)
        self.assertNotIn("fail-open", r.stderr)

    def test_coverage_promoted_conditional_is_not_gated(self):
        # a pre-existing conditional-assert, promoted advisory->proven by
        # --coverage on the current side, must NOT gate: the base single-file
        # scan has no coverage, so it can never reproduce the promotion. It is
        # excluded from candidates alongside type-guaranteed.
        d = self.repo()
        self.write(d, "test_cond.py", PY_COND_SRC)
        base = self.commit(d)
        self.write(d, "test_cond.py", PY_COND_SRC + "# touched\n")
        self.commit(d)
        cov = os.path.join(d, "coverage.dat")
        with open(cov, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"files": {"test_cond.py":
                     {"executed_lines": [1, 2, 4, 5, 6], "missing_lines": [7]}}}))
        r = self.check(d, base, extra=("--coverage", cov))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no newly-introduced", r.stderr)

    def test_bad_ref_fails_open(self):
        d = self.repo()
        self.write(d, "test_a.py", DEAD)
        self.commit(d)
        r = self.check(d, "does-not-exist")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("fail-open", r.stderr)

    def test_check_with_fix_is_rejected(self):
        d = self.repo()
        self.write(d, "test_a.py", CLEAN)
        base = self.commit(d)
        r = self.check(d, base, extra=("--fix",))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_brand_new_file_absent_from_base_fails(self):
        d = self.repo()
        self.write(d, "test_a.py", CLEAN)
        base = self.commit(d)
        self.write(d, "test_new.py", DEAD)
        self.commit(d)
        r = self.check(d, base)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("test_new.py", r.stderr)


@unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
class CheckGateTs(_Base):
    def test_new_dead_test_fails(self):
        # append to a file *present in base* so the base-side self-spawn
        # (git show -> `--file --stdin` re-invoke -> JSON parse) actually runs
        d = self.repo()
        self.write(d, "a.test.ts", TS_CLEAN)
        base = self.commit(d)
        self.write(d, "a.test.ts", TS_CLEAN + "\n" + TS_DEAD)
        self.commit(d)
        r = self.check(d, base, cli=TS_CLI)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("constant-assert", r.stderr)

    def test_preexisting_ts_finding_passes(self):
        # TS gate's clean/dedup branch: a dead test present on both sides of a
        # changed file, its line shifted, must exit 0 with the clean sentinel
        d = self.repo()
        self.write(d, "a.test.ts", TS_DEAD)
        base = self.commit(d)
        self.write(d, "a.test.ts", "// touched — shifts the finding's line\n" + TS_DEAD)
        self.commit(d)
        r = self.check(d, base, cli=TS_CLI)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no newly-introduced", r.stderr)
        self.assertNotIn("fail-open", r.stderr)

    def test_bad_ref_fails_open(self):
        d = self.repo()
        self.write(d, "a.test.ts", TS_DEAD)
        self.commit(d)
        r = self.check(d, "does-not-exist", cli=TS_CLI)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("fail-open", r.stderr)

    def test_check_with_fix_is_rejected(self):
        d = self.repo()
        self.write(d, "a.test.ts", TS_CLEAN)
        base = self.commit(d)
        r = self.check(d, base, cli=TS_CLI, extra=("--fix",))
        self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main()
