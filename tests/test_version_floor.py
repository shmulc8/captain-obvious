"""Runtime-floor guards are present and inert on supported runtimes.

The Python CLI and the hook must fail (CLI: exit 2 + message; hook: fail open)
below Python 3.9 because they call ast.unparse. The TS scanner must reject
typescript < 4. These tests assert the guards exist statically (simulating an
old interpreter in-process is fragile) and that they are no-ops on the
supported runtime this suite runs under.

Stdlib only — run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import ast
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
HOOK = os.path.join(REPO, "hooks", "prevent.py")
NODE = shutil.which("node")


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


def _main_guards_below(path: str, floor: tuple[int, int]) -> bool:
    """True iff main() contains `sys.version_info < <floor>` — pins the
    comparison DIRECTION (Lt) and THRESHOLD, not just that version_info is
    mentioned, so a flipped `>` or a wrong `(3, 0)` would fail this check."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "main":
            for n in ast.walk(fn):
                if (isinstance(n, ast.Compare)
                        and isinstance(n.left, ast.Attribute)
                        and n.left.attr == "version_info"
                        and len(n.ops) == 1 and isinstance(n.ops[0], ast.Lt)
                        and isinstance(n.comparators[0], ast.Tuple)):
                    vals = [e.value for e in n.comparators[0].elts
                            if isinstance(e, ast.Constant)]
                    if tuple(vals[:2]) == floor:
                        return True
    return False


class PythonFloorGuard(unittest.TestCase):
    def test_cli_main_guards_below_39(self):
        self.assertTrue(_main_guards_below(PY_CLI, (3, 9)))

    def test_hook_main_guards_below_39(self):
        self.assertTrue(_main_guards_below(HOOK, (3, 9)))


class TsFloorGuard(unittest.TestCase):
    def test_source_has_floor_message(self):
        src = open(TS_CLI, encoding="utf-8").read()
        self.assertIn("unsupported typescript version", src)

    def test_floor_threshold_and_direction(self):
        # pin `tsMajor < 4` — a flipped `>` or a wrong threshold would fail here
        src = open(TS_CLI, encoding="utf-8").read()
        self.assertRegex(src, r"tsMajor\s*<\s*4\b")


@unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
class TsFloorInertOnSupported(unittest.TestCase):
    def test_ts5_passes_the_floor(self):
        d = tempfile.mkdtemp(prefix="capobv-floor-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "lit.test.ts"), "w", encoding="utf-8") as fh:
            fh.write('test("t", () => { expect(true).toBe(true); });\n')
        out = os.path.join(d, "report.json")
        proc = subprocess.run([NODE, TS_CLI, "--project", d, "--json", out],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("unsupported typescript version", proc.stderr)


if __name__ == "__main__":
    unittest.main()
