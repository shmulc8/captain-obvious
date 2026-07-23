# Plan 010: Scan plain-JavaScript test files (`.test.js` / `.spec.jsx` / `.test.mjs|.cjs`) — syntactic categories

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/co_ts/discovery.mjs skills/captain-obvious/scripts/captain_obvious_ts.mjs hooks/prevent.py tests/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition. (Plan 007 adds `.sort()` to
> discovery — expected drift if DONE.)

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW–MED (additive scope widening; the risk is accidentally
  running type-guaranteed on unchecked JS — explicitly gated below)
- **Depends on**: plans/007-determinism-and-parity.md (same discovery
  function; land 007 first to avoid conflicts)
- **Category**: direction
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

The plugin manifest and README advertise "Jest/Vitest/bun:test" — runners
whose suites are very often plain JavaScript — but discovery is TS-only:
a `.test.js` project scans as "no test files", and the write-time hook
silently skips JS test writes. The syntactic detector set (constant-assert,
boundary-tautology, local-const-echo, self-compare-call, dead/swallowed
assert, mock-echo, duplicate-test, silent-smoke…) is language-agnostic
within the JS/TS family — the same `ts.createSourceFile` parse the
single-file path already uses handles JS. Widening two regexes and the
ScriptKind selection unlocks a large audience for detectors the engine
already has.

**Hard constraint**: `type-guaranteed` must NOT fire on JS files unless the
project's tsconfig enables `checkJs` — TS's JS inference without `checkJs`
is not a checked guarantee, and "proven" findings must never rest on
unchecked types. This stage ships syntactic parity for JS; typed-JS support
is gated, not chased.

## Current state

- `co_ts/discovery.mjs:5,15-17`:

```js
export const TEST_RE = /\.(test|spec)\.(ts|tsx|mts|cts)$/;
...
      } else if (TEST_RE.test(e.name) ||
                 (/\.(ts|tsx)$/.test(e.name) && path.basename(dir) === '__tests__')) {
```

- `hooks/prevent.py:33` mirrors it:

```python
TS_TEST_RE = re.compile(r"\.(test|spec)\.(ts|tsx|mts|cts)$|[\\/]__tests__[\\/][^\\/]*\.(ts|tsx)$")
```

- Single-file parse kind, `captain_obvious_ts.mjs:89`:

```js
  const kind = /\.[jt]sx$/.test(filePath) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
```

- Program construction, `captain_obvious_ts.mjs:134-137`: test files are
  passed as root names with the tsconfig's options + `noEmit: true`.
  Without `allowJs`, a Program given `.js` root files will not include them
  (`program.getSourceFile` returns undefined) — the per-file loop at
  145-152 silently skips such files (`if (!sf) continue;`), which would make
  JS discovery a no-op in typed projects. The fix below forces `allowJs`
  for parsing and gates type usage per file.
- Per-file analysis call, `captain_obvious_ts.mjs:148-151`: `analyzeTest(ts,
  checker, typesAvailable, strictNull, uncheckedIndex, sf, t, ...)` —
  `typesAvailable` is a plain boolean argument; per-file variation is a
  call-site change only.
- `tsconfig.json` flags of record: `options.checkJs` (and `allowJs`) come
  from `parseJsonConfigFileContent` at lines 124-129.
- The hook's scan dispatch (`hooks/prevent.py:40-48`): `.py` → Python CLI,
  everything else → TS CLI — so widening `TS_TEST_RE` automatically routes
  JS files to the TS scanner in single-file mode.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| TS setup | `npm install typescript@^5 --no-save --no-audit --no-fund` | exit 0 |
| Full suite | `python3 -m unittest discover -v tests` | `OK` |
| New tests | `python3 -m unittest tests.test_js_discovery -v` | `OK` |

## Scope

**In scope**:

- `skills/captain-obvious/scripts/co_ts/discovery.mjs`
- `skills/captain-obvious/scripts/captain_obvious_ts.mjs`
- `hooks/prevent.py` (regex only)
- `tests/test_js_discovery.py` (create)
- `README.md` (one-line scope note)
- `plans/README.md` (status row)

**Out of scope**:

- Enabling `type-guaranteed` on `checkJs` projects — deferred follow-up;
  this plan only *gates* (disables) types for JS files.
- `co_ts/analyzer.mjs` / `classifier.mjs` internals — the analyzers are
  syntax-driven and need no change.
- The Python engine.

## Git workflow

- Branch: `feat/plain-js-test-files`
- Conventional commit: `feat(ts): scan plain-JS test files (syntactic categories)`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Widen discovery

`co_ts/discovery.mjs`:

```js
export const TEST_RE = /\.(test|spec)\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$/;
```

and the `__tests__` arm: `/\.(ts|tsx|js|jsx)$/`.

**Verify**: `node -e "import('./skills/captain-obvious/scripts/co_ts/discovery.mjs').then(m => { console.log(m.TEST_RE.test('a.test.js'), m.TEST_RE.test('a.spec.cjs'), m.TEST_RE.test('a.test.ts'), m.TEST_RE.test('a.js')); })"` → `true true true false`

### Step 2: ScriptKind + per-file type gating in the TS CLI

`captain_obvious_ts.mjs`:

1. Single-file mode (line 89) — pick the kind by extension:

```js
  const kind = /\.tsx$/.test(filePath) ? ts.ScriptKind.TSX
    : /\.jsx$/.test(filePath) ? ts.ScriptKind.JSX
    : /\.(js|mjs|cjs)$/.test(filePath) ? ts.ScriptKind.JS
    : ts.ScriptKind.TS;
```

2. Program options (line 134-137): force JS parsing without changing typed
   behavior — `{ ...options, noEmit: true, allowJs: true }`.
3. Per-file gating in the analysis loop (148-151): compute

```js
  const isJs = /\.(js|jsx|mjs|cjs)$/.test(file);
  const fileTypes = typesAvailable && !isJs;
```

   and pass `fileTypes` instead of `typesAvailable` to `analyzeTest`.
   (`strictNull`/`uncheckedIndex` are only consulted when types are on;
   pass them through unchanged.) This stage types **no** JS file — even
   `checkJs: true` projects get syntactic categories only; honoring `checkJs`
   is the deferred follow-up (Out-of-scope + Maintenance notes below), so the
   gate deliberately does not branch on `options.checkJs`. (Consistent with
   the Hard constraint: never firing `type-guaranteed` on JS satisfies "must
   NOT fire … unless `checkJs`".)

**Verify**: full suite → `OK` (TS-file behavior unchanged: for `.ts` files
`fileTypes === typesAvailable`).

### Step 3: Hook regex

`hooks/prevent.py:33`:

```python
TS_TEST_RE = re.compile(r"\.(test|spec)\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$|[\\/]__tests__[\\/][^\\/]*\.(ts|tsx|js|jsx)$")
```

**Verify**: `python3 -m unittest tests.test_prevent_hook -v` → `OK`.

### Step 4: Tests

Create `tests/test_js_discovery.py` (TS-CLI pattern from
`tests/test_literal_tautology_ts.py`, incl. `_ts_resolvable()`):

1. **Plain-JS project scan**: tempdir with ONLY `app.test.js`:

```js
test("truth", () => {
  expect(true).toBe(true);
});
```

   Run `--project <dir> --json out`. Assert `testFilesScanned == 1` and one
   (`constant-assert`, `proven`) finding. (At `0eaabb5` this reports "no
   test files" — the test proves the widening works.)
2. **Typed project with a JS test**: tempdir with `tsconfig.json`
   (`{"compilerOptions": {"strict": true}}` — note: NO `checkJs`),
   `typed.test.ts` (the `expect(true).toBe(true)` fixture), and `js.test.js`
   containing a would-be type-guaranteed shape:

```js
function getCount() { return 1; }
test("js typeof", () => {
  const n = getCount();
  expect(typeof n).toBe("number");
});
```

   Assert: both files scanned; NO `type-guaranteed` finding exists for
   `"js typeof"` (the gate holds — TS would happily infer `number` here,
   which is exactly the unchecked guarantee we refuse); the `.ts` file's
   findings are unaffected.
3. **Hook single-file JS**: run the TS CLI
   `--file <dir>/app.test.js --stdin` feeding the fixture from case 1 on
   stdin; assert the JSON report has the `constant-assert` finding (this is
   the exact path `prevent.py` invokes for JS writes).

**Verify**: `python3 -m unittest tests.test_js_discovery -v` → `OK`.

### Step 5: README scope line

In `README.md`, the "🧠 The idea" TypeScript bullet (line 32-34) — append
one sentence: "Plain-JS test files (`.test.js` etc.) get the syntactic
categories; `type-guaranteed` needs TypeScript (or stays off without
`checkJs`)."

**Verify**: `rg -n "Plain-JS" README.md` → 1 match.

## Test plan

- `tests/test_js_discovery.py` — 3 cases (step 4).
- Regression: full suite, esp. `tests/test_single_file_mode_ts.py`,
  `tests/test_prevent_hook.py`, `tests/test_never_asserts_ts.py`.

## Done criteria

- [ ] Step-1 regex one-liner verify prints `true true true false`
- [ ] `python3 -m unittest discover -v tests` exits 0 (incl. 3 new cases)
- [ ] Case-2 gate assertion holds (no type-guaranteed on unchecked JS)
- [ ] `git status --porcelain` shows only in-scope files
- [ ] `plans/README.md` status row updated

## STOP conditions

- Case 2 shows a `type-guaranteed` finding on the JS file — the gate is not
  wired where the plan thinks; report the finding JSON and the call-site
  diff, do not ship.
- The typed project's `.ts` findings change in any way after adding
  `allowJs: true` to the Program options.
- Any pre-existing hook/single-file test fails after step 3.

## Maintenance notes

- Follow-up (own plan, only on demand): honor `checkJs: true` by letting
  `fileTypes` be true for JS — needs its own fixture project proving TS
  actually checks the JS before anything is marked proven.
- `__tests__`-dir JS discovery includes `.js/.jsx` but not `.mjs/.cjs`
  (matching how rare those are in `__tests__` conventions); widen only with
  a real-world case in hand.
- Reviewer focus: the `fileTypes` computation and that `allowJs` is forced
  ONLY at Program construction (never written to the user's tsconfig).
