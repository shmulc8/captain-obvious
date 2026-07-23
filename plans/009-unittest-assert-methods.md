# Plan 009: Detect redundant `unittest.TestCase` assert-methods (report-only first stage)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/co_py/analyzer.py tests/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (touches the central Python analyzer; mitigated by the
  report-only constraint below)
- **Depends on**: plans/001-fixer-line-integrity.md (not for code — for the
  fixer invariants its tests pin; land 001 first)
- **Category**: direction
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

`unittest.TestCase` is one of the two dominant Python test styles, and today
it gets ZERO redundancy findings from the flagship categories. The tool's own
headline example — `assert isinstance(add(1, 2), int)` on a typed function —
is invisible when written as `self.assertIsInstance(add(1, 2), int)`.
`self.assertTrue(True)`, `self.assertEqual(1, 1)`, `self.assertEqual(x, x)`
are all invisible too: `classify_assert` accepts only bare `ast.Assert`
nodes, and `self.assertX` calls are merely *counted* so the test isn't
flagged `no-assert`.

**Hard constraint for this stage**: unittest-derived findings are
REPORT-ONLY — `deletable` is forced to `"report-only"` regardless of what
the classifier says, so `--fix` never auto-deletes them. Removing an
`Expr(Call)` statement safely (vs an `ast.Assert`) needs its own fixer work;
that is explicitly deferred. This stage delivers the detection value at zero
deletion risk.

## Current state

- `co_py/analyzer.py:130-140` — where assert-method calls are recognized and
  counted (and nothing else):

```python
            elif isinstance(d, ast.Call):
                cn = call_name(d)
                if cn is None:
                    continue
                if isinstance(d.func, ast.Attribute) and cn.startswith("assert"):
                    assert_nodes.append(d)          # self.assertX / mock.assert_called*
```

- `co_py/analyzer.py:258-281` — the live-assert triage loop: anything that
  is not a top-level bare `ast.Assert` falls into
  `rec.nonredundant += 1` (line 280: "helper call, with-raises, self.assertX
  handled below, etc." — the "handled below" is aspirational; nothing below
  handles them).
- `co_py/analyzer.py:406-414` — the classification loop over
  `plain_live_asserts`, calling `classify_assert(a, fn, stubs, ...)`;
  `"QUEUED"` means a mypy probe was registered.
- `co_py/analyzer.py:420-540` — `classify_assert(a: ast.Assert, ...)`:
  works entirely off `a.test` (the asserted expression) and `a.lineno`;
  emits `constant-assert`, `boundary-tautology`, `local-const-echo`,
  `self-compare-call`, `mock-echo`, and queues `not-none` / `isinstance` /
  `exact-type` probes via `queue()` (line 493), which uses `a.lineno` and
  the line's indent for shadow-file insertion.
- `co_py/models.py:5-20` — `Finding` carries `node`; the fixer deletes by
  `f.node.lineno..end_lineno`. Because this stage forces `report-only`,
  `plan_removals` never selects these findings (`want()` at `fixer.py:57-58`
  requires `deletable == "safe"`).
- Probe mechanics: `Probe.finding_slot = (fn.name, a)` and `resolve_probes`
  attaches the finding to the record — works for any node carrying `lineno`.
- Fake-mypy test pattern for type-guaranteed assertions:
  `tests/test_flat_layout_laundering.py:47-65` (builds a fake mypy script,
  passes `--mypy <fake>`).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `python3 -m unittest discover -v tests` | `OK` |
| New tests | `python3 -m unittest tests.test_unittest_asserts -v` | `OK` |

No TypeScript needed (Python-only feature; TS has no unittest analog — note
this in the PR as the CLAUDE.md parity-rule exemption).

## Scope

**In scope**:

- `skills/captain-obvious/scripts/co_py/analyzer.py`
- `tests/test_unittest_asserts.py` (create)
- `skills/captain-obvious/references/detectors.md` +
  `README.md` (one sentence each noting unittest assert-method support,
  report-only)
- `plans/README.md` (status row)

**Out of scope**:

- `co_py/fixer.py` — NO fixer changes; findings are report-only by
  construction this stage.
- `mypy_pass.py`, `models.py` — the probe/finding machinery works as-is.
- The TS engine.
- assert-methods beyond the five below (assertGreater, assertIn, ~20 others)
  — explicitly deferred; do not chase them.

## Git workflow

- Branch: `feat/unittest-assert-methods`
- Conventional commit: `feat(py): classify unittest assert-methods (report-only)`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Canonicalizer

Add to `analyzer.py` (module level, near `_is_noise_call`):

```python
_UNITTEST_EQ = {"assertEqual", "assertIs"}
_UNITTEST_TRUE = {"assertTrue"}
_UNITTEST_ISINSTANCE = {"assertIsInstance"}
_UNITTEST_NONE = {"assertIsNone", "assertIsNotNone"}

def _unittest_call_to_assert(call: ast.Call) -> ast.Assert | None:
    """Map self.assert<X>(...) to an equivalent synthetic ast.Assert whose
    .test mirrors the bare-assert form, so classify_assert and the mypy
    probe queue can reason about it. Returns None for unrecognized shapes.
    The synthetic node copies the call's location so findings and probes
    point at the real line."""
    if not (isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"):
        return None
    name = call.func.attr
    args = call.args
    t = None
    if name in _UNITTEST_EQ and len(args) == 2:
        t = ast.Compare(left=args[0],
                        ops=[ast.Eq() if name == "assertEqual" else ast.Is()],
                        comparators=[args[1]])
    elif name in _UNITTEST_TRUE and len(args) == 1:
        t = args[0]
    elif name in _UNITTEST_ISINSTANCE and len(args) == 2:
        t = ast.Call(func=ast.Name(id="isinstance", ctx=ast.Load()),
                     args=[args[0], args[1]], keywords=[])
    elif name in _UNITTEST_NONE and len(args) == 1:
        cmp_op = ast.Is() if name == "assertIsNone" else ast.IsNot()
        t = ast.Compare(left=args[0], ops=[cmp_op],
                        comparators=[ast.Constant(value=None)])
    if t is None:
        return None
    a = ast.Assert(test=t, msg=None)
    for n in ast.walk(a):
        ast.copy_location(n, call)
    ast.copy_location(a, call)
    a.end_lineno = call.end_lineno
    return a
```

Notes: `assertFalse` is deliberately absent (its `not x` form has no proven
category to hit and only adds surface); a `msg=` second/third positional or
keyword argument means `len(args)` checks above simply skip the call —
acceptable conservatism, note it in the docstring if you like.

**Verify**:
`python3 -c "import sys, ast; sys.path.insert(0, 'skills/captain-obvious/scripts'); from co_py.analyzer import _unittest_call_to_assert as u; c = ast.parse('self.assertEqual(1, 1)').body[0].value; a = u(c); assert isinstance(a, ast.Assert) and isinstance(a.test, ast.Compare); print('ok')"` → `ok`

### Step 2: Route recognized assert-methods through the classifier

In the triage loop (`analyzer.py:258-281`), the `else:` branch currently
does `rec.nonredundant += 1` for every non-bare-assert node. Change it to
attempt canonicalization first — ONLY for live, top-level statements (mirror
the `isinstance(a, ast.Assert) and ts is a` positional condition of the
bare-assert branch: require `ts is not None and ts.value is a` where `ts` is
the enclosing `ast.Expr` statement — i.e. the call IS the whole statement,
not nested inside something):

```python
            else:
                synth = None
                if isinstance(a, ast.Call) and ts is not None \
                        and isinstance(ts, ast.Expr) and ts.value is a:
                    synth = _unittest_call_to_assert(a)
                if synth is not None:
                    unittest_asserts.append(synth)
                else:
                    rec.nonredundant += 1  # helper call, with-raises, etc.
```

Add `unittest_asserts = []` next to the `plain_live_asserts = []` init.
Then, after the existing classification loop over `plain_live_asserts`
(lines 406-414), add a second loop:

```python
        for a in unittest_asserts:
            # throwaway probe list ([], NOT the real `probes`): this stage does
            # not queue mypy probes for unittest asserts, so assertIsInstance/
            # assertIsNotNone produce no finding here — see the Decision below.
            f = classify_assert(a, fn, stubs, fn_has_cast(fn), const_map,
                                bare_mocks, path, [])
            if f is None or f == "QUEUED":
                # f is None → genuinely nonredundant. "QUEUED" → a probe was
                # appended to the throwaway list and can never resolve, so it
                # is nonredundant too, NOT a finding — nothing type-guaranteed
                # slips through as deletable="safe".
                rec.nonredundant += 1
            else:
                f.deletable = "report-only"   # stage 1: never auto-delete
                f.reason += " (unittest assert-method — auto-fix not yet supported)"
                rec.findings.append(f)
```

Two invariants to preserve — read before editing:

1. `rec.live_assert_count` must NOT include unittest asserts (it feeds the
   whole-test-removal arithmetic in `fixer.py:69-70`; unittest-heavy tests
   must never become whole-test removable through this stage). The existing
   code sets it from `len(plain_live_asserts)` — leave that line untouched.
2. A classified unittest assert must not ALSO count as `nonredundant` (it
   previously did, unconditionally). The reconciled loop above moves that
   increment into the `f is None or f == "QUEUED"` arm — that is the intended
   accounting change: a proven-redundant `self.assertTrue(True)` no longer
   props up `rec.nonredundant`. Consequence to verify in tests: a test whose
   ONLY content is `self.assertTrue(True)` now yields a `constant-assert`
   advisory-equivalent finding (report-only) and `nonredundant == 0`, but is
   still NOT deletable (live_assert_count is 0, and `want()` rejects
   report-only).

**Decision (keep it simple, stay in scope): do NOT queue mypy probes for
unittest asserts in this stage** — this is why the loop above passes a
throwaway `[]` for `probes`, not the real list. Forcing report-only on a
*probe-resolved* finding would need a marker that survives `resolve_probes`
(a `Probe`/`models.py` field), which drags in `mypy_pass.py` — out of scope
this stage. Passing `[]` sidesteps it entirely: `classify_assert`'s queue
path still returns `"QUEUED"`, but the probe is appended to the throwaway
list and dies there, so `assertIsInstance`/`assertIsNotNone` yield no finding
now — the reconciled loop counts `"QUEUED"` as `nonredundant` since the probe
can never resolve. The `type-guaranteed`-for-unittest capability moves to the
deferred stage 2 with the fixer work. This keeps `mypy_pass.py` untouched and
the stage honest: syntactic categories only.

**Verify**: `python3 -m unittest discover -v tests` → `OK` (existing suite
green — especially `tests/test_never_asserts.py`, which exercises the
counting arithmetic this step touches).

### Step 3: Tests

Create `tests/test_unittest_asserts.py` (pattern `tests/test_fix_plan.py`,
`--no-types`). Fixture:

```python
import unittest
from app import compute

class TestApp(unittest.TestCase):
    def test_const(self):
        self.assertTrue(True)

    def test_eq_literal(self):
        self.assertEqual(1, 1)

    def test_self_eq(self):
        x = compute()
        self.assertEqual(x, x)

    def test_real(self):
        self.assertEqual(compute(), "expected")
```

NOTE the discovery caveat: `tests_in` (`analyzer.py:58-64`) only descends
into `ast.ClassDef` whose name starts with `Test` — `TestApp` qualifies.

Assert:

1. `test_const` → finding `constant-assert`, `level == "proven"`,
   `deletable == "report-only"`, reason ends with the
   "(unittest assert-method — auto-fix not yet supported)" suffix.
2. `test_eq_literal` → `constant-assert`, `proven`, `report-only`.
3. `test_self_eq` → `constant-assert` (compares a simple chain to itself),
   `report-only`.
4. `test_real` → NO finding.
5. `report["plan"]["testsToRemove"] == []` and
   `report["plan"]["assertionsToRemove"] == 0` — nothing became deletable.
6. Run `--fix --force` on the fixture dir and assert the file content is
   byte-identical before/after — `--fix` is provably inert on these.

**Verify**: `python3 -m unittest tests.test_unittest_asserts -v` → `OK`.

### Step 4: Docs

- `references/detectors.md`: in the `constant-assert` row (line 15), append
  "; unittest assert-methods (`assertEqual(1, 1)`, `assertTrue(True)`,
  `assertEqual(x, x)`) are classified too, always report-only".
- `README.md` detector table `constant-assert` row: append
  "(incl. `self.assertEqual(1, 1)`-style unittest methods, report-only)".

**Verify**: `rg -n "unittest assert-method" skills/captain-obvious/references/detectors.md README.md` → ≥1 each.

## Test plan

- `tests/test_unittest_asserts.py` — 6 cases (step 3).
- Regression: full suite green; `tests/test_never_asserts.py` and
  `tests/test_fix_plan.py` in particular (counting + plan arithmetic).

## Done criteria

- [ ] `python3 -m unittest discover -v tests` exits 0
- [ ] Fixture from step 3 produces the three report-only findings and zero plan entries
- [ ] `rg -n "_unittest_call_to_assert" skills/captain-obvious/scripts/co_py/analyzer.py` → definition + 1 call site
- [ ] `rg -n "auto-fix not yet supported" skills/captain-obvious/scripts/co_py/analyzer.py` → 1 match
- [ ] No changes to `fixer.py` / `mypy_pass.py` / `models.py` (`git diff --stat` clean there)
- [ ] `plans/README.md` status row updated

## STOP conditions

- Any pre-existing test fails after step 2 and the cause isn't an obvious
  typo — the counting invariants are subtler than modeled; report which test
  and the diff of the relevant counts.
- You find yourself editing `fixer.py` or `mypy_pass.py` — that is stage 2
  scope creep; stop and note what pushed you there.
- `--fix --force` on the step-3 fixture modifies the file at all.

## Maintenance notes

- **Stage 2 (deferred, needs its own plan)**: (a) `Expr(Call)` removal
  support in `fixer.py` so proven unittest findings can graduate to
  `deletable: safe`; (b) probe queueing for `assertIsInstance`/
  `assertIsNotNone` → `type-guaranteed` (needs a report-only marker that
  survives `resolve_probes`, i.e. a `Probe` field — touch `models.py` +
  `mypy_pass.py` then). Do not start it before plan 001's fixer tests are in.
- The five-method allowlist is a floor, not a ceiling — extend only with a
  fixture per added method.
- Reviewer focus: `rec.live_assert_count` must remain bare-assert-only.
