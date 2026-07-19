---
name: captain-obvious
description: Finds and deletes "Captain Obvious" tests — tests that can never fail or check nothing. It catches assertions the type checker already guarantees (typeof/isinstance/toBeDefined on typed values), assertion-free tests, tautologies (expect(true).toBe(true), assert x == x, len >= 0), arrange-assert echoes (const x = 5; expect(x).toBe(5)), mock-echo tests, unawaited async assertions, dead/swallowed/conditional assertions, overly-broad pytest.raises(Exception), and duplicate test bodies. Use whenever the user wants to clean up a test suite, remove redundant/useless/tautological/AI-generated tests, mentions tests that "never fail" or "test nothing", asks to slim down CI time, or says "captain obvious". Works on TypeScript (Jest/Vitest/bun:test) and Python (pytest + mypy). The detection is fully deterministic — always run the bundled scripts, never scan test files one by one yourself.
---

# Captain Obvious

Deletes tests that assert what is already guaranteed — by the compiler, by the
mock framework, or by the laws of logic. These tests burn CI time, inflate
coverage confidence, and can never catch a regression. They are the signature
of AI-generated test suites (empirical studies find test smells in 38–100% of
LLM-generated tests).

The heavy lifting is done by two deterministic scripts in `scripts/`. Your job
is orchestration: run them, interpret the report, clean up the residue, and
verify nothing broke. **Do not** hand-scan test files or spawn subagents per
file — one script invocation scans the whole project.

## Workflow

### 1. Detect the stack(s)

- TypeScript: a `tsconfig.json` and `*.test.ts` / `*.spec.ts` / `__tests__` files.
- Python: `test_*.py` / `*_test.py` files (pytest).
- A repo can have both; run both detectors.

### 2. Safety first

The fix step edits test files in place. Require a clean git working tree
(untracked files are fine). If dirty, ask the user or stash. Never run `--fix`
outside a git repository without explicit user confirmation.

### 3. Scan (report-only)

```bash
node <skill-dir>/scripts/captain_obvious_ts.mjs --project <repo> --json /tmp/co-ts.json
python3 <skill-dir>/scripts/captain_obvious_py.py --path <repo> --json /tmp/co-py.json
```

- The Python detector shells out to mypy for the type-guaranteed category. Use
  the project's own environment: pass `--mypy "uv run mypy"` for uv projects,
  `--mypy "poetry run mypy"` for poetry, etc. If mypy isn't available it
  degrades gracefully to the syntactic categories.
- The TS detector resolves the project's own `typescript` package; without a
  tsconfig it degrades to syntactic categories.

Show the user the summary table and the findings before deleting anything.

### 4. Understand the two levels

- **proven** — cannot fail, by construction. The scripts guard the known
  escape hatches (`any`/`unknown`, `as` casts, `!`, index signatures, unchecked
  index access, structural `instanceof`, custom assertion helpers). Safe to
  auto-delete.
- **advisory** — almost certainly useless but not provable (assertion-free
  tests, structural instanceof, mock-echo variants, index-signature-backed
  checks). Deleted only with `--aggressive`. Categories marked *report-only*
  in the output (conditional-assert, swallowed-assert, must-not-raise contract
  tests) are never auto-deleted — they usually need a rewrite, not a deletion.

See `references/detectors.md` for the full category catalog and the reasoning
behind each guard.

### 5. Fix

```bash
node <skill-dir>/scripts/captain_obvious_ts.mjs --project <repo> --fix [--aggressive]
python3 <skill-dir>/scripts/captain_obvious_py.py --path <repo> --fix [--aggressive]
```

Default to proven-only (`--fix`). Add `--aggressive` when the user asked for
an aggressive cleanup or explicitly wants assertion-free / mock-echo tests
gone too. When unsure, fix proven first, then show the advisory list and ask.

### 6. Clean the residue

The scripts delete whole test blocks or individual assertion lines. That can
leave behind: unused imports/variables (`noUnusedLocals` will flag them),
empty `describe()` blocks, empty test classes, orphaned fixtures/mocks. Fix
those by hand — the typechecker output is your worklist.

### 7. Verify

Run the project's typecheck AND full test suite (`tsc --noEmit` + the test
command from package.json / `pytest`). Everything must pass with the same
result as before (minus the deleted tests). If anything regresses,
`git checkout -- <files>` and report what happened instead of pushing through.

### 8. Report

Tell the user: tests removed, assertion lines removed, per-category counts,
lines of code saved, and the advisory/report-only findings that deserve a
human look (especially conditional-assert — those are rotten green tests that
should be *fixed*, not deleted).

## What NOT to flag (the scripts already know, but so should you)

- `toBeDefined()` on `.find()` / `Map.get()` results — the type is `T | undefined`, the check is real.
- Enum/constant contract locks (`expect(ExitCode.OK).toBe(0)`) — they catch renumbering.
- Assertions on values read from files/APIs at test time — real regression tests.
- Tests asserting via custom helpers (`expectAllow(x)`, `self._check(...)`).
- "Must not raise" contract tests for fail-open code paths.
