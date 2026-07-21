"""--fix must not rewrite files when there is no undo path.

Stdlib only — run with:  python3 -m unittest discover tests
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "skills", "captain-obvious", "scripts", "captain_obvious_py.py")

FIXTURE = '''\
def test_tautology():
    assert True
'''


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


class FixRequiresCleanTree(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-guard-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.test_file = os.path.join(self.dir, "test_x.py")
        with open(self.test_file, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE)

    def _fix(self, *extra):
        return subprocess.run(
            [sys.executable, CLI, "--path", self.dir, "--fix", *extra],
            capture_output=True, text=True)

    def _init_repo(self):
        _git(["init", "-q", "."], self.dir)
        _git(["config", "user.email", "t@example.com"], self.dir)
        _git(["config", "user.name", "t"], self.dir)
        _git(["add", "-A"], self.dir)
        _git(["commit", "-qm", "init"], self.dir)

    def test_refuses_outside_a_git_repo(self):
        proc = self._fix()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not a git repository", proc.stderr)
        with open(self.test_file, encoding="utf-8") as fh:
            self.assertIn("test_tautology", fh.read())

    def test_refuses_on_a_dirty_tree(self):
        self._init_repo()
        with open(self.test_file, "a", encoding="utf-8") as fh:
            fh.write("\n# uncommitted edit\n")
        proc = self._fix()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("uncommitted change", proc.stderr)

    def test_runs_on_a_clean_tree(self):
        self._init_repo()
        proc = self._fix()
        self.assertEqual(proc.returncode, 0)
        with open(self.test_file, encoding="utf-8") as fh:
            self.assertNotIn("test_tautology", fh.read())

    def test_force_overrides_the_guard(self):
        proc = self._fix("--force")
        self.assertEqual(proc.returncode, 0)
        with open(self.test_file, encoding="utf-8") as fh:
            self.assertNotIn("test_tautology", fh.read())

    def test_untracked_files_do_not_block(self):
        self._init_repo()
        with open(os.path.join(self.dir, "scratch.txt"), "w", encoding="utf-8") as fh:
            fh.write("untracked\n")
        proc = self._fix()
        self.assertEqual(proc.returncode, 0)

    def test_report_mode_is_unaffected_without_git(self):
        proc = subprocess.run([sys.executable, CLI, "--path", self.dir],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        with open(self.test_file, encoding="utf-8") as fh:
            self.assertIn("test_tautology", fh.read())


if __name__ == "__main__":
    unittest.main()
