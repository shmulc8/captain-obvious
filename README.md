# Captain Obvious 🫡

<p align="center">
  <img src="assets/logo.png" width="200" alt="Captain Obvious Logo" />
</p>

**AI agents write tests that can never fail. This skill deletes them.**

![captain-obvious scanning a repo: proven findings are auto-deletable, advisory findings are surfaced only](assets/demo.svg)

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

Detection is **fully deterministic** — no LLM in the loop, one script
invocation per repo. Instead of asking an AI "is this test useful?", it asks
the tools that already know:

- **TypeScript**: the project's own compiler API answers "what is the static
  type of this expression?" — if `typeof result === 'number'` is a fact of the
  type, the assertion is not a test.
- **Python**: batched `mypy reveal_type` probes (one mypy run for the whole
  repo) answer the same for `isinstance(...)` and `is not None`.
- **AST passes** catch the rest: tautologies, mock-echo tests, assertions after
  a `return`, assertions swallowed by empty `catch`/`except: pass`, duplicate
  bodies, `len(x) >= 0`.

## ✅ Proven vs ⚠️ advisory

Every finding is one of two tiers, and each tier is handled by whoever can
actually decide it:

- **proven** — cannot fail, by construction. Auto-deleted by `--fix`, **no LLM
  involved**. The scripts guard every known way types lie: `any`/`unknown`,
  `as` casts, non-null `!`, index signatures, unchecked index access, structural
  `instanceof` (only nominal classes are provable), `cast()`/`type: ignore`.
- **advisory** — almost certainly useless but *not* provable (structural
  `instanceof`, mock-echo variants, `pytest.raises(Exception)`, unawaited async
  assertions, rotten-green conditional asserts). The script writes down exactly
  *why* it's uncertain, and the coding agent running the skill adjudicates each
  one against the surrounding code — **delete, keep, or rewrite** — then
  proposes the calls for your approval before touching anything.

It also knows what **not** to flag: `toBeDefined()` on `.find()` results
(`T | undefined` — a real check), enum contract locks, custom assertion
helpers, deliberate determinism tests, and "must not raise" contract tests.

## 📊 Real-world results

- Internal repo, ~1,800 pytest tests: **124 lines deleted across 21 files** —
  111 assertions re-checking what mypy already guaranteed, plus one all-constant
  test. Type-checker and full CI green before and after; change auto-approved.
- TypeScript CLI repo, ~730 tests: **zero deletions** (it doesn't invent work)
  — but it caught a real bug: a `.rejects.toThrow(...)` never `await`ed, so it
  silently never ran.

The honest headline isn't a line count — it's that `--fix` only removes what it
can *prove*, so whatever it deletes could never have caught a regression. Clone
it and run it on your own repo.

## 🚀 Install & use

```bash
npx skills add shmulc8/captain-obvious@captain-obvious
```

Also ships a `.claude-plugin/plugin.json` manifest, or copy
`skills/captain-obvious/` into `~/.claude/skills/`. Then ask your agent to
*"clean up the useless tests"* — or run the scanners directly:

```bash
# report-only (add --json out.json to save)
node    skills/captain-obvious/scripts/captain_obvious_ts.mjs --project <repo>
python3 skills/captain-obvious/scripts/captain_obvious_py.py  --path <repo> --mypy "uv run mypy"

# delete proven findings (needs a clean git tree — review the diff after)
node ... --fix

# confirm rotten-green asserts against real coverage (lcov / istanbul / coverage.py)
node    ... --coverage coverage/lcov.info
python3 ... --coverage coverage.json
```

With `--coverage`, a `conditional-assert` whose line never executed is promoted
to **proven rotten**; one that did execute is a confirmed false positive and is
dropped — the dynamic half of the ICSE'19 analysis, using coverage your runner
already emits.

## 🛡️ Prevention (write-time hook)

Removing dead tests after the fact pays for them twice — once to write them,
once to clean them up. Installed **as a plugin**, captain-obvious also ships a
`PreToolUse` hook that catches them *before they land*: whenever the agent
writes or edits a test file, the pending content runs through a fast
syntactic-only single-file scan (`--file --stdin`; no mypy, no tsc, no
side effects), and the call is **denied with a per-finding reason** if it
would introduce proven can-never-fail patterns. The agent fixes the test on
the spot.

- **Proven, newly-introduced findings only.** Advisories never block, and a
  pre-existing finding elsewhere in the file never blocks an unrelated edit.
  TDD red is safe: a deliberately *failing* test is not a can-never-fail test.
- **Fails open, always.** Missing node, scanner crash, timeout, huge or
  syntactically-broken content — the write goes through.
- **Configurable**: `CAPTAIN_OBVIOUS_HOOK=block` (default) | `warn` | `off`.
- Skill-only installs (`npx skills add`) don't get hooks — paste the
  test-writing rules from
  [`skills/captain-obvious/references/prevention.md`](skills/captain-obvious/references/prevention.md)
  into your `CLAUDE.md` instead.

## 🔍 Detector catalog

| Category | Example | Level |
|---|---|---|
| `type-guaranteed` | `expect(typeof f()).toBe('number')` when `f(): number`; `assert x is not None` on non-Optional | proven |
| `constant-assert` | `expect(true).toBe(true)`, `assert x == x` | proven |
| `boundary-tautology` | `expect(arr.length).toBeGreaterThanOrEqual(0)` | proven |
| `local-const-echo` | `const expected = 5; expect(expected).toBe(5)` | proven |
| `mock-echo` | stub returns 5 → assert it returns 5 | proven / advisory |
| `dead-assert` / `swallowed-assert` / `never-asserts` | assertion after `return`, or inside `try {} catch {}` | proven |
| `missed-fail` | a forced-fail marker (`pytest.fail()` / `throw new Error`) stuck in dead code — can never fire | proven |
| `missed-skip` | a conditional early `return`/`skip` above the asserts — if it fires, they never run | advisory |
| `duplicate-test` | identical body in the same suite | proven |
| `no-assert` | no assertion anywhere — a **smoke test** (legit by design, per ICSE '19), surfaced not deleted | advisory |
| `conditional-assert` | assertion gated behind `if` — rotten green (ICSE '19) | advisory |
| `floating-async-assert` | unawaited `expect(p).rejects...` (silent pass under Jest) | advisory |
| `smoke-only` | `expect(fn).not.toThrow()` as the only check | advisory |
| `self-compare-call` | `expect(f(a)).toEqual(f(a))` | advisory |
| `broad-raises` | `pytest.raises(Exception)` as the only check | advisory |
| `skipped-test` | `it.skip` / `xit` / `@pytest.mark.skip` — never runs | advisory |

Full semantics, escape hatches, and false-positive guards — plus the academic
grounding (the rotten-green lineage: Delplanque *ICSE '19* → RTj Java '19 →
Robinson *ESEC/FSE '23* Google Test, which this tool extends to TS + Python) —
are in [`references/detectors.md`](skills/captain-obvious/references/detectors.md).

## ⚠️ Honest limitations

- Cannot catch weak-but-executing assertions (`result.length >= 0` is caught,
  `result.length < 1e9` is not) — only mutation testing proves those useless.
- Cross-file duplicates and coverage-subsumption are out of scope.
- Dynamically-built assertions are invisible.
- "Deleted nothing" does not mean "your suite is sound."
