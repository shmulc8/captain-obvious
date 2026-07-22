"""Regression tests for the never-asserts whole-test removal guard.

`never-asserts` fires when every assertion is dead or swallowed, so no
assertion can fail the test. But an UNGUARDED call in the body can still fail
it by raising — deleting the test drops that signal. A call inside a
silently-caught try: cannot (the handler absorbs the raise), so a
swallowed-everything test really is unable to fail and stays auto-deletable.

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
CLI = os.path.join(REPO, "skills", "captain-obvious", "scripts", "captain_obvious_py.py")

FIXTURE = '''\
import logging
import pytest

@pytest.fixture
def test_fixture_starting_with_test():
    return 1


def do_work():
    return 1


def test_unreachable_assert_with_live_call():
    # unguarded: if do_work() raises, this test FAILS. Real signal.
    do_work()
    return
    assert False


def test_swallowed_everything():
    # cannot fail whatever do_work() does — the handler absorbs it
    try:
        r = do_work()
        assert r == 2
    except Exception as e:
        print(e)


def test_swallowed_and_truly_inert():
    try:
        assert 1 == 2
    except Exception as e:
        print(e)


def test_unreachable_call_after_return():
    return
    do_work()
    assert False


def test_logger_noise_call():
    # self.logger and chained logging should not count as remnant calls keeping it advisory
    logging.getLogger(__name__).info("x")
    return
    assert False


def test_silent_smoke_with_call():
    try:
        do_work()
    except Exception:
        pass


def test_silent_smoke_empty():
    pass
'''


class NeverAssertsGuard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="capobv-")
        self.test_file = os.path.join(self.dir, "test_smoke.py")
        with open(self.test_file, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _scan(self):
        out = os.path.join(self.dir, "report.json")
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--json", out],
                       capture_output=True, text=True, check=True)
        with open(out, encoding="utf-8") as fh:
            report = json.load(fh)
        return {f["test"]: f for f in report["findings"]}

    def test_unguarded_call_makes_it_advisory(self):
        found = self._scan()["test_unreachable_assert_with_live_call"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "advisory")
        self.assertEqual(found["deletable"], "report-only")

    def test_swallowed_everything_stays_proven(self):
        """The whole point of never-asserts: a test wrapped in a silent catch
        genuinely cannot fail, so it must remain auto-deletable. Narrowing the
        guard must not weaken the category it exists for."""
        found = self._scan()["test_swallowed_everything"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_truly_inert_test_stays_proven(self):
        found = self._scan()["test_swallowed_and_truly_inert"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_unreachable_call_after_return_stays_proven(self):
        found = self._scan()["test_unreachable_call_after_return"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_logger_noise_call_stays_proven(self):
        found = self._scan()["test_logger_noise_call"]
        self.assertEqual(found["category"], "never-asserts")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_silent_smoke_with_call_is_proven(self):
        found = self._scan()["test_silent_smoke_with_call"]
        self.assertEqual(found["category"], "silent-smoke")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_silent_smoke_empty_is_proven(self):
        found = self._scan()["test_silent_smoke_empty"]
        self.assertEqual(found["category"], "silent-smoke")
        self.assertEqual(found["level"], "proven")
        self.assertEqual(found["deletable"], "safe")

    def test_fixture_is_ignored(self):
        self.assertNotIn("test_fixture_starting_with_test", self._scan())

    def test_fix_keeps_only_the_test_that_can_still_fail(self):
        for args in (["init", "-q", "."], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "init"]):
            subprocess.run(["git"] + args, cwd=self.dir, capture_output=True, text=True)
        subprocess.run([sys.executable, CLI, "--path", self.dir, "--fix"],
                       capture_output=True, text=True, check=True)
        with open(self.test_file, encoding="utf-8") as fh:
            remaining = fh.read()
        self.assertIn("test_unreachable_assert_with_live_call", remaining,
                      "--fix deleted a test that can still fail when do_work() raises")
        self.assertNotIn("test_swallowed_everything", remaining)
        self.assertNotIn("test_swallowed_and_truly_inert", remaining)
        self.assertNotIn("test_unreachable_call_after_return", remaining)
        self.assertNotIn("test_logger_noise_call", remaining)
        self.assertNotIn("test_silent_smoke_with_call", remaining)
        self.assertNotIn("test_silent_smoke_empty", remaining)


if __name__ == "__main__":
    unittest.main()
