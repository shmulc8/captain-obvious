# Plan 003: Self-test the auto-delete detectors — TS type-guaranteed (+ its guards), mock-echo / boundary-tautology / local-const-echo, and all coverage formats

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/ tests/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW (tests only — no production code changes)
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

Findings with `deletable: "safe"` are auto-deleted by `--fix` with no human
in the loop. Today three groups of that machinery have **zero self-tests**:

1. The **TS `type-guaranteed` classifier** (`co_ts/classifier.mjs`, 245
   lines) — the tool's flagship "ask the compiler" path — including the
   guards that prevent over-deletion (`as` casts, index signatures, unchecked
   index access). A regression in a guard silently auto-deletes real tests.
2. **`mock-echo`, `boundary-tautology`, `local-const-echo`** — all emit
   `deletable: safe` on both sides; `rg` finds no test naming any of them.
3. **Coverage parsing**: the TS promote/suppress path
   (`captain_obvious_ts.mjs:158-187` + `co_ts/coverage.mjs`) is untested, and
   the Python side (`tests/test_coverage_inert_warning.py`) feeds only 1 of
   the 3 supported formats. A parser bug on an unexercised format changes
   what `--fix` deletes (coverage promotes `conditional-assert` to proven).

This plan adds report-only fixtures asserting `category`, `level`, and
`deletable` per case — no production code changes at all.

## Current state

Facts the tests rely on (verified at `0eaabb5`):

- The TS CLI enables type checks only when it finds a `tsconfig.json`
  (`captain_obvious_ts.mjs:119-132`): `typesAvailable = !!tsconfigPath`, and
  `strictNull = options.strictNullChecks ?? options.strict ?? false`. So the
  TS type-guaranteed fixtures need a tsconfig with `"strict": true`.
- `co_ts/classifier.mjs` decision points the fixtures target:
  - `typeof` proven: `classifier.mjs:117-131` — `expect(typeof x).toBe('number')`
    with `x` typed `number` → `{category: 'type-guaranteed', level: 'proven', deletable: 'safe'}`
    when the subject contains no call.
  - `hasUnsafeCast` guard: `classifier.mjs:125,133` — any `as` cast in the
    operand → return `null` (NOT flagged at all).
  - index-signature guard: `classifier.mjs:144-146` — `toBeDefined()` on a
    property from an index signature → advisory
    (`deletable: 'aggressive'`, reason mentions "index signature").
  - unchecked element access guard: `classifier.mjs:141-143` —
    `expect(arr[0]).toBeDefined()` without `noUncheckedIndexedAccess` →
    advisory (reason mentions "indexed access").
- `co_ts/mock_echo.mjs`:
  - direct stub echo (`mock_echo.mjs:59-68`): `m.mockReturnValue(5)` then
    `expect(m()).toBe(5)` → proven `mock-echo`.
  - self-call echo (`mock_echo.mjs:45-57`): `m();` then
    `expect(m).toHaveBeenCalled()` → proven `mock-echo`.
- `co_py/analyzer.py` classify branches:
  - boundary (`analyzer.py:433-440`): `assert len(x) >= 0` → proven
    `boundary-tautology`.
  - local-const-echo (`analyzer.py:442-452`): `x = 5` (single assignment)
    then `assert x == 5` → proven.
  - mock-echo proven (`analyzer.py:468-476`): `m = MagicMock();
    m.return_value = 5; assert m() == 5` → proven (requires the bare-mock
    ctor to be literally `MagicMock`/`Mock`/`AsyncMock`).
  - mock-echo advisory (`analyzer.py:477-483`): asserted side equals a
    stubbed value but isn't a direct bare-mock call → advisory.
- TS `boundary-tautology` / `local-const-echo` are syntactic
  (`classifier.mjs:29-48`) — they fire with or without tsconfig.
- Coverage promote/suppress contract (both CLIs, identical): a
  `conditional-assert` finding whose line has `hits == 0` → `level` becomes
  `proven` and reason gains "coverage confirms it ran 0 times"; `hits > 0` →
  finding dropped; report `coverage.conditionalAssertsPromoted` /
  `conditionalAssertsSuppressed` count them. Python reference behavior is
  already pinned in `tests/test_coverage_inert_warning.py` (coverage.py-json
  format only).
- Supported coverage formats (`co_py/coverage.py:29-56`,
  `co_ts/coverage.mjs:17-44`): coverage.py JSON (`{"files": {path:
  {executed_lines, missing_lines}}}`), istanbul JSON (`{path:
  {statementMap: {id: {start:{line}}}, s: {id: hits}, path}}`), lcov text
  (`SF:<path>` / `DA:<line>,<hits>` / `end_of_record`).
- A Python `conditional-assert` fixture that fires is pinned in
  `tests/test_coverage_inert_warning.py:22-31` (assert guarded by
  `if sys.platform == "win32":`). The TS analog: an `expect` inside
  `if (process.platform === "win32") { ... }` in a test body.

Conventions: unittest TestCase classes, tempdir + `subprocess.run` of the
CLIs, `--json` report parsing. Python exemplar: `tests/test_fix_plan.py`.
TS exemplar incl. the `_ts_resolvable()` skip guard and node invocation:
`tests/test_literal_tautology_ts.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| One-time TS setup | `npm install typescript@^5 --no-save --no-audit --no-fund` (repo root) | exit 0 |
| Full suite | `python3 -m unittest discover -v tests` | `OK`, zero `skipped` lines |
| New tests | `python3 -m unittest tests.test_type_guaranteed_ts tests.test_proven_detectors tests.test_proven_detectors_ts tests.test_coverage_formats -v` | `OK` |

## Scope

**In scope** (create only; modify nothing in `skills/`):

- `tests/test_type_guaranteed_ts.py`
- `tests/test_proven_detectors.py` (Python detectors)
- `tests/test_proven_detectors_ts.py` (TS syntactic + mock-echo)
- `tests/test_coverage_formats.py` (both CLIs × three formats)
- `plans/README.md` (status row)

**Out of scope**:

- ANY change under `skills/captain-obvious/scripts/` — if a fixture exposes a
  real classifier bug, that is a STOP condition (report it; do not fix it
  here — a test plan must not smuggle behavior changes).
- Duplicating what exists: `constant-assert`/`self-compare-call` TS coverage
  lives in `tests/test_literal_tautology_ts.py`; don't re-test those.

## Git workflow

- Branch: `test/deletion-path-fixtures`
- Conventional commits, e.g. `test(ts): cover type-guaranteed proven/guard verdicts`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: TS type-guaranteed fixture project + tests

`tests/test_type_guaranteed_ts.py`, skip-guarded with `_ts_resolvable()`
copied from `tests/test_literal_tautology_ts.py:28-34`. `setUp` builds a
tempdir with:

`tsconfig.json`:

```json
{ "compilerOptions": { "strict": true, "target": "es2020", "module": "esnext" } }
```

`types.test.ts`:

```ts
interface Bag { [k: string]: string }
declare function getCount(): number;

test("tg proven", () => {
  const n = getCount();
  expect(typeof n).toBe("number");
});
test("tg cast guard", () => {
  expect(typeof (getCount() as any)).toBe("number");
});
test("tg index signature", () => {
  const b: Bag = { x: "1" };
  expect(b.x).toBeDefined();
});
test("tg element access", () => {
  const arr: string[] = ["a"];
  expect(arr[0]).toBeDefined();
});
```

Run `node CLI --project <dir> --json out.json`; index findings by `test`.
Assert:

1. report `typeChecksEnabled` is `true` (tsconfig was found — otherwise the
   whole fixture silently degrades and proves nothing).
2. `"tg proven"` → category `type-guaranteed`, level `proven`,
   deletable `safe`.
3. `"tg cast guard"` → NO finding at all (the `as` cast suppresses).
4. `"tg index signature"` → category `type-guaranteed`, level `advisory`,
   and `"index signature"` in `reason`.
5. `"tg element access"` → category `type-guaranteed`, level `advisory`, and
   `"ndexed access"` in `reason` (match case-insensitively or on the
   substring `"noUncheckedIndexedAccess"`).

**Verify**: `python3 -m unittest tests.test_type_guaranteed_ts -v` → `OK`.
If assertion 1 fails, fix the fixture (tsconfig discovery), not the scanner.
If 2-5 fail with assertion 1 passing, STOP — that's a real classifier
discrepancy; report the actual `category/level/reason` you got.

### Step 2: Python proven-detector fixtures

`tests/test_proven_detectors.py` (pattern `test_fix_plan.py`, `--no-types`).
One fixture file, one test method per case asserting
`(category, level, deletable)`:

```python
from unittest.mock import MagicMock
from app import compute

def test_boundary():
    data = compute()
    assert len(data) >= 0

def test_const_echo():
    x = 5
    assert x == 5

def test_mock_echo_direct():
    m = MagicMock()
    m.return_value = 5
    assert m() == 5

def test_mock_echo_indirect():
    m = MagicMock()
    m.return_value = 5
    result = compute(m)
    assert result == 5
```

Expected: `test_boundary` → (`boundary-tautology`, `proven`, `safe`);
`test_const_echo` → (`local-const-echo`, `proven`, `safe`);
`test_mock_echo_direct` → (`mock-echo`, `proven`, `safe`);
`test_mock_echo_indirect` → (`mock-echo`, `advisory`, `report-only`).

**Verify**: `python3 -m unittest tests.test_proven_detectors -v` → `OK`.

### Step 3: TS syntactic + mock-echo fixtures

`tests/test_proven_detectors_ts.py` — NO tsconfig needed (all syntactic;
also implicitly locks that these fire without type info). Fixture
`prov.test.ts`:

```ts
test("boundary", () => {
  const arr: number[] = [];
  expect(arr.length).toBeGreaterThanOrEqual(0);
});
test("const echo", () => {
  const expected = 5;
  expect(expected).toBe(5);
});
test("mock echo stub", () => {
  const m = jest.fn();
  m.mockReturnValue(5);
  expect(m()).toBe(5);
});
test("mock echo called", () => {
  const m = jest.fn();
  m();
  expect(m).toHaveBeenCalled();
});
```

Expected: `boundary` → (`boundary-tautology`, `proven`, `safe`);
`const echo` → (`local-const-echo`, `proven`, `safe`); both mock tests →
(`mock-echo`, `proven`, `safe`).

Also assert a same-file **duplicate** through the TS CLI: append two tests
with identical bodies (reuse the `test_fix_plan.py` duplicate shape in TS:
`expect(compute("a")).toEqual("b");` twice under different names, canonicalized
body key ≥ 8 chars — `co_ts/duplicates.mjs:15`) and assert the second yields
(`duplicate-test`, `proven`, `safe`) —
`markDuplicates` on the TS side is currently only exercised via the Python
CLI.

**Verify**: `python3 -m unittest tests.test_proven_detectors_ts -v` → `OK`.

### Step 4: Coverage-format matrix

`tests/test_coverage_formats.py`. Reuse the exact Python conditional fixture
from `tests/test_coverage_inert_warning.py:22-31` (assert at line 7 behind
`if sys.platform == "win32":`) and a TS analog `cond.test.ts`:

```ts
import { compute } from "./app";
test("conditional", () => {
  const result = compute();
  if (process.platform === "win32") {
    expect(result).toBe(5);
  }
});
```

(First run the TS CLI report-only on this fixture WITHOUT coverage and
assert a `conditional-assert` advisory finding exists, recording its
reported `line`; build the coverage inputs against that line. If no
`conditional-assert` is produced, STOP — the TS conditional detector's shape
differs from this plan's model; report the findings you got.)

For each scanner, three sub-tests — one per format, each encoding
"assert-line ran 0 times, other lines ran":

- coverage.py JSON: `{"files": {"<testfile>": {"executed_lines": [..all
  other lines..], "missing_lines": [<assert line>]}}}`
- istanbul JSON: `{"<abs testfile>": {"path": "<abs testfile>",
  "statementMap": {"0": {"start": {"line": <assert line>}}}, "s": {"0": 0}}}`
- lcov: `SF:<testfile>` / `DA:<assert line>,0` / `end_of_record`

Assert for each: `coverage.conditionalAssertsPromoted == 1` and the finding's
`level == "proven"`. Then one suppression case per scanner (any single
format, hits > 0 on the assert line): `conditionalAssertsSuppressed == 1` and
NO `conditional-assert` finding remains.

**Verify**: `python3 -m unittest tests.test_coverage_formats -v` → `OK`
(8 sub-cases: 3 formats + 1 suppression, × 2 scanners).

## Test plan

This plan IS the test plan; see steps. Final:
`python3 -m unittest discover -v tests` → all pass, count grows by ≥14.

## Done criteria

- [ ] `python3 -m unittest discover -v tests` exits 0 with zero `skipped` lines (typescript installed)
- [ ] `rg -l "type-guaranteed" tests/` now includes a TS-CLI-driven test file
- [ ] `rg -l "mock-echo|boundary-tautology|local-const-echo" tests/` non-empty
- [ ] `git status --porcelain` shows only new files under `tests/` + the plans index
- [ ] No modifications under `skills/` (`git diff --stat -- skills/` empty)
- [ ] `plans/README.md` status row updated

## STOP conditions

- Any fixture yields a DIFFERENT verdict than specified while its
  preconditions hold (e.g. `typeChecksEnabled: true` but "tg proven" is
  advisory) — that is a live classifier bug or a wrong plan model. Report the
  exact finding JSON; do not adjust the expected values to make it pass, and
  do not patch the scanner.
- The TS conditional-assert fixture produces no finding (step 4 pre-check).
- You need to edit anything under `skills/captain-obvious/scripts/`.

## Maintenance notes

- These fixtures pin the *verdict contract* (`category`, `level`,
  `deletable`) — the exact `reason` strings are asserted only where the
  guard identity matters (substring match). Keep it that way; full-string
  reason assertions would make every wording tweak a test failure.
- When a new proven detector is added, add its fixture here in the same
  breath — this file is now the place a reviewer checks for it.
- Plan 007's parity corpus overlaps intentionally at the syntactic level but
  asserts *cross-language agreement*, not per-detector depth. Keep both.
