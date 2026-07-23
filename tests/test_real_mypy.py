"""Opt-in end-to-end test against REAL mypy — locks the reveal_type contract.

The hermetic suite tests the type-guaranteed path against fake mypy scripts.
This canary runs the actual mypy so a real release changing the
`Revealed type is "..."` note shape, the flags, or path reporting is caught
instead of silently vanishing the whole category with the suite still green.

Skips when mypy is not on PATH. Stdlib only —
run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "skills", "captain-obvious", "scripts", "captain_obvious_py.py")

APP_SRC = "def compute() -> int:\n    return 5\n"
TEST_SRC = "from app import compute\n\ndef test_typed():\n    result = compute()\n    assert isinstance(result, int)\n"


@unittest.skipUnless(shutil.which("mypy"), "real mypy not on PATH")
class RealMypyContract(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-realmypy-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        with open(os.path.join(self.dir, "app.py"), "w", encoding="utf-8") as fh:
            fh.write(APP_SRC)
        with open(os.path.join(self.dir, "test_app.py"), "w", encoding="utf-8") as fh:
            fh.write(TEST_SRC)
        out = os.path.join(self.dir, "report.json")
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--json", out,
                        "--mypy", "mypy"],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            self.report = json.load(fh)

    def test_mypy_ran_without_degradation(self):
        self.assertIsNone(self.report["mypyNote"])

    def test_exactly_one_type_guaranteed_proven(self):
        tg = [f for f in self.report["findings"] if f["category"] == "type-guaranteed"]
        self.assertEqual(len(tg), 1)
        f = tg[0]
        self.assertEqual(f["level"], "proven")
        self.assertEqual(f["deletable"], "safe")
        self.assertEqual(f["test"], "test_typed")

    def test_reason_reflects_revealed_type(self):
        f = next(f for f in self.report["findings"] if f["category"] == "type-guaranteed")
        self.assertIn("mypy already guarantees isinstance", f["reason"])
        self.assertIn("int", f["reason"])

    def test_shadow_files_cleaned_up(self):
        leftovers = glob.glob(os.path.join(self.dir, "_cap_obv_shadow_*"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
