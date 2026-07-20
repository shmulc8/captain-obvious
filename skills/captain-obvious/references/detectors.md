# Detector catalog

Both scripts emit the same JSON shape: `{findings: [{file, line, test, category,
level, deletable, reason}], summary, plan/fixed}`. `level` is `proven` or
`advisory`; `deletable` is `safe` (removed by `--fix`), `aggressive` (removed
by `--fix --aggressive`), or `report-only` (never auto-removed).

## Categories

| Category | What it catches | Level |
|---|---|---|
| `type-guaranteed` | Assertion re-checks a fact the type checker proves: `expect(typeof f()).toBe('number')`, `toBeDefined()`/`not.toBeNull()` on non-nullable types, `toBeInstanceOf` on nominal classes, `toBeTruthy` on object types, `Array.isArray` on arrays, `expect.any(Ctor)`/`expect.anything()`, `toHaveProperty` on required props; Python: `assert isinstance(x, T)` / `assert x is not None` / `type(x) is T` checked against mypy `reveal_type` | proven / advisory |
| `constant-assert` | Tautologies: `expect(true).toBe(true)`, `assert 1 == 1`, `expect(x).toBe(x)`, `assert x == x` on side-effect-free chains | proven |
| `no-assert` | No assertion anywhere in the test (the "Unknown Test" smell) — passes silently by design of the framework | advisory |
| `mock-echo` | Test asserts the mock does what it was just stubbed to do: `m.mockReturnValue(5); expect(m()).toBe(5)`, or calls `m()` then asserts `toHaveBeenCalled()` | proven (direct) / advisory (indirect) |
| `duplicate-test` | Body identical to an earlier test in the *same* suite scope — same-file, same-describe only, snapshot tests excluded. Comparison is comment/formatting-insensitive but **literal-sensitive** (whitespace *inside* a string/template literal counts), so whitespace-handling tests are not merged. When the two tests' names materially diverge the finding is flagged as a likely copy-paste bug that leaves the named behaviour untested | proven |
| `dead-assert` | Assertion after an unconditional `return`/`throw`/`raise` — unreachable | proven |
| `swallowed-assert` | Assertion inside `try` with an empty/`pass`/console-only catch — a failure is absorbed | proven, report-only |
| `never-asserts` | Test has assertions but ALL are dead or swallowed → the test cannot fail | proven |
| `conditional-assert` | Assertion gated behind `if` (rotten green test, ICSE '19) — may never execute (e.g. `if (process.platform === 'darwin')` in Linux-only CI). Two shapes are deliberately NOT flagged, being common legit style: loop-nested asserts (`for x in items: if …: assert`), and asserts whose guard is keyed on a parametrized/fixture argument (`if mode: … else: …` — every branch runs across the parametrization) | advisory, report-only |
| `boundary-tautology` | `expect(x.length).toBeGreaterThanOrEqual(0)` / `assert len(x) >= 0` — a length can never be negative | proven |
| `local-const-echo` | `const expected = 5; expect(expected).toBe(5)` — the test asserts its own arrangement, code under test never involved | proven |
| `floating-async-assert` | `expect(p).resolves/.rejects...` without `await`. Runner-dependent severity: Jest silently never evaluates it (real silent-pass bug); bun:test and modern Vitest fail the test at settle time (style/portability issue). Needs `await` added, so report-only | advisory, report-only |
| `smoke-only` | `expect(fn).not.toThrow()` as an assertion — calling `fn()` directly fails the test on throw anyway; assert on the return value instead | advisory, report-only |
| `self-compare-call` | `expect(f(a)).toEqual(f(a))` / `assert f(a) == f(a)` — equal by construction. Skipped when the test name says stable/deterministic/idempotent (then it's a real determinism check) | advisory, report-only |
| `broad-raises` | `with pytest.raises(Exception):` as the only check — passes on ANY bug that raises | advisory, report-only |
| `skipped-test` | `it.skip` / `xit` / `@pytest.mark.skip` — never runs, can never fail. Advisory because skips sometimes document future work; `skipif` is conditional and untouched | advisory |

## Why proven vs advisory — the escape hatches

A type is only a guarantee when nothing lied to the checker. The scripts
refuse to mark `type-guaranteed` as proven when:

- the expression's type involves `any` / `unknown` / type parameters (TS) or
  `Any` / unions (Python) — `JSON.parse`, untyped imports, untyped defs;
- the expression contains `as` casts (except `as const`), `<T>` assertions,
  or non-null `!` (TS), or the file uses `cast(` / `type: ignore` (Python);
- the value comes through element access without `noUncheckedIndexedAccess`,
  or through an **index signature** — the type is a promise, not a check
  (contract tests over JSON-ish APIs live here; they're legit);
- `strictNullChecks` is off — null/undefined facts aren't tracked at all;
- `toBeInstanceOf`: TypeScript is structural, so a declared type `C` can hold
  a shaped non-instance. Only nominal classes (a private/protected/`#` member
  somewhere in the chain) make `instanceof` provable; otherwise advisory.
- Python `type(x) is T` is advisory even when types match: a subclass
  instance satisfies the type but fails the identity check.
- **Annotation laundering** (found by adversarial audit on a real repo): a
  function annotated `-> dict[str, Any]` whose body just does
  `return http_client.api_get(...)` where `api_get() -> Any`. The type checker
  trusts the signature, but nothing enforces it at runtime — an isinstance
  check on its result is real API-shape regression coverage. The Python
  detector runs mypy with `--warn-return-any` over the source tree and refuses
  to flag assertions on values that flow (directly or through one assignment)
  from a `[no-any-return]` function. The TS detector walks the callee's return
  statements and refuses when an annotated function returns an `any`-typed
  expression or contains a cast in a return.
- Duplicate detection includes decorators in the test's identity — two
  `@pytest.mark.parametrize` tests can share a body but run different cases.
- **Literal-sensitive duplicate key** (found by real-repo iteration on langfuse):
  a naive whitespace-stripped body comparison merges tests that differ only in
  whitespace *inside* a string/template literal — exactly what a template
  whitespace-handling suite looks like (`"{{ name }}"` vs `"{{name}}"`). The TS
  detector builds the duplicate/echo key by tokenizing (`ts.createScanner`) and
  keeping each token's raw text, so literal contents are significant while
  indentation/comments are not. Python already dumps the AST, which preserves
  string-constant values, so it is immune by construction.
- **Closure-mutated locals** (found on mlflow): a call-counter
  `count = 0; def cb(): nonlocal count; count += 1; …; assert count == 0`
  is not a `local-const-echo` — the assertion is real behavioural coverage that
  the callback did/didn't run. The Python detector counts assignments across the
  full function subtree (not just the top level) and hard-disqualifies any name
  declared `nonlocal`/`global` in a nested scope. The TS detector only trusts
  `const` bindings to primitive literals, which cannot be closure-mutated.

Known residual edge cases (accepted, documented): property getters with side
effects can defeat `expect(a.b).toBe(a.b)` self-comparison detection; `NaN`
makes `toBe(self)` fail rather than pass — either way the test checks nothing
about the code under test.

## False-positive guards (learned from real repos)

- **Custom assertion helpers**: any called function matching
  `/^(expect|assert|verify|check|should)/i` counts as an assertion; the Python
  detector also resolves same-file helpers transitively (a test calling
  `_check_result(...)` whose body asserts is NOT assertion-free).
- **Must-not-raise contract tests** (name/docstring mentions noop / not raise /
  swallow / silent / graceful): reported but never auto-deleted.
- **Enum contract locks** (`expect(ExitCode.OK).toBe(0)`): not flagged — one
  side is a real imported symbol, not a literal.
- **`it.each` / parametrized tables**: skipped entirely.
- **Same-body tests in different describes**: not duplicates — different
  `beforeEach` context can make identical bodies test different things.

## Grounding

- Rotten Green Tests — Delplanque et al., ICSE 2019 (assertions that never execute)
- Test smell catalogs / tsDetect, JNose ("Unknown Test", "Duplicate Assert", "Conditional Test Logic")
- Pseudo-tested methods — Niedermayr 2016, Descartes ASE 2018 (the dynamic ceiling this static tool approximates)
- "On the Diffusion of Test Smells in LLM-Generated Unit Tests" (2024) — smells in 38–100% of LLM suites
- TS unsoundness checklist — Effective TypeScript, "Seven Sources of Unsoundness"

## Limitations (be honest in reports)

- Cannot catch weak-but-executing assertions (a test asserting `result.length >= 0`);
  only mutation testing proves those useless.
- Cross-file duplicate tests and coverage-subsumption are out of scope.
- Assertions built dynamically (loops over matcher names, `expect[m]()`) are invisible.
- "Deleted nothing" does not mean "suite is sound".
