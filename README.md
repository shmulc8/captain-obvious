# Captain Obvious 🫡

**AI agents write tests that can never fail. This skill deletes them.**

Every AI-generated test suite has them:

```python
assert isinstance(add(1, 2), int)   # the function is typed `-> int`. mypy knew.
```

```ts
expect(true).toBe(true);            // bold claim
```

Tests like these can't catch a regression — they re-assert what the compiler
already proved, or assert nothing at all. They burn CI minutes and inflate
coverage into false confidence. Empirical studies find test smells in 38–100%
of LLM-generated test suites.

## 🧠 The idea: ask the type checker, not another LLM

Detection is **fully deterministic** — no LLM in the loop, no per-file agent
calls, one script invocation per repo. Instead of asking an AI "is this test
useful?", it asks the tools that already know:

- **TypeScript**: the project's own compiler API answers "what is the static
  type of this expression?" — if `typeof result === 'number'` is a fact of the
  type, the assertion is not a test.
- **Python**: batched `mypy reveal_type` probes (one mypy run for the whole
  repo) answer the same question for `isinstance(...)` and `is not None`.
- **AST passes** catch the rest: tautologies, mock-echo tests, assertions
  after a `return`, assertions swallowed by empty `catch`/`except: pass`,
  duplicate test bodies, `len(x) >= 0`.

## ✅ Proven vs ⚠️ advisory

Every finding is two-tier:

- **proven** — cannot fail, by construction. Auto-deleted by `--fix`. The
  scripts guard every known way types lie: `any`/`unknown`, `as` casts,
  non-null `!`, index signatures, unchecked index access, structural
  `instanceof` (only nominal classes — private members — are provable),
  `cast()`/`type: ignore` in Python.
- **advisory** — almost certainly useless but not provable (assertion-free
  tests, mock-echo variants, `pytest.raises(Exception)` as the only check).
  Deleted only with `--fix --aggressive`; the report-only subset (rotten-green
  conditional assertions, unawaited async assertions) is never auto-deleted —
  those need a rewrite, not a deletion.

It also knows what **not** to flag: `toBeDefined()` on `.find()` results
(`T | undefined` — a real check), enum contract locks, custom assertion
helpers, deliberate determinism tests (`expect(f(x)).toEqual(f(x))` when the
test says "stable"), and "must not raise" contract tests.

## 📊 Real-world results

- Internal repo, ~1,800 pytest tests: **228 lines of test theater deleted**
  (15 whole tests + 58 assertion lines) — 73 assertions re-checking what mypy
  guaranteed, one `assert True` with a comment admitting it. Suite stayed
  green; the only failures before and after were pre-existing.
- Hand-curated CLI repo, 733 tests: **zero deletions** (it doesn't invent
  work) — but it flagged a test whose only assertion runs behind
  `if (process.platform === 'darwin')` in a CI matrix with no macOS runner.
  Green for months, asserting nothing.

## 🚀 Install

```bash
npx skills add shmulc8/captain-obvious@captain-obvious
```

Or as a Claude Code plugin: this repo carries a `.claude-plugin/plugin.json`
manifest. Or just copy `skills/captain-obvious/` into `~/.claude/skills/`.

Then ask your agent to *"clean up the useless tests"* — or run the scanners
directly:

```bash
# report-only
node skills/captain-obvious/scripts/captain_obvious_ts.mjs --project <repo> [--json out.json]
python3 skills/captain-obvious/scripts/captain_obvious_py.py --path <repo> --mypy "uv run mypy"

# delete proven findings (clean git tree required — review the diff after)
node ... --fix          # proven only
python3 ... --fix --aggressive   # also assertion-free & mock-echo tests
```

## 🔍 Detector catalog

| Category | Example | Level |
|---|---|---|
| `type-guaranteed` | `expect(typeof f()).toBe('number')` when `f(): number`; `assert x is not None` on non-Optional | proven |
| `constant-assert` | `expect(true).toBe(true)`, `assert x == x` | proven |
| `boundary-tautology` | `expect(arr.length).toBeGreaterThanOrEqual(0)` | proven |
| `local-const-echo` | `const expected = 5; expect(expected).toBe(5)` | proven |
| `mock-echo` | stub returns 5 → assert it returns 5 | proven / advisory |
| `dead-assert` / `swallowed-assert` / `never-asserts` | assertion after `return`, or inside `try {} catch {}` | proven |
| `duplicate-test` | identical body in the same suite | proven |
| `no-assert` | no assertion anywhere ("Unknown Test" smell) | advisory |
| `conditional-assert` | assertion gated behind `if` — rotten green (ICSE '19) | report-only |
| `floating-async-assert` | unawaited `expect(p).rejects...` (silent pass under Jest) | report-only |
| `smoke-only` | `expect(fn).not.toThrow()` as the only check | report-only |
| `self-compare-call` | `expect(f(a)).toEqual(f(a))` | report-only |
| `broad-raises` | `pytest.raises(Exception)` as the only check | report-only |

Full semantics, escape hatches, and false-positive guards:
[`skills/captain-obvious/references/detectors.md`](skills/captain-obvious/references/detectors.md)

## 📚 Grounding

- *Rotten Green Tests* — Delplanque et al., ICSE 2019
- Test smell catalogs / tsDetect, JNose ("Unknown Test", "Conditional Test Logic")
- Pseudo-tested methods — Niedermayr 2016; Descartes, ASE 2018 (the dynamic ceiling this static tool approximates)
- *On the Diffusion of Test Smells in LLM-Generated Unit Tests* (2024)
- *Effective TypeScript* — "Seven Sources of Unsoundness"

## ⚠️ Honest limitations

- Cannot catch weak-but-executing assertions (`expect(result.length >= 0)` is
  caught, `expect(result.length).toBeLessThan(1e9)` is not) — only mutation
  testing proves those useless.
- Cross-file duplicates and coverage-subsumption are out of scope.
- Dynamically-built assertions are invisible.
- "Deleted nothing" does not mean "your suite is sound."

## 📂 Repository structure

- [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) – plugin manifest
- [`skills/captain-obvious/SKILL.md`](skills/captain-obvious/SKILL.md) – agent workflow (scan → review → fix → verify)
- [`skills/captain-obvious/scripts/captain_obvious_ts.mjs`](skills/captain-obvious/scripts/captain_obvious_ts.mjs) – TypeScript detector (compiler API)
- [`skills/captain-obvious/scripts/captain_obvious_py.py`](skills/captain-obvious/scripts/captain_obvious_py.py) – Python detector (AST + mypy reveal_type)
- [`skills/captain-obvious/references/detectors.md`](skills/captain-obvious/references/detectors.md) – full detector catalog and guards
