# Plan 011: `--check --base <ref>` — a report-only CI/pre-commit gate for newly-introduced proven findings

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/captain_obvious_py.py skills/captain-obvious/scripts/captain_obvious_ts.mjs hooks/prevent.py README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: M–L
- **Risk**: MED (new exit-code contract; must never produce false CI
  failures — every ambiguity resolves toward exit 0)
- **Depends on**: none (conceptually after 005's exit-code conventions are
  in; no file conflicts)
- **Category**: direction
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

The write-time hook (`hooks/prevent.py`) blocks NEW proven can-never-fail
tests — but only inside a Claude Code agent session. Non-agent contributors
and other-agent PRs have nothing: both CLIs exit 0 after reporting (non-zero
is reserved for `--fix` refusals), so no CI job can fail on findings. The
pieces already exist — single-file scans, JSON reports, and the hook's exact
"newly-introduced proven only" keying (`prevent.py:107-109`: findings keyed
on `(category, test)` because line numbers shift). This plan adds the
missing exit-code contract plus thin CI/pre-commit wrappers.

**Doc-tension scoping (load-bearing).** `SKILL.md` ("When NOT to run this at
all") rejects the tool "as a coverage or CI-time optimizer" and permits only
`--json` (never `--fix`) on branches under review. `--check` must therefore
be: report-only (never writes, `--fix` combination rejected), and failing
ONLY on **proven findings newly introduced vs a base ref** — the CI twin of
the write-time hook the repo already ships and endorses, not a gate on the
full finding set. Any drift toward "fail on all findings" contradicts the
repo's own documented stance — don't build it.

## Current state

- Exit codes today: `captain_obvious_py.py:216` `return 0` (report path);
  `return 2` only for the gitguard refusal (line 96) and argparse errors.
  TS: `process.exit(2)` for refusals/load errors, exit 0 otherwise
  (`captain_obvious_ts.mjs:33-80,109`). **Exit 1 is unclaimed on both** —
  reserve it for the check verdict.
- The newly-introduced keying to replicate (`hooks/prevent.py:102-109`):

```python
    fresh = proven_findings(report)
    if fresh and old:
        old_report = scan(path, old)
        if old_report is None:
            return
        # line numbers shift across edits — key on (category, test name)
        seen = {(f["category"], f["test"]) for f in proven_findings(old_report)}
        fresh = [f for f in fresh if (f["category"], f["test"]) not in seen]
```

- Getting base content: `git show <base>:<relpath>` (relpath from repo
  root, forward slashes). `git merge-base origin/main HEAD` may not exist on
  shallow CI checkouts — every git failure must degrade to "treat file as
  previously clean"? NO — the safe direction for a *gate* is the opposite of
  the hook's: a file we can't read from base is treated as NEW (all its
  proven findings count) **only if `git show` failed because the file
  doesn't exist in base** (`fatal: path ... does not exist`); any OTHER git
  failure (no such ref, shallow clone) fails OPEN: print a note, exit 0.
  Distinguish the two: `git cat-file -e <base>` first — ref invalid → note +
  exit 0; ref valid but file absent → file is new.
- Single-file scans for base content already exist: Python `single_file()`
  reads stdin (`captain_obvious_py.py:26-59`); simplest correct
  implementation shells the CLI to ITSELF per changed file
  (`--file <path> --stdin` with base content on stdin), reusing tested code
  paths. Current-content findings come from the normal full scan already
  performed. Asymmetry note: the full scan may include type-guaranteed
  (mypy/tsc) findings; the base scan (single-file) is syntactic-only —
  keying on `(category, test)` therefore over-fires for type-guaranteed on
  changed files. **Scope decision: `--check` compares syntactic proven
  findings only** — filter both sides to categories the single-file mode can
  produce, i.e. drop `type-guaranteed` findings from the check comparison
  (they cannot appear in the base-side report). This mirrors the hook, which
  is also syntactic-only.
- Which files to diff: `git diff --name-only <base>...HEAD` intersected
  with each scanner's discovery set (the scanners already know their test
  files; intersect by absolute path).
- Wrapper surfaces: composite action at `action.yml` (repo root — GitHub
  resolves `uses: <owner>/captain-obvious@<ref>` to a root action.yml), and
  `.pre-commit-hooks.yaml` (pre-commit's registry file, repo root).
- Conventions: stderr messages prefixed `captain-obvious:`; degradations
  always say so (see the mypy notes in `skills/captain-obvious/scripts/co_py/mypy_pass.py:212-221` for tone).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `python3 -m unittest discover -v tests` | `OK` |
| New tests | `python3 -m unittest tests.test_check_gate -v` | `OK` |
| TS setup | `npm install typescript@^5 --no-save --no-audit --no-fund` | exit 0 |

## Scope

**In scope**:

- `skills/captain-obvious/scripts/captain_obvious_py.py` (`--check`, `--base`)
- `skills/captain-obvious/scripts/captain_obvious_ts.mjs` (same)
- `action.yml` (create, repo root)
- `.pre-commit-hooks.yaml` (create, repo root)
- `README.md` (a "CI gate" subsection under Prevention)
- `tests/test_check_gate.py` (create)
- `plans/README.md` (status row)

**Out of scope**:

- `hooks/prevent.py` — do NOT refactor the keying into a shared module in
  this plan; duplicate the 3-line keying with a comment cross-referencing
  `prevent.py` (the hook must stay a self-contained fail-open script; a
  shared import adds a failure mode there).
- Any `--fix` interaction (reject the combination).
- Gating on advisory findings, ever.

## Git workflow

- Branch: `feat/check-gate`
- Conventional commits: `feat(cli): --check/--base report-only gate for new proven findings`,
  `feat(ci): composite action + pre-commit hook for --check`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Python `--check`

In `captain_obvious_py.py`:

1. Args: `ap.add_argument("--check", action="store_true", help="exit 1 if any proven syntactic finding is newly introduced vs --base (report-only; implies no writes)")`;
   `ap.add_argument("--base", default=None, help="git ref to compare against for --check (e.g. origin/main)")`.
   Reject combos: `--check` with `--fix` → `ap.error("--check is report-only — it cannot be combined with --fix")`; `--check` without `--base` → `ap.error("--check requires --base <ref>")`.
2. After the report is assembled (post-summary, pre-`return 0`), when
   `args.check`:
   - `changed = git diff --name-only <base>...HEAD` run with `cwd=root`
     (`subprocess.run(["git", "diff", "--name-only", f"{args.base}...HEAD"], ...)`). Any git failure → print
     `captain-obvious: --check could not diff against {base} (<first stderr line>) — treating as clean (fail-open)` to stderr, `return 0`.
   - Candidate findings: `level == "proven"` and `category != "type-guaranteed"`
     and the finding's file (relpath) is in `changed`.
   - For each candidate file: `git cat-file -e {base}` guard once up front
     (invalid ref → the fail-open path above). Then
     `git show {base}:{relpath}`; if it exits non-zero (file new in this
     branch) → base findings set is empty; else run
     `[sys.executable, __file__, "--file", abspath, "--stdin"]` with the
     base content as input, parse its JSON, and build the seen-set
     `{(f["category"], f["test"]) for f in findings if level proven}`.
   - `new = [f for f in candidates if (f["category"], f["test"]) not in seen_for_file]`.
   - If `new`: print each as
     `captain-obvious: NEW proven finding: {file}:{line} ({category}) "{test}" — {reason}`
     to stderr and `return 1`. Else print
     `captain-obvious: --check clean — no newly-introduced proven findings vs {base}` and `return 0`.

**Verify**: `python3 skills/captain-obvious/scripts/captain_obvious_py.py --path . --no-types --check --base HEAD` (run at repo root) → exit 0, "clean" message (this repo's tests dir vs itself).

### Step 2: TS `--check`

Mirror step 1 in `captain_obvious_ts.mjs`: parse `--check`/`--base`
(`argv.includes` / `argVal`), same rejections (exit 2 with a
`captain-obvious:` message), same algorithm — `child_process.spawnSync` for
git and for re-invoking `process.argv[1]` with `--file --stdin` (pass base
content via `input:`). Same fail-open rules, same exit codes (1 = new
findings, 0 = clean/fail-open), same `type-guaranteed` exclusion.

**Verify**: `node skills/captain-obvious/scripts/captain_obvious_ts.mjs --project . --check --base HEAD` → exit 0, clean message.

### Step 3: Tests

`tests/test_check_gate.py` (pattern `test_fix_plan.py`, plus
`git init`/`git commit` in the tempdir — see `tests/test_fix_guard.py` for
the repo-fixture idiom; set `GIT_AUTHOR_*`/`GIT_COMMITTER_*` env or
`git -c user.email=... -c user.name=... commit`). Cases (Python CLI; one
mirrored happy-path case for the TS CLI behind `_ts_resolvable()`):

1. Repo with a committed clean test file; add a NEW test containing
   `assert True` (uncommitted or in a second commit); `--check --base
   <first-commit>` → exit 1, stderr mentions `constant-assert`.
2. Repo where the proven finding exists ALREADY in base and is unchanged:
   → exit 0 (pre-existing findings never gate).
3. Bad ref: `--check --base does-not-exist` → exit 0 + fail-open note.
4. `--check --fix` → exit 2 (argparse error).
5. Brand-new test file (absent from base) with a proven finding → exit 1.

**Verify**: `python3 -m unittest tests.test_check_gate -v` → `OK`.

### Step 4: Wrappers

`action.yml` (composite; inputs `path` default `.`, `base` default
`${{ github.event.pull_request.base.sha || 'origin/main' }}` is NOT
available in composite input defaults — take a plain `base` input default
`origin/main` and document overriding it):

```yaml
name: captain-obvious check
description: Fail if the PR introduces proven can-never-fail tests (report-only)
inputs:
  path:
    default: "."
  base:
    default: "origin/main"
runs:
  using: composite
  steps:
    - run: python3 "${{ github.action_path }}/skills/captain-obvious/scripts/captain_obvious_py.py" --path "${{ inputs.path }}" --no-types --check --base "${{ inputs.base }}"
      shell: bash
    - run: node "${{ github.action_path }}/skills/captain-obvious/scripts/captain_obvious_ts.mjs" --project "${{ inputs.path }}" --check --base "${{ inputs.base }}"
      shell: bash
```

`.pre-commit-hooks.yaml`:

```yaml
- id: captain-obvious-check
  name: captain-obvious (new proven dead tests)
  entry: python3 skills/captain-obvious/scripts/captain_obvious_py.py --no-types --check --base HEAD --path .
  language: system
  pass_filenames: false
```

**Verify**: YAML parses (`python3 -c "import json,sys;\nimport yaml" ` may
lack pyyaml — eyeball indentation against this plan); files exist at repo
root.

### Step 5: README

Under the "🛡️ Prevention (write-time hook)" section, add a short "CI gate"
paragraph: what `--check --base` does (fails only on NEWLY-introduced proven
syntactic findings; report-only; fail-open on git trouble), the action usage
snippet (`uses: <owner>/captain-obvious@main` with `base`), and one sentence
tying it to the SKILL.md stance: "this is the CI twin of the write-time
hook — a correctness gate on new dead tests, not a CI-time coverage
optimizer."

**Verify**: `rg -n "\-\-check" README.md` → ≥1.

## Test plan

- `tests/test_check_gate.py` — 5 Python cases + 1 TS happy path (step 3).
- Full suite: `python3 -m unittest discover -v tests` → all pass.

## Done criteria

- [ ] Both CLIs: `--check --base HEAD` on this repo → exit 0 "clean"
- [ ] `python3 -m unittest discover -v tests` exits 0 (incl. new gate tests)
- [ ] `--check --fix` rejected with exit 2 on both CLIs
- [ ] `action.yml` and `.pre-commit-hooks.yaml` exist at repo root
- [ ] README documents the gate with the hook-twin framing
- [ ] `git status --porcelain` shows only in-scope files
- [ ] `plans/README.md` status row updated

## STOP conditions

- You find yourself importing from `hooks/prevent.py` or refactoring it —
  out of scope by design; duplicate the 3-line keying instead.
- The gate fires on this repo's own tree at `--base HEAD` (case: exit 1 on
  unchanged code) — the newly-introduced logic is wrong; report.
- Implementing requires gating on advisory or type-guaranteed findings to
  make a test pass — the scope decision says exclude them; report the
  tension instead of widening.

## Maintenance notes

- The `(category, test)` key is shared BY CONVENTION with `prevent.py` (a
  comment in both places cross-references the other). If either keying
  changes, change both — candidate for a real shared module only when a
  third consumer appears.
- The action runs `--no-types` (Python) deliberately: CI speed and no mypy
  dependency; document-level parity with the syntactic-only comparison.
- Future: a `--check-json <file>` machine output for PR annotations —
  deferred until someone asks.
