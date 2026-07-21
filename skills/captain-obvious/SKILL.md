---
name: captain-obvious
description: Finds and deletes "Captain Obvious" tests — tests that can never fail or check nothing. It catches assertions the type checker already guarantees (typeof/isinstance/toBeDefined on typed values), assertion-free tests, tautologies (expect(true).toBe(true), assert x == x, len >= 0), arrange-assert echoes (const x = 5; expect(x).toBe(5)), mock-echo tests, unawaited async assertions, dead/swallowed/conditional assertions, overly-broad pytest.raises(Exception), and duplicate test bodies. Use whenever the user wants to clean up a test suite, remove redundant/useless/tautological/AI-generated tests, mentions tests that "never fail" or "test nothing", or says "captain obvious". Works on TypeScript (Jest/Vitest/bun:test) and Python (pytest + mypy). The detection is fully deterministic — always run the bundled scripts, never scan test files one by one yourself.
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

The fix step edits test files in place. Both scripts enforce this themselves:
`--fix` exits 2 unless the target is a git repository with a clean working
tree (untracked files are fine). If it refuses, stash or commit rather than
reaching for `--force` — `--force` removes the only undo path there is
(`git checkout -- <files>`), so use it only when the user has explicitly
accepted that.

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
- **If the project already produces coverage** (or you can cheaply run it),
  pass `--coverage <file>` (lcov / istanbul `coverage-final.json` / coverage.py
  `coverage json`). This is the dynamic half of the ICSE'19 rotten-green
  analysis: a `conditional-assert` whose line never ran is promoted to **proven
  rotten**, and one that did run is dropped as a confirmed false positive. It
  turns the noisiest advisory category into a trustworthy one — use it whenever
  coverage is available.

Show the user the summary table and the findings before deleting anything.

### 4. Understand the two levels

- **proven** — cannot fail, by construction. The scripts guard the known
  escape hatches (`any`/`unknown`, `as` casts, `!`, index signatures, unchecked
  index access, structural `instanceof`, custom assertion helpers). Safe to
  auto-delete.
- **advisory** — almost certainly useless but *not* provable (assertion-free
  tests, structural instanceof, mock-echo variants, index-signature-backed
  checks, rotten-green conditional asserts, unawaited async assertions). The
  script never auto-deletes these, but it records *exactly why* each is
  uncertain, plus a `deletable` hint (`aggressive` = usually a deletion,
  `report-only` = usually needs a rewrite). That reason is a question **you**
  are equipped to answer against the surrounding code — so advisories are
  adjudicated by you (step 6), not dumped on the user.

See `references/detectors.md` for the full category catalog and the reasoning
behind each guard.

### 5. Fix the proven tier (deterministic)

```bash
node <skill-dir>/scripts/captain_obvious_ts.mjs --project <repo> --fix
python3 <skill-dir>/scripts/captain_obvious_py.py --path <repo> --fix
```

Plain `--fix` removes only the **proven** findings — no judgment required, no
LLM. This is the safe deterministic core; run it first.

### 6. Adjudicate the advisory tier (you decide, then confirm)

Advisories are the cases determinism *can't* settle — and that's your job, not
a report line for the user. Do **not** just forward the list. For each advisory
finding:

1. Read the test and the code it exercises. The finding's `reason` field is a
   pointed question — e.g. *"structural instanceof — a shaped non-instance
   could sneak in"* → check whether anything actually constructs a non-instance
   of that type; *"mock-echo, indirect"* → check whether a real code path runs
   between stub and assert.
2. Decide one of: **delete** (the doubt doesn't hold — it really is useless),
   **keep** (the doubt holds — it's a real check), or **rewrite** (the intent
   is valid but the assertion is broken). Rewrite is the advisory tier's real
   value: fix the unawaited `.rejects` (`await` it), narrow a
   `pytest.raises(Exception)` to the specific type, repair a rotten-green
   `conditional-assert` so it actually runs. Note `no-assert` findings are
   **smoke tests** — legitimate by design (ICSE'19); default to **keep** unless
   the test clearly *meant* to assert something and forgot.
3. **Propose before acting.** Present a compact per-item table — finding,
   verdict, one-line rationale, and the exact edit for rewrites — and apply
   only what the user approves. Never auto-delete or auto-rewrite an advisory.

For a large advisory set, delegate the per-item code reads to a **Sonnet
subagent** (batch the findings; have it return verdict + rationale + proposed
edit per item) and keep the final proposal/synthesis here — don't burn the main
loop reading files one by one. The proven tier is never handed to a subagent;
it's already decided.

### 7. Clean the residue

The scripts delete whole test blocks or individual assertion lines. That can
leave behind: unused imports/variables (`noUnusedLocals` will flag them),
empty `describe()` blocks, empty test classes, orphaned fixtures/mocks. Fix
those by hand — the typechecker output is your worklist.

### 8. Verify

Run the project's typecheck AND full test suite (`tsc --noEmit` + the test
command from package.json / `pytest`). Everything must pass with the same
result as before (minus the deleted tests). If anything regresses,
`git checkout -- <files>` and report what happened instead of pushing through.

### 9. Report

Tell the user: proven tests/assertions removed (per-category counts, lines
saved), the advisory verdicts you applied (deleted / rewritten, with the fix),
and anything you chose to **keep** with the reason the doubt held — that last
group is the tool earning trust, not failing.

## What NOT to flag (the scripts already know, but so should you)

- `toBeDefined()` on `.find()` / `Map.get()` results — the type is `T | undefined`, the check is real.
- Enum/constant contract locks (`expect(ExitCode.OK).toBe(0)`) — they catch renumbering.
- Assertions on values read from files/APIs at test time — real regression tests.
- Tests asserting via custom helpers (`expectAllow(x)`, `self._check(...)`).
- "Must not raise" contract tests for fail-open code paths.

## When NOT to run this at all

- **Mid red-green.** During TDD a test is *supposed* to be failing, and a
  freshly-written test may not have its assertion yet. This is post-hoc
  cleanup — run it once the suite is green, never between red and green.
- **On a branch under review.** Scan (`--json`) is fine; `--fix` is not.
  Rewriting test files while a reviewer or a merge gate is reading the diff
  invalidates what they reviewed.
- **As a coverage or CI-time optimizer.** It deletes tests that cannot fail,
  which is a correctness argument, not a speed one. "CI is slow" is not a
  reason to reach for it — a slow suite full of real tests stays slow.
