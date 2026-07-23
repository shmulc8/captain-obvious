# Plan 006: Verify, then fix, the TS Any-laundering guard's missing transitivity

> **Executor instructions**: This is a VERIFY-THEN-FIX plan: step 1 decides
> whether the bug is real. Follow it step by step; run every verification
> command. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/co_ts/laundering.mjs skills/captain-obvious/scripts/co_ts/analyzer.mjs tests/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (touches the sole demotion gate on a proven-delete path; the
  fix direction is safe — it can only demote proven→advisory, i.e. fewer
  deletions)
- **Depends on**: none
- **Category**: bug (confidence MED — hence the verify stage)
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

"Annotation laundering" is this tool's own documented guard
(`references/detectors.md:57-69`): a function whose signature promises a
type but whose body launders an `any` (`return JSON.parse(x) as Foo`) makes
type-guaranteed assertions on its result REAL regression coverage — deleting
them would be over-deletion. The Python side propagates the laundering set
**transitively** to a fixpoint (`co_py/mypy_pass.py:89-124`, and
`detectors.md:64-67` explicitly documents the transitive fold). The TS side
(`co_ts/laundering.mjs`) inspects only the **directly-called** function's
body. Static reading says a one-level indirection (`outer()` returns
`inner()`, and `inner` does the cast) defeats the TS guard, leaving the
finding proven/safe — and `--fix` would then delete a real assertion. This
was NOT runtime-verified during the audit; step 1 settles it.

## Current state

- `skills/captain-obvious/scripts/co_ts/laundering.mjs` (41 lines, whole
  file relevant). `callLaunders(ts, checker, callExpr)` resolves the callee's
  declaration, requires `decl.body && decl.type` (annotated return), then
  walks ONLY that body's return statements:

```js
  walk(ts, decl.body, n => {
    if (launders) return;
    if (ts.isReturnStatement(n) && n.expression) {
      const t = checker.getTypeAtLocation(n.expression);
      if ((t.flags & ts.TypeFlags.Any) || hasUnsafeCast(ts, n.expression)) launders = true;
    }
  });
```

  For `outer(): Foo { return inner(); }` where `inner(): Foo { return
  JSON.parse("{}") as Foo; }`: the return expression `inner()` has declared
  type `Foo` (no `Any` flag) and contains no cast → `launders` stays false.
  Nothing recurses into `inner`.
- `subjectLaunders` (same file, lines 27-41) collects calls from the expect
  subject AND from the initializers of local variables the subject names,
  then ORs `callLaunders` over them — so the *variable-bound* form
  (`const v = outer(); expect(v).toBeDefined()`) reaches `callLaunders(outer)`.
- The gate's consumer: `co_ts/analyzer.mjs` — the proven type-guaranteed
  verdict is demoted when `subjectLaunders(...)` is true (find the call site
  with `rg -n "subjectLaunders" skills/captain-obvious/scripts/co_ts/analyzer.mjs`; at plan time it sits at line 361).
- Python reference implementation (the behavior to match):
  `co_py/mypy_pass.py:89-124` `propagate_laundering` — builds a
  `function name -> names of functions it returns calls of` map for the whole
  repo, then grows the seed set to a fixpoint.
- The TS type-guaranteed fixture pattern (tsconfig + tempdir + CLI + JSON
  report) is established by plan 003's `tests/test_type_guaranteed_ts.py`;
  if that plan has landed, model on it. Otherwise model on
  `tests/test_literal_tautology_ts.py` and note the fixture needs a
  `tsconfig.json` with `{"compilerOptions": {"strict": true}}` for
  `toBeDefined` classification to be active (strictNullChecks).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| TS setup | `npm install typescript@^5 --no-save --no-audit --no-fund` (repo root) | exit 0 |
| Full suite | `python3 -m unittest discover -v tests` | `OK` |
| Scanner on a fixture | `node skills/captain-obvious/scripts/captain_obvious_ts.mjs --project <dir> --json <out>` | exit 0, JSON report |

## Scope

**In scope**:

- `skills/captain-obvious/scripts/co_ts/laundering.mjs`
- `tests/test_laundering_ts.py` (create)
- `plans/README.md` (status row / verdict)

**Out of scope**:

- `co_py/mypy_pass.py` — the Python side is already transitive.
- `analyzer.mjs`, `classifier.mjs` — the fix belongs inside `callLaunders`;
  if it seems to require analyzer changes, STOP.

## Git workflow

- Branch: `fix/ts-laundering-transitivity`
- Conventional commit: `fix(ts): make the Any-laundering guard transitive across return-call chains`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: VERIFY — build the repro fixture and observe current behavior

Tempdir fixture (do this in a scratch dir first, by hand, before writing the
test file):

`tsconfig.json`: `{ "compilerOptions": { "strict": true, "target": "es2020" } }`

`launder.test.ts`:

```ts
interface Foo { a: number }
function inner(): Foo {
  return JSON.parse("{}") as Foo;
}
function outer(): Foo {
  return inner();
}
test("direct launder", () => {
  const d = inner();
  expect(d).toBeDefined();
});
test("indirect launder", () => {
  const v = outer();
  expect(v).toBeDefined();
});
```

Run the scanner, index findings by `test`, and record for each:
`category`, `level`, `deletable`.

Expected per the bug model: `"direct launder"` → demoted or unflagged (the
direct guard works: NOT `proven`); `"indirect launder"` → `type-guaranteed`
/ `proven` / `safe` (the guard missed it — the bug).

Decision gate:

- **Both demoted/unflagged** → the bug does NOT reproduce. Update
  `plans/README.md`: status `REJECTED`, note "indirect laundering already
  demoted at <date>, verified with fixture". STOP here — no code change.
- **"direct launder" is proven** → the direct guard itself doesn't behave as
  modeled; the plan's model is wrong. STOP and report the observed JSON.
- **Direct demoted, indirect proven** → bug confirmed; continue.

**Verify**: paste the two findings' JSON into your report/commit message.

### Step 2: Regression test (red)

Create `tests/test_laundering_ts.py` with the step-1 fixture, skip-guarded
by `_ts_resolvable()` (copy from `tests/test_literal_tautology_ts.py:28-34`).
Assert:

1. `"direct launder"` has NO finding with `level == "proven"`.
2. `"indirect launder"` has NO finding with `level == "proven"` — this
   assertion FAILS before the fix (red), proving the test bites.

**Verify**: `python3 -m unittest tests.test_laundering_ts -v` → exactly one
failure (case 2).

### Step 3: Make `callLaunders` transitive (bounded)

Rework `callLaunders` in `laundering.mjs` to recurse into functions called in
return position, with a visited-set and a depth cap:

```js
export function callLaunders(ts, checker, callExpr, seen = new Set(), depth = 0) {
  if (depth > 5) return true;            // deep chain: refuse to prove — stay safe
  const decl = /* existing resolution code, unchanged */;
  if (!decl || !decl.body || !decl.type) return false;
  if (seen.has(decl)) return false;      // cycle: no new information
  seen.add(decl);
  let launders = false;
  walk(ts, decl.body, n => {
    if (launders) return;
    if (ts.isReturnStatement(n) && n.expression) {
      const t = checker.getTypeAtLocation(n.expression);
      if ((t.flags & ts.TypeFlags.Any) || hasUnsafeCast(ts, n.expression)) { launders = true; return; }
      let re = n.expression;
      while (ts.isAwaitExpression(re) || ts.isParenthesizedExpression(re)) re = re.expression;
      if (ts.isCallExpression(re) && callLaunders(ts, checker, re, seen, depth + 1)) launders = true;
    }
  });
  return launders;
}
```

Design notes to honor (mirror the Python semantics):

- Recurse ONLY on return expressions that are (possibly awaited/parenthesized)
  calls — that matches `propagate_laundering`'s `return <call>` edges
  (`mypy_pass.py:107-114` also unwraps `Await`).
- Depth cap returns `true` (assume laundering) — refusing to prove is the
  safe direction on an auto-delete gate.
- `subjectLaunders` needs no change (it already funnels through
  `callLaunders`); keep its signature untouched.

**Verify**: `python3 -m unittest tests.test_laundering_ts -v` → `OK` (both
cases green).

### Step 4: No collateral demotions

Run the full suite: `python3 -m unittest discover -v tests` → `OK`. In
particular `tests/test_type_guaranteed_ts.py` (if plan 003 landed): the
plain `"tg proven"` case must STILL be proven — `getCount` is a `declare`d
function with no body, and `callLaunders` returns `false` for body-less
declarations (the `!decl.body` early-out), so recursion must not change it.

## Test plan

- `tests/test_laundering_ts.py`: direct + indirect cases (step 2), green
  after step 3.
- Full suite green (step 4).

## Done criteria

- [ ] Step-1 verdict recorded (in the plans index row: CONFIRMED or REJECTED)
- [ ] If confirmed: `python3 -m unittest discover -v tests` exits 0, including the new `tests/test_laundering_ts.py`
- [ ] `rg -n "seen|depth" skills/captain-obvious/scripts/co_ts/laundering.mjs` shows the bounded recursion
- [ ] `git status --porcelain` shows only in-scope files
- [ ] `plans/README.md` status row updated with the verdict

## STOP conditions

- Step 1's decision gate hits either non-continue branch.
- After step 3, ANY previously-proven finding in the existing test suite
  demotes (a fixture that should stay proven goes advisory) — the recursion
  is too eager; report which fixture and the new verdict.
- The fix seems to need changes outside `laundering.mjs`.

## Maintenance notes

- The recursion mirrors Python's `return <call>` edge model. If Python's
  `propagate_laundering` gains new edge types (e.g. assignments-then-return),
  port them here in the same change — the parity rule in `CLAUDE.md` applies.
- Reviewer focus: the depth-cap direction (`true` = assume laundering =
  demote to advisory = fewer deletions). Never flip it.
