"""Black-box tests for the write-time prevention hook (hooks/prevent.py).

Each case pipes a PreToolUse stdin payload into the hook and asserts on
stdout/exit code. The hook must fail open on everything unexpected.

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
HOOK = os.path.join(REPO, "hooks", "prevent.py")
SCRIPTS = os.path.join(REPO, "skills", "captain-obvious", "scripts")

NODE = shutil.which("node")


def _ts_resolvable() -> bool:
    if not NODE:
        return False
    probe = subprocess.run(
        [NODE, "-e", "import('typescript').then(()=>process.exit(0),()=>process.exit(1))"],
        cwd=SCRIPTS, capture_output=True)
    return probe.returncode == 0


TAUTOLOGY_PY = '''\
def test_math_still_works():
    assert True
'''

HONEST_PY = '''\
from app import add

def test_add():
    assert add(2, 2) == 4
'''


class PreventHook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-hook-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_hook(self, payload, mode=None, raw=None):
        env = dict(os.environ)
        env.pop("CAPTAIN_OBVIOUS_HOOK", None)
        if mode:
            env["CAPTAIN_OBVIOUS_HOOK"] = mode
        proc = subprocess.run([sys.executable, HOOK],
                              input=raw if raw is not None else json.dumps(payload),
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def write_payload(self, name, content):
        return {"tool_name": "Write",
                "tool_input": {"file_path": os.path.join(self.dir, name),
                               "content": content}}

    def decision(self, out):
        if not out:
            return "allow"
        return json.loads(out).get("hookSpecificOutput", {}).get(
            "permissionDecision", "allow")

    def test_non_test_file_fast_path(self):
        out = self.run_hook(self.write_payload("app.py", TAUTOLOGY_PY))
        self.assertEqual(out, "")

    def test_write_with_tautology_is_denied(self):
        out = self.run_hook(self.write_payload("test_x.py", TAUTOLOGY_PY))
        self.assertEqual(self.decision(out), "deny")
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("constant-assert", reason)
        self.assertIn("test_math_still_works", reason)

    def test_honest_write_is_allowed(self):
        out = self.run_hook(self.write_payload("test_x.py", HONEST_PY))
        self.assertEqual(out, "")

    def test_edit_introducing_tautology_is_denied(self):
        path = os.path.join(self.dir, "test_x.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HONEST_PY)
        payload = {"tool_name": "Edit",
                   "tool_input": {"file_path": path,
                                  "old_string": "assert add(2, 2) == 4",
                                  "new_string": "assert True"}}
        self.assertEqual(self.decision(self.run_hook(payload)), "deny")

    def test_preexisting_finding_does_not_block_unrelated_edit(self):
        path = os.path.join(self.dir, "test_x.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(TAUTOLOGY_PY + "\n" + HONEST_PY)
        payload = {"tool_name": "Edit",
                   "tool_input": {"file_path": path,
                                  "old_string": "add(2, 2) == 4",
                                  "new_string": "add(2, 3) == 5"}}
        self.assertEqual(self.run_hook(payload), "")

    def test_multiedit_is_composed(self):
        path = os.path.join(self.dir, "test_x.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HONEST_PY)
        payload = {"tool_name": "MultiEdit",
                   "tool_input": {"file_path": path,
                                  "edits": [{"old_string": "assert add(2, 2) == 4",
                                             "new_string": "assert True"}]}}
        self.assertEqual(self.decision(self.run_hook(payload)), "deny")

    def test_mode_off_allows(self):
        out = self.run_hook(self.write_payload("test_x.py", TAUTOLOGY_PY), mode="off")
        self.assertEqual(out, "")

    def test_mode_warn_allows_with_message(self):
        out = self.run_hook(self.write_payload("test_x.py", TAUTOLOGY_PY), mode="warn")
        msg = json.loads(out)
        self.assertNotIn("hookSpecificOutput", msg)
        self.assertIn("captain-obvious (warn)", msg["systemMessage"])

    def test_malformed_stdin_fails_open(self):
        self.assertEqual(self.run_hook(None, raw="not json{"), "")

    def test_edit_on_missing_file_fails_open(self):
        payload = {"tool_name": "Edit",
                   "tool_input": {"file_path": os.path.join(self.dir, "test_gone.py"),
                                  "old_string": "a", "new_string": "b"}}
        self.assertEqual(self.run_hook(payload), "")

    def test_broken_syntax_fails_open(self):
        out = self.run_hook(self.write_payload("test_x.py", "def test_(:\n  assert\n"))
        self.assertEqual(out, "")

    @unittest.skipUnless(_ts_resolvable(), "node + typescript not available")
    def test_ts_write_with_tautology_is_denied(self):
        content = ('const x = compute();\n'
                   'test("x equals itself", () => {\n'
                   '  expect(x).toBe(x);\n'
                   '});\n')
        out = self.run_hook(self.write_payload("example.test.ts", content))
        self.assertEqual(self.decision(out), "deny")


if __name__ == "__main__":
    unittest.main()
