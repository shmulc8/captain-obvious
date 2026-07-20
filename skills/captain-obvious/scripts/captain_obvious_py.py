#!/usr/bin/env python3
"""
captain-obvious — Python detector

Deterministically finds pytest tests that can never fail or check nothing,
and optionally deletes them.

Categories:
  type-guaranteed   assertion proven by the type checker via mypy reveal_type
                    (isinstance on typed values, `is not None` on non-Optional)
  constant-assert   assert True / assert 1 == 1 / assert x == x
  no-assert         test contains no assertion at all
  mock-echo         test asserts a mock does what it was just stubbed to do
  duplicate-test    identical body as an earlier test in the same scope
  dead-assert       assertion after an unconditional return/raise — never runs
  swallowed-assert  assertion inside try: with a silent except — cannot fail
  never-asserts     test has assertions but ALL are dead or swallowed
  conditional-assert  assertion gated behind if/loop — may never run
                    (reported only, never auto-deleted)

Levels:
  proven    provably cannot fail — deleted by --fix
  advisory  almost certainly useless but not provable — deleted by
            --fix --aggressive (except report-only categories)

Usage:
  python3 captain_obvious_py.py --path <project-dir>
       [--fix] [--aggressive] [--json <out.json>] [--mypy "<cmd>"] [--no-types]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox",
             ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs"}
ASSERT_NAME_RE = re.compile(r"^(assert|expect|verify|check|should)", re.I)
MUST_NOT_RAISE_RE = re.compile(
    r"(not?[_ ]raise|noop|no[_ ]op|silent|swallow|graceful|does[_ ]not[_ ]throw)", re.I)
REVEAL_RE = re.compile(r'^(.*?):(\d+):(?:\d+:)?\s*note: Revealed type is "(.*)"\s*$')
ANY_RETURN_RE = re.compile(r'^(.*?):(\d+):(?:\d+:)?\s*(?:error|warning):.*\[no-any-return\]\s*$')


# ------------------------------------------------------------------ discovery
def find_test_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.startswith("test_") and f.endswith(".py") or f.endswith("_test.py"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


# ------------------------------------------------------------------ AST utils
def call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def is_simple_chain(node: ast.AST) -> bool:
    """Name or dotted attribute chain — no calls, no subscripts."""
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Attribute):
        return is_simple_chain(node.value)
    return False


def const_truthiness(node: ast.AST):
    """True/False if node is a constant with known truthiness, else None."""
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        l, r = node.left, node.comparators[0]
        if isinstance(l, ast.Constant) and isinstance(r, ast.Constant):
            op = node.ops[0]
            try:
                if isinstance(op, ast.Eq):
                    return l.value == r.value
                if isinstance(op, ast.Is):
                    return l.value is r.value
            except Exception:
                return None
    return None


def walk_no_nested_funcs(node: ast.AST):
    """Yield descendants without descending into nested function/class defs."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from walk_no_nested_funcs(child)


def is_assertionish_call(node: ast.Call) -> bool:
    name = call_name(node)
    if name is None:
        return False
    if ASSERT_NAME_RE.match(name):
        return True
    if name in ("fail", "raises"):
        return True
    return False


def has_pytest_raises(node: ast.AST) -> bool:
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            e = item.context_expr
            if isinstance(e, ast.Call) and call_name(e) in ("raises", "warns", "deprecated_call"):
                return True
    return False


class HelperIndex:
    """Same-file helper resolution: a test that calls _check_result(...) which
    contains asserts is NOT assertion-free. Transitive, cycle-safe."""

    def __init__(self, tree: ast.Module):
        self.defs: dict[str, ast.AST] = {}
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.defs[n.name] = n
        self._memo: dict[str, bool] = {}

    def fn_asserts(self, name: str, depth: int = 0) -> bool:
        if name in self._memo:
            return self._memo[name]
        if depth > 3 or name not in self.defs:
            return False
        self._memo[name] = False  # cycle guard
        node = self.defs[name]
        result = False
        for d in walk_no_nested_funcs(node):
            if isinstance(d, ast.Assert) or has_pytest_raises(d):
                result = True
                break
            if isinstance(d, ast.Call):
                if is_assertionish_call(d):
                    result = True
                    break
                cn = call_name(d)
                if cn and cn in self.defs and self.fn_asserts(cn, depth + 1):
                    result = True
                    break
        self._memo[name] = result
        return result


# ------------------------------------------------------------------ analysis
class Finding:
    def __init__(self, file, line, test, category, level, deletable, reason, node=None):
        self.file, self.line, self.test = file, line, test
        self.category, self.level, self.deletable = category, level, deletable
        self.reason, self.node = reason, node

    def to_dict(self, root):
        return {"file": os.path.relpath(self.file, root), "line": self.line,
                "test": self.test, "category": self.category, "level": self.level,
                "deletable": self.deletable, "reason": self.reason}


class Probe:
    """A type question for mypy: what is the type of `expr` just before `line`?"""

    def __init__(self, file, line, indent, expr_src, kind, extra):
        self.file, self.line, self.indent = file, line, indent
        self.expr_src, self.kind, self.extra = expr_src, kind, extra
        self.revealed: str | None = None
        self.finding_slot = None  # (test_record, assert_node) to fill on success


class TestRecord:
    def __init__(self, file, node, name, scope_key):
        self.file, self.node, self.name, self.scope_key = file, node, name, scope_key
        self.findings: list[Finding] = []
        self.live_assert_count = 0
        self.nonredundant = 0      # live assertions we could not prove useless
        self.helper_asserts = 0    # assertions living in called helpers
        self.conditional = 0
        self.is_duplicate = False
        self.body_key = None
        self.deletable_stmt_nodes: list[ast.AST] = []  # proven per-line removals


def silent_handler(h: ast.ExceptHandler) -> bool:
    return all(isinstance(s, ast.Pass) or
               (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and
                call_name(s.value) in ("print",))
               for s in h.body)


def analyze_file(path: str, src: str, tree: ast.Module, root: str,
                 probes: list[Probe], records: list[TestRecord]):
    helpers = HelperIndex(tree)
    lines = src.splitlines()

    def fn_has_cast(fn) -> bool:
        """cast()/type:ignore inside THIS test mean its types may lie — but a
        stray `# type: ignore` elsewhere in the file shouldn't kill all probes."""
        seg = ast.get_source_segment(src, fn) or ""
        return "cast(" in seg or "type: ignore" in seg

    def tests_in(container, scope_key, class_name=None):
        for n in container:
            if isinstance(n, ast.ClassDef) and n.name.startswith("Test"):
                tests_in(n.body, f"{scope_key}::{n.name}", n.name)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
                analyze_test(n, scope_key, class_name)

    def analyze_test(fn, scope_key, class_name):
        rec = TestRecord(path, fn, fn.name, scope_key)
        records.append(rec)
        body = fn.body

        # unconditionally-skipped test (@pytest.mark.skip / @unittest.skip):
        # it never runs, so it can never fail. Advisory — skips sometimes
        # document future work. skipif is conditional and stays untouched.
        for dec in fn.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "skip":
                rec.findings.append(Finding(
                    path, fn.lineno, fn.name, "skipped-test", "advisory", "aggressive",
                    "test is unconditionally skipped — it never runs and can never fail"))
                return

        # skip parametrized-over-everything? no — parametrize is fine, analyze body

        # -- reachability at top level
        unreachable: set[int] = set()
        dead = False
        for s in body:
            if dead:
                unreachable.add(id(s))
            if isinstance(s, (ast.Return, ast.Raise)):
                dead = True

        def top_stmt_of(node):
            for s in body:
                if s is node:
                    return s
                for d in ast.walk(s):
                    if d is node:
                        return s
            return None

        # -- swallowed: asserts inside try whose handlers are all silent
        swallowed_ids: set[int] = set()
        for d in walk_no_nested_funcs(fn):
            if isinstance(d, ast.Try) and d.handlers and all(silent_handler(h) for h in d.handlers):
                for x in ast.walk(ast.Module(body=d.body, type_ignores=[])):
                    if isinstance(x, ast.Assert) or (isinstance(x, ast.Call) and is_assertionish_call(x)):
                        swallowed_ids.add(id(x))

        # -- collect assertion-ish nodes (not descending into nested defs)
        assert_nodes: list[ast.AST] = []
        for d in walk_no_nested_funcs(fn):
            if isinstance(d, ast.Assert) or has_pytest_raises(d):
                assert_nodes.append(d)
            elif isinstance(d, ast.Call):
                cn = call_name(d)
                if cn is None:
                    continue
                if isinstance(d.func, ast.Attribute) and cn.startswith("assert"):
                    assert_nodes.append(d)          # self.assertX / mock.assert_called*
                elif ASSERT_NAME_RE.match(cn) or cn == "fail":
                    assert_nodes.append(d)
                elif cn in helpers.defs and helpers.fn_asserts(cn):
                    assert_nodes.append(d)          # local helper that asserts
                    rec.helper_asserts += 1

        live = []
        for a in assert_nodes:
            if id(a) in swallowed_ids:
                rec.findings.append(Finding(
                    path, a.lineno, fn.name, "swallowed-assert", "proven", "report-only",
                    f"assertion at line {a.lineno} sits in try: with a silent except — a failure is swallowed"))
                continue
            ts = top_stmt_of(a)
            if ts is not None and id(ts) in unreachable:
                if isinstance(a, ast.Assert) and ts is a:
                    rec.findings.append(Finding(
                        path, a.lineno, fn.name, "dead-assert", "proven", "safe",
                        "sits after an unconditional return/raise — never executes", node=a))
                    rec.deletable_stmt_nodes.append(a)
                continue
            live.append(a)

        # -- no-assert / never-asserts
        doc = ast.get_docstring(fn) or ""
        contract_like = bool(MUST_NOT_RAISE_RE.search(fn.name) or MUST_NOT_RAISE_RE.search(doc))
        if not assert_nodes:
            if contract_like:
                rec.findings.append(Finding(
                    path, fn.lineno, fn.name, "no-assert", "advisory", "report-only",
                    "no assertion, but the name/docstring suggests a deliberate must-not-raise contract test — review by hand"))
            else:
                rec.findings.append(Finding(
                    path, fn.lineno, fn.name, "no-assert", "advisory", "aggressive",
                    "test contains no assertion — it can only fail if something raises"))
            return
        if not live:
            rec.findings.append(Finding(
                path, fn.lineno, fn.name, "never-asserts", "proven", "safe",
                "every assertion is unreachable or swallowed — the test can never fail"))
            return

        # -- conditional (rotten green): a live assert that may never execute
        # because a guard is never true. To keep this signal clean we flag ONLY
        # the classic vacuous-pass shape and deliberately exclude two common,
        # legitimate patterns that otherwise drown it:
        #   * loop-nested asserts (`for x in items: if ...: assert`) — ordinary
        #     data-driven dispatch, not a rotten green test;
        #   * asserts guarded by a parametrized argument (`if mode: ... else: ...`
        #     where `mode` is a @pytest.mark.parametrize / fixture parameter) —
        #     every branch is exercised across the parametrization.
        param_names: set[str] = set()
        _a = fn.args
        for grp in (_a.posonlyargs, _a.args, _a.kwonlyargs):
            for arg in grp:
                param_names.add(arg.arg)
        if _a.vararg:
            param_names.add(_a.vararg.arg)
        if _a.kwarg:
            param_names.add(_a.kwarg.arg)

        loop_gated_asserts: set[int] = set()
        for d in walk_no_nested_funcs(fn):
            if isinstance(d, (ast.For, ast.AsyncFor, ast.While)):
                for x in ast.walk(d):
                    if isinstance(x, ast.Assert):
                        loop_gated_asserts.add(id(x))

        plain_live_asserts = []
        for a in live:
            gated = False
            ts = top_stmt_of(a)
            if ts is not None and ts is not a and id(a) not in loop_gated_asserts:
                for d in [ts, *walk_no_nested_funcs(ts)]:
                    if isinstance(d, ast.If) and any(x is a for x in ast.walk(d)):
                        # skip guards keyed on a test parameter — parametrization
                        # runs every branch, so the assert is not really optional
                        cond_names = {n.id for n in ast.walk(d.test) if isinstance(n, ast.Name)}
                        if cond_names & param_names:
                            break
                        gated = True
                        break
            if gated:
                rec.conditional += 1
                rec.findings.append(Finding(
                    path, a.lineno, fn.name, "conditional-assert", "advisory", "report-only",
                    f"assertion at line {a.lineno} is gated behind an if — it may never execute (rotten green)"))
            elif isinstance(a, ast.Assert) and ts is a:
                plain_live_asserts.append(a)
            else:
                rec.nonredundant += 1  # helper call, with-raises, self.assertX handled below, etc.

        rec.live_assert_count = len(plain_live_asserts)

        # -- mock stubs in this test: `m.return_value = V`, plus which names are
        #    bare mocks created in-test (m = MagicMock()) — asserting on a direct
        #    call of those provably tests the mock library, not project code
        stubs = {}       # mock name -> ast.dump of V
        bare_mocks = set()
        for s in body:
            if isinstance(s, ast.Assign) and len(s.targets) == 1:
                t = s.targets[0]
                if isinstance(t, ast.Attribute) and t.attr in ("return_value", "side_effect") \
                        and isinstance(t.value, (ast.Name, ast.Attribute)):
                    base = t.value.id if isinstance(t.value, ast.Name) else t.value.attr
                    stubs[base] = ast.dump(s.value)
                elif isinstance(t, ast.Name) and isinstance(s.value, ast.Call):
                    ctor = call_name(s.value)
                    if ctor in ("MagicMock", "Mock", "AsyncMock"):
                        bare_mocks.add(t.id)

        # -- literal locals for local-const-echo: x = 5 (and never rebound)
        # Walk the FULL subtree (including nested functions): a closure can mutate
        # a captured variable via `nonlocal`/`global` (e.g. a call-counter
        # `count = 0; def cb(): nonlocal count; count += 1; ... assert count == 0`),
        # in which case `assert count == <literal>` is real behavioural coverage,
        # not a self-referential arrangement. Counting only top-level assignments
        # would wrongly treat such a counter as an immutable literal.
        assign_counts: dict[str, int] = {}
        const_map: dict[str, object] = {}
        for d in ast.walk(fn):
            targets = []
            if isinstance(d, ast.Assign):
                targets = d.targets
            elif isinstance(d, (ast.AugAssign, ast.AnnAssign)) and d.target is not None:
                targets = [d.target]
            elif isinstance(d, (ast.For, ast.AsyncFor)):
                targets = [d.target]
            elif isinstance(d, ast.NamedExpr):
                targets = [d.target]
            elif isinstance(d, (ast.Global, ast.Nonlocal)):
                # a name rebindable from a nested/other scope is never a fixed literal
                for nm in d.names:
                    assign_counts[nm] = assign_counts.get(nm, 0) + 2
                continue
            elif isinstance(d, (ast.With, ast.AsyncWith)):
                targets = [it.optional_vars for it in d.items if it.optional_vars is not None]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        assign_counts[n.id] = assign_counts.get(n.id, 0) + 1
        for s in body:
            if isinstance(s, ast.Assign) and len(s.targets) == 1 \
                    and isinstance(s.targets[0], ast.Name) and isinstance(s.value, ast.Constant) \
                    and assign_counts.get(s.targets[0].id, 0) == 1:
                const_map[s.targets[0].id] = s.value.value

        # -- broad-raises: `with pytest.raises(Exception)` as the only check
        if rec.nonredundant + len(plain_live_asserts) <= 1:
            for d in walk_no_nested_funcs(fn):
                if isinstance(d, (ast.With, ast.AsyncWith)):
                    for item in d.items:
                        e = item.context_expr
                        if isinstance(e, ast.Call) and call_name(e) == "raises" and e.args:
                            a0 = e.args[0]
                            nm = a0.id if isinstance(a0, ast.Name) else getattr(a0, "attr", None)
                            if nm in ("Exception", "BaseException"):
                                rec.findings.append(Finding(
                                    path, d.lineno, fn.name, "broad-raises", "advisory", "report-only",
                                    "pytest.raises(Exception) is the only check — it passes on ANY bug that raises; "
                                    "narrow the exception type or assert on the message"))

        # -- classify each plain live `assert`
        for a in plain_live_asserts:
            f = classify_assert(a, fn, stubs, fn_has_cast(fn), const_map, bare_mocks)
            if f is None:
                rec.nonredundant += 1
            elif f == "QUEUED":
                pass  # a mypy probe will resolve this one (or count it nonredundant)
            else:
                rec.findings.append(f)
                if f.deletable in ("safe", "aggressive") and f.node is not None:
                    rec.deletable_stmt_nodes.append(f.node)

    def classify_assert(a: ast.Assert, fn, stubs, file_has_cast, const_map=None,
                        bare_mocks=None) -> Finding | None:
        t = a.test
        const_map = const_map or {}
        bare_mocks = bare_mocks or set()

        # constant-assert
        truth = const_truthiness(t)
        if truth is True:
            return Finding(path, a.lineno, fn.name, "constant-assert", "proven", "safe",
                           f"`assert {ast.unparse(t)}` is a constant truth — can never fail", node=a)

        # boundary-tautology: len(x) >= 0 / len(x) > -1
        if isinstance(t, ast.Compare) and len(t.ops) == 1 \
                and isinstance(t.left, ast.Call) and call_name(t.left) == "len" \
                and isinstance(t.comparators[0], ast.Constant):
            v = t.comparators[0].value
            if (isinstance(t.ops[0], ast.GtE) and v == 0) or (isinstance(t.ops[0], ast.Gt) and v == -1):
                return Finding(path, a.lineno, fn.name, "boundary-tautology", "proven", "safe",
                               "len() can never be negative — the comparison always holds", node=a)

        # local-const-echo: x = 5; ... assert x == 5
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Eq, ast.Is)):
            l, r = t.left, t.comparators[0]
            for name_side, lit_side in ((l, r), (r, l)):
                if isinstance(name_side, ast.Name) and name_side.id in const_map \
                        and isinstance(lit_side, ast.Constant) \
                        and const_map[name_side.id] == lit_side.value \
                        and type(const_map[name_side.id]) is type(lit_side.value):
                    return Finding(path, a.lineno, fn.name, "local-const-echo", "proven", "safe",
                                   f"{name_side.id} is bound to the literal {lit_side.value!r} in this test and never "
                                   "reassigned — the test asserts its own arrangement", node=a)

        # self-compare-call: assert f(a) == f(a)
        # (skipped when the test name says it's a deliberate determinism check)
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Eq, ast.Is)) \
                and not is_simple_chain(t.left) and ast.dump(t.left) == ast.dump(t.comparators[0]) \
                and any(isinstance(n, ast.Call) for n in ast.walk(t.left)) \
                and not re.search(r"stable|determin|consistent|idempotent|same|pure", fn.name, re.I):
            return Finding(path, a.lineno, fn.name, "self-compare-call", "advisory", "report-only",
                           f"compares `{ast.unparse(t.left)[:60]}` to an identical expression — equal by "
                           "construction unless nondeterministic", node=a)
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Eq, ast.Is)):
            l, r = t.left, t.comparators[0]
            if is_simple_chain(l) and ast.dump(l) == ast.dump(r):
                return Finding(path, a.lineno, fn.name, "constant-assert", "proven", "safe",
                               f"compares {ast.unparse(l)} to itself", node=a)
            # mock-echo (proven): assert m() == V where m is a bare in-test mock
            # stubbed with V — this can only test the mock library itself
            for call_side, val_side in ((l, r), (r, l)):
                if isinstance(call_side, ast.Call) and isinstance(call_side.func, ast.Name) \
                        and call_side.func.id in bare_mocks \
                        and stubs.get(call_side.func.id) == ast.dump(val_side):
                    return Finding(path, a.lineno, fn.name, "mock-echo", "proven", "safe",
                                   f"{call_side.func.id} is a bare mock created in this test and stubbed with "
                                   "this exact value — the assertion tests the mock library", node=a)
            # mock-echo (advisory): the asserted value matches a stubbed return_value
            # but flows through other code — might still test real pass-through logic
            for side in (l, r):
                if ast.dump(side) in stubs.values():
                    return Finding(path, a.lineno, fn.name, "mock-echo", "advisory", "report-only",
                                   "compares against the exact value stubbed into a mock's return_value — "
                                   "likely echoes the mock rather than testing logic", node=a)

        # type probes (filled in by the mypy pass)
        indent = len(lines[a.lineno - 1]) - len(lines[a.lineno - 1].lstrip())

        def queue(kind, expr_node, extra):
            try:
                expr_src = " ".join(ast.unparse(expr_node).split())
            except Exception:
                return None
            if file_has_cast:
                return None  # cast()/type:ignore in file — types may lie, stay silent
            p = Probe(path, a.lineno, indent, expr_src, kind, extra)
            p.finding_slot = (fn.name, a)
            probes.append(p)
            return p

        # assert x is not None
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.IsNot) \
                and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value is None:
            return "QUEUED" if queue("not-none", t.left, None) else None

        # assert isinstance(x, T)
        if isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id == "isinstance" \
                and len(t.args) == 2:
            cls = t.args[1]
            names = []
            for c in (cls.elts if isinstance(cls, ast.Tuple) else [cls]):
                if isinstance(c, ast.Name):
                    names.append(c.id)
                elif isinstance(c, ast.Attribute):
                    names.append(c.attr)
            if names:
                return "QUEUED" if queue("isinstance", t.args[0], names) else None
            return None

        # assert type(x) is T  — advisory even when types match (subclass caveat)
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Is, ast.Eq)) \
                and isinstance(t.left, ast.Call) and isinstance(t.left.func, ast.Name) \
                and t.left.func.id == "type" and len(t.left.args) == 1:
            c = t.comparators[0]
            nm = c.id if isinstance(c, ast.Name) else (c.attr if isinstance(c, ast.Attribute) else None)
            if nm:
                return "QUEUED" if queue("exact-type", t.left.args[0], [nm]) else None
            return None

        return None

    tests_in(tree.body, os.path.relpath(path, root))
    return records


# ------------------------------------------------------------------ mypy pass
def strip_generics(t: str) -> str:
    return t.split("[", 1)[0]


def base_name(t: str) -> str:
    t = t.strip().rstrip("*").strip('"')
    if t.startswith("Literal["):
        inner = t[len("Literal["):-1]
        if inner and (inner[0] in "'\""):
            return "str"
        if inner in ("True", "False"):
            return "bool"
        if re.fullmatch(r"-?\d+", inner):
            return "int"
        return "Literal"
    t = strip_generics(t)
    return t.split(".")[-1]


def union_members(t: str) -> list[str]:
    t = t.strip().rstrip("*")
    if t.startswith("Union[") and t.endswith("]"):
        inner, out, depth, cur = t[6:-1], [], 0, ""
        for ch in inner:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        out.append(cur.strip())
        return out
    if " | " in t:
        # split on top-level pipes only
        out, depth, cur = [], 0, ""
        for ch in t:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "|" and depth == 0:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        out.append(cur.strip())
        return out
    if t.startswith("Optional[") and t.endswith("]"):
        return [t[9:-1].strip(), "None"]
    return [t]


def enclosing_function_names(sites: set[tuple[str, int]]) -> set[str]:
    """Map (file, line) sites to the names of their enclosing functions."""
    names: set[str] = set()
    by_file: dict[str, list[int]] = {}
    for f, ln in sites:
        by_file.setdefault(f, []).append(ln)
    for f, lns in by_file.items():
        try:
            tree = ast.parse(open(f, encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.end_lineno:
                if any(n.lineno <= ln <= n.end_lineno for ln in lns):
                    names.add(n.name)
    return names


def run_mypy_probes(probes: list[Probe], root: str,
                    mypy_cmd: list[str] | None) -> tuple[str | None, set[str]]:
    """Insert reveal_type() shadow files, run mypy once, map notes back.

    Also collects [no-any-return] warnings: a function whose body returns
    unvalidated Any has an UNENFORCED return annotation — a runtime isinstance
    or None-check on its result is real regression coverage, not redundancy.
    Returns (note, set of Any-laundering function names)."""
    if not probes:
        return None, set()
    by_file: dict[str, list[Probe]] = {}
    for p in probes:
        by_file.setdefault(p.file, []).append(p)

    shadow_map = {}   # shadow_path -> {shadow_line: probe}
    shadow_files = []
    try:
        for file, plist in by_file.items():
            src_lines = open(file, encoding="utf-8").read().splitlines()
            plist.sort(key=lambda p: p.line)
            out_lines, li, inserted = [], 0, 0
            line_map = {}
            for p in plist:
                while li < p.line - 1:
                    out_lines.append(src_lines[li])
                    li += 1
                out_lines.append(" " * p.indent + f"reveal_type(({p.expr_src}))")
                inserted += 1
                line_map[len(out_lines)] = p
            out_lines.extend(src_lines[li:])
            shadow = os.path.join(os.path.dirname(file),
                                  "_cap_obv_shadow_" + os.path.basename(file))
            with open(shadow, "w", encoding="utf-8") as f:
                f.write("\n".join(out_lines) + "\n")
            shadow_files.append(shadow)
            shadow_map[os.path.abspath(shadow)] = line_map

        cmds = [mypy_cmd] if mypy_cmd else []
        if not cmds:
            if os.path.exists(os.path.join(root, "uv.lock")) and shutil.which("uv"):
                cmds.append(["uv", "run", "mypy"])
            if shutil.which("mypy"):
                cmds.append(["mypy"])
            cmds.append([sys.executable, "-m", "mypy"])

        # mypy only reports errors for explicitly-listed targets, so the source
        # tree must be in the run for [no-any-return] laundering detection
        src_targets = []
        if os.path.isdir(os.path.join(root, "src")):
            src_targets.append("src")
        else:
            for d in sorted(os.listdir(root)):
                if d in SKIP_DIRS or d.startswith(".") or d in ("tests", "test"):
                    continue
                if os.path.isfile(os.path.join(root, d, "__init__.py")):
                    src_targets.append(d)

        proc = None
        for cmd in cmds:
            try:
                proc = subprocess.run(
                    cmd + ["--no-error-summary", "--no-pretty",
                           "--check-untyped-defs",  # test functions are rarely annotated
                           "--warn-return-any",     # detect unenforced return annotations
                           "--show-error-codes",
                           "--show-column-numbers"] + shadow_files + src_targets,
                    cwd=root, capture_output=True, text=True, timeout=600)
                if "Revealed type" in proc.stdout or proc.returncode in (0, 1):
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                proc = None
                continue
        if proc is None:
            return ("mypy not runnable — type-guaranteed checks skipped (install mypy or pass --mypy)", set())

        any_return_sites: set[tuple[str, int]] = set()
        for line in proc.stdout.splitlines():
            m = REVEAL_RE.match(line)
            if m:
                fpath, lno, revealed = os.path.abspath(os.path.join(root, m.group(1))), int(m.group(2)), m.group(3)
                if fpath not in shadow_map:
                    fpath = os.path.abspath(m.group(1))
                probe = shadow_map.get(fpath, {}).get(lno)
                if probe:
                    probe.revealed = revealed
                continue
            m = ANY_RETURN_RE.match(line)
            if m:
                fpath = os.path.abspath(os.path.join(root, m.group(1)))
                if not os.path.exists(fpath):
                    fpath = os.path.abspath(m.group(1))
                any_return_sites.add((fpath, int(m.group(2))))
        return None, enclosing_function_names(any_return_sites)
    finally:
        for s in shadow_files:
            try:
                os.remove(s)
            except OSError:
                pass


def resolve_probes(probes: list[Probe], records: list[TestRecord], root: str,
                   laundering: set[str] | None = None):
    laundering = laundering or set()
    recs_by_key = {}
    for r in records:
        recs_by_key.setdefault((r.file, r.name), r)

    def touches_laundering(p: Probe, rec: TestRecord) -> bool:
        """The probed value comes (directly or via one assignment) from a
        function that returns unvalidated Any — its annotation is a promise,
        not a check, so the runtime assertion is real coverage."""
        if not laundering:
            return False
        try:
            expr = ast.parse(p.expr_src, mode="eval").body
        except SyntaxError:
            return True  # can't reason — stay safe
        called = {call_name(n) for n in ast.walk(expr) if isinstance(n, ast.Call)}
        for n in ast.walk(expr):
            if isinstance(n, ast.Name):
                for d in walk_no_nested_funcs(rec.node):
                    if isinstance(d, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == n.id for t in d.targets):
                        called |= {call_name(c) for c in ast.walk(d.value) if isinstance(c, ast.Call)}
        return bool((called - {None}) & laundering)

    for p in probes:
        rec = recs_by_key.get((p.file, p.finding_slot[0]))
        if rec is None:
            continue
        a = p.finding_slot[1]
        f = None
        if p.revealed and touches_laundering(p, rec):
            rec.nonredundant += 1
            continue
        if p.revealed:
            members = union_members(p.revealed)
            bad = any(base_name(m) in ("Any", "") for m in members)
            if p.kind == "not-none" and not bad and "None" not in [base_name(m) for m in members]:
                f = Finding(p.file, p.line, rec.name, "type-guaranteed", "proven", "safe",
                            f'mypy already guarantees non-None (revealed type: "{p.revealed}")', node=a)
            elif p.kind == "isinstance" and not bad \
                    and all(base_name(m) in p.extra for m in members):
                # every union member is one of the isinstance classes — always true
                f = Finding(p.file, p.line, rec.name, "type-guaranteed", "proven", "safe",
                            f'mypy already guarantees isinstance (revealed type: "{p.revealed}")', node=a)
            elif p.kind == "exact-type" and not bad and len(members) == 1 \
                    and base_name(members[0]) in p.extra:
                f = Finding(p.file, p.line, rec.name, "type-guaranteed", "advisory", "aggressive",
                            f'revealed type is "{p.revealed}", but a subclass instance would still fail type() is — advisory', node=a)
        if f is not None:
            rec.findings.append(f)
            if f.deletable in ("safe", "aggressive"):
                rec.deletable_stmt_nodes.append(a)
        else:
            rec.nonredundant += 1


# ------------------------------------------------------------------ duplicates
def _name_tokens(name: str) -> set[str]:
    # split on underscores/camelCase, drop the leading `test` marker and pure digits
    words = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name)
    return {w.lower() for w in words if w.lower() not in ("test",) and not w.isdigit()}


def _names_diverge(a: str, b: str) -> bool:
    # True when two identical-bodied tests carry materially different names — a
    # strong signal of a copy-paste bug where the second test's *named* behaviour
    # is silently untested (e.g. `..._none_type_returns_unchanged` whose body is a
    # verbatim copy of `..._request_dict`). Still coverage-safe to delete, but the
    # report should say so loudly rather than call it harmless redundancy.
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    return overlap < 0.6


def mark_duplicates(records: list[TestRecord]):
    seen = {}
    for rec in records:
        try:
            body_dump = ast.dump(ast.Module(body=rec.node.body, type_ignores=[]))
            # decorators are part of the test's identity: two @parametrize tests
            # can share a body but run entirely different cases
            deco_dump = "|".join(ast.dump(d) for d in rec.node.decorator_list)
        except Exception:
            continue
        if len(body_dump) < 60:
            continue
        key = (rec.file, rec.scope_key, deco_dump, body_dump)
        if key in seen:
            first = seen[key]
            rec.is_duplicate = True
            reason = f'body is identical to "{first.name}" (line {first.node.lineno}) in the same scope'
            if _names_diverge(rec.name, first.name):
                reason += (' — names differ, so this is likely a copy-paste that leaves '
                           "this test's named behaviour untested; deleting is coverage-safe "
                           'but consider fixing the body instead')
            rec.findings.append(Finding(
                rec.file, rec.node.lineno, rec.name, "duplicate-test", "proven", "safe", reason))
        else:
            seen[key] = rec


# ------------------------------------------------------------------ fix
def apply_fix(records: list[TestRecord], aggressive: bool, root: str):
    removals_by_file: dict[str, list[tuple[int, int]]] = {}
    tests_removed, asserts_removed = 0, 0

    def want(f: Finding) -> bool:
        return f.deletable == "safe" or (aggressive and f.deletable == "aggressive")

    for rec in records:
        whole = False
        if rec.is_duplicate and any(f.category == "duplicate-test" and want(f) for f in rec.findings):
            whole = True
        elif any(f.category == "never-asserts" and want(f) for f in rec.findings):
            whole = True
        elif any(f.category in ("no-assert", "skipped-test") and want(f) for f in rec.findings):
            whole = True
        else:
            deletable = [f for f in rec.findings if f.node is not None and want(f)
                         and f.category != "dead-assert"]
            if (rec.live_assert_count > 0 and len(deletable) == rec.live_assert_count
                    and rec.nonredundant == 0 and rec.conditional == 0 and rec.helper_asserts == 0):
                whole = True

        spans = removals_by_file.setdefault(rec.file, [])
        if whole:
            start = min([d.lineno for d in rec.node.decorator_list] + [rec.node.lineno])
            spans.append((start, rec.node.end_lineno))
            tests_removed += 1
        else:
            for f in rec.findings:
                if f.node is not None and want(f):
                    report_only_asserts = sum(1 for x in rec.findings
                                              if x.node is not None and x.deletable == "report-only")
                    ok_partial = (f.category == "dead-assert" or
                                  rec.nonredundant + rec.helper_asserts + rec.conditional
                                  + report_only_asserts > 0)
                    if ok_partial:
                        spans.append((f.node.lineno, f.node.end_lineno))
                        asserts_removed += 1

    files_changed = 0
    for file, spans in removals_by_file.items():
        if not spans:
            continue
        lines = open(file, encoding="utf-8").read().splitlines(keepends=True)
        drop = set()
        for s, e in spans:
            drop.update(range(s, e + 1))
        new = [l for i, l in enumerate(lines, 1) if i not in drop]
        with open(file, "w", encoding="utf-8") as fh:
            fh.writelines(new)
        files_changed += 1
    return {"testsRemoved": tests_removed, "assertionsRemoved": asserts_removed,
            "filesChanged": files_changed}


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=".")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--aggressive", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--mypy", help='mypy command, e.g. "uv run mypy"')
    ap.add_argument("--no-types", action="store_true", help="skip the mypy pass")
    args = ap.parse_args()

    root = os.path.abspath(args.path)
    files = find_test_files(root)
    if not files:
        print(f"captain-obvious: no test files (test_*.py / *_test.py) under {root}")
        return 0

    probes: list[Probe] = []
    records: list[TestRecord] = []
    for f in files:
        try:
            src = open(f, encoding="utf-8").read()
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"  skipping {f}: {e}", file=sys.stderr)
            continue
        analyze_file(f, src, tree, root, probes, records)

    mypy_note = None
    laundering: set[str] = set()
    if args.no_types:
        for p in probes:
            p.revealed = None
        mypy_note = "type checks skipped (--no-types)"
    else:
        mypy_note, laundering = run_mypy_probes(probes, root, args.mypy.split() if args.mypy else None)
    resolve_probes(probes, records, root, laundering)

    mark_duplicates(records)

    findings = [f for r in records for f in r.findings]
    summary: dict[str, dict[str, int]] = {}
    for f in findings:
        summary.setdefault(f.category, {"proven": 0, "advisory": 0})[f.level] += 1

    fixed = apply_fix(records, args.aggressive, root) if args.fix else None

    report = {
        "tool": "captain-obvious/py",
        "project": root,
        "mypyNote": mypy_note,
        "testFilesScanned": len(files),
        "testsScanned": len(records),
        "findings": [f.to_dict(root) for f in findings],
        "summary": summary,
        "aggressive": args.aggressive,
        "fixed": fixed,
    }
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)

    print(f"\ncaptain-obvious (py) — {len(records)} tests scanned in {len(files)} files")
    if mypy_note:
        print(f"  note: {mypy_note}")
    print()
    for cat, c in summary.items():
        print(f"  {cat:<20} proven: {c['proven']}  advisory: {c['advisory']}")
    if findings:
        print("\nFindings:")
        for f in findings:
            tag = "PROVEN  " if f.level == "proven" else "ADVISORY"
            print(f"  [{tag}] {os.path.relpath(f.file, root)}:{f.line} ({f.category}) \"{f.test}\"")
            print(f"             {f.reason}")
    if fixed:
        print(f"\nFixed: removed {fixed['testsRemoved']} tests and {fixed['assertionsRemoved']} assertions "
              f"across {fixed['filesChanged']} files.")
        print("Re-run your typechecker and test suite now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
