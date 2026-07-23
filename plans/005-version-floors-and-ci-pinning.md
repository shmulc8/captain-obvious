# Plan 005: Enforce runtime floors (Python ≥3.9, TypeScript ≥4) and harden CI (least-privilege token, pinned action + typescript)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/captain_obvious_py.py skills/captain-obvious/scripts/captain_obvious_ts.mjs hooks/prevent.py .github/workflows/tests.yml README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition. Plan 004 adds steps to the same
> workflow file — expected drift if 004 is DONE; the steps this plan adds are
> disjoint from 004's.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (order after 004 to avoid workflow-file conflicts)
- **Category**: dependencies / security
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

- **Python floor.** The scripts call `ast.unparse` (added in Python 3.9) —
  e.g. `co_py/fixer.py:32`, `co_py/analyzer.py:431` — but nothing checks the
  interpreter: `rg "version_info" skills hooks` → zero matches. A user whose
  `python3` is 3.8 gets `AttributeError: module 'ast' has no attribute
  'unparse'` deep inside a scan, with no hint that the floor is the problem.
- **TypeScript floor.** The TS scanner loads whatever `typescript` the
  TARGET repo ships and validates only that `ts.sys` exists
  (`captain_obvious_ts.mjs:77-80`). Compiler-API shapes this code relies on
  differ meaningfully before TS 4.x; a TS 3.x target would misbehave mid-scan
  instead of failing with a clear "unsupported version".
- **CI posture.** `.github/workflows/tests.yml` has no `permissions:` block
  (job runs with the default, write-capable `GITHUB_TOKEN` on push) and pins
  `actions/checkout@v4` by mutable tag; the `typescript@^5` install floats to
  whatever 5.x is latest, making CI non-deterministic and exposed to a bad
  publish. The workflow needs only read access.

## Current state

- `skills/captain-obvious/scripts/captain_obvious_py.py:62-78` — `main()`
  starts directly with `argparse`; no version check. Entry:
  `if __name__ == "__main__": sys.exit(main())`.
- `hooks/prevent.py:77-81` — `main()` reads stdin JSON; the wrapper at
  135-140 catches all exceptions and exits 0 (fail open). A version guard
  here must also fail OPEN (return silently), never block a write.
- `captain_obvious_ts.mjs:77-80`:

```js
if (!ts || !ts.sys) {
  console.error(`captain-obvious: cannot load valid "typescript" from ${projectDir} (missing ts.sys).`);
  process.exit(2);
}
```

  `ts.version` is a string like `"5.9.3"` on every TS release ≥ 2.x.
- `.github/workflows/tests.yml` (all 16 content lines at `0eaabb5`):

```yaml
name: tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install typescript for the TS scanner tests
        run: npm install typescript@^5 --no-save --no-audit --no-fund
      - name: Run self-tests
        run: python3 -m unittest discover -v tests
```

- The locally verified typescript version the suite is developed against:
  `5.9.3`.
- `README.md:74-105` — "🚀 Install & use" section (next `##` at line 107); the
  natural place for the Python floor note is after the trust-boundary
  blockquote at the section's end.
- Exit-code conventions: the CLIs exit `2` for refusals with a
  `captain-obvious: ...` message on stderr (see the gitguard refusal at
  `captain_obvious_py.py:89-96`). Match that.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `python3 -m unittest discover -v tests` | `OK` |
| TS setup | `npm install typescript@^5 --no-save --no-audit --no-fund` | exit 0 |
| Checkout-action SHA (network) | `gh api repos/actions/checkout/git/ref/tags/v4.2.2 --jq .object.sha` | a 40-char SHA |

## Scope

**In scope**:

- `skills/captain-obvious/scripts/captain_obvious_py.py` (version guard)
- `hooks/prevent.py` (version guard, fail-open)
- `skills/captain-obvious/scripts/captain_obvious_ts.mjs` (ts.version floor)
- `.github/workflows/tests.yml` (permissions, pinned checkout, pinned typescript)
- `README.md` (floor documentation, one short addition)
- `tests/test_version_floor.py` (create)
- `plans/README.md` (status row)

**Out of scope**:

- Adding a `package.json`/`pyproject.toml` — the zero-manifest layout is
  deliberate (skill portability).
- `co_py/`/`co_ts/` internals.
- The CI steps plan 004 added (skip-guard) — leave them untouched.

## Git workflow

- Branch: `chore/version-floors-ci-pinning`
- Conventional commits: `fix(cli): fail fast below Python 3.9`,
  `fix(ts): require typescript >= 4`, `ci: least-privilege token, pinned checkout and typescript`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Python floor guard in the CLI

At the very top of `main()` in `captain_obvious_py.py` (before argparse):

```python
    if sys.version_info < (3, 9):
        print("captain-obvious: requires Python 3.9+ (this is Python "
              f"{sys.version_info.major}.{sys.version_info.minor}; ast.unparse is missing below 3.9)",
              file=sys.stderr)
        return 2
```

In `hooks/prevent.py`, first line of `main()` (fail OPEN — the hook must
never block a write because of an old interpreter):

```python
    if sys.version_info < (3, 9):
        return
```

**Verify**: `python3 -m unittest discover -v tests` → `OK` (guards are
no-ops on modern interpreters). Manual floor simulation is not required.

### Step 2: TypeScript floor in the TS scanner

Immediately after the existing `if (!ts || !ts.sys)` block in
`captain_obvious_ts.mjs`, add:

```js
const tsMajor = parseInt(String(ts.version ?? '0').split('.')[0], 10);
if (!Number.isFinite(tsMajor) || tsMajor < 4) {
  console.error(`captain-obvious: unsupported typescript version ${ts.version} loaded from ${projectDir} — need >= 4.0. Upgrade the project's typescript or run with a newer one installed.`);
  process.exit(2);
}
```

**Verify**: full suite (with typescript installed) → `OK`; and
`node skills/captain-obvious/scripts/captain_obvious_ts.mjs --project . --json /tmp/co.json && echo fine` → `fine` (5.x passes the floor).

### Step 3: Version-floor tests

Create `tests/test_version_floor.py` (pattern `tests/test_fix_plan.py` for
imports/constants). Two cases:

1. CLI guard message exists: run
   `python3 -c "import ast,sys; src=open('<CLI>').read(); assert 'version_info' in src and '3, 9' in src"`
   style check — or simpler and better, run the CLI under a fake floor:
   `subprocess.run([sys.executable, "-c", "import sys; sys.version_info=(3,8,0); exec(open(CLI).read())"])`
   is fragile — do NOT do that. Instead assert statically: read the CLI
   source, `ast.parse` it, and assert a `version_info` comparison exists in
   `main` (walk for `ast.Attribute(attr='version_info')`). Same static
   assertion for `hooks/prevent.py`.
2. TS floor: read `captain_obvious_ts.mjs` and assert the string
   `"unsupported typescript version"` is present, and that running the CLI
   against this repo (which has TS 5.x) still exits 0 in report mode (reuse
   the `_ts_resolvable` guard from `tests/test_literal_tautology_ts.py`).

**Verify**: `python3 -m unittest tests.test_version_floor -v` → `OK`.

### Step 4: Harden the workflow

Edit `.github/workflows/tests.yml`:

1. Add a top-level `permissions:` block right after `on:`:

```yaml
permissions:
  contents: read
```

2. Pin the checkout action by commit SHA. Resolve the SHA for the latest
   v4.x tag with the `gh api` command from the table (v4.2.2's published SHA
   is `11bd71901bbe5b1630ceea73d27597364c9af683`; verify it with the command
   if network is available — if `gh` is unavailable or offline, use that SHA
   as-is, it is the widely published v4.2.2 ref):

```yaml
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
```

3. Pin typescript exactly (the version the suite is developed against):

```yaml
      - name: Install typescript for the TS scanner tests
        run: npm install typescript@5.9.3 --no-save --no-audit --no-fund
```

**Verify**: YAML sanity — indentation matches siblings; `rg -n "permissions|checkout@|typescript@" .github/workflows/tests.yml` shows all three changes; full local suite still `OK` (workflow changes don't affect it).

### Step 5: Document the floors

In `README.md`, at the end of the "🚀 Install & use" section (after the
trust-boundary blockquote ending at line 105, before the next `##` at line
107), add one line:

```markdown
Requires Python ≥ 3.9 for the Python scanner and hook; the TS scanner uses the target repo's own `typescript` and requires ≥ 4.0.
```

**Verify**: `rg -n "Python ≥ 3.9|Python >= 3.9" README.md` → 1 match.

## Test plan

- `tests/test_version_floor.py` as in step 3.
- Full suite: `python3 -m unittest discover -v tests` → all pass.

## Done criteria

- [ ] `rg -c "version_info" skills/captain-obvious/scripts/captain_obvious_py.py hooks/prevent.py` → ≥1 each
- [ ] `rg -c "unsupported typescript version" skills/captain-obvious/scripts/captain_obvious_ts.mjs` → 1
- [ ] `.github/workflows/tests.yml` has `permissions: contents: read`, a SHA-pinned checkout, and `typescript@5.9.3`
- [ ] `python3 -m unittest discover -v tests` exits 0
- [ ] README documents the floor
- [ ] `git status --porcelain` shows only in-scope files
- [ ] `plans/README.md` status row updated

## STOP conditions

- CI (if the operator runs it) fails on the pinned SHA — the SHA doesn't
  match the tag; re-resolve with `gh api` and report if it still differs
  from the one in this plan.
- Any existing test starts failing after the guards (they must be pure
  no-ops on ≥3.9 / TS ≥4).
- You are tempted to add a manifest file — out of scope, deliberate design.

## Maintenance notes

- Bumping the pinned typescript is now a deliberate act (one line in CI);
  do it when the suite is validated against the new version.
- If a future detector needs a Python ≥3.10 feature, raise the floor guard
  AND the README line in the same commit — the guard is now the single
  source of truth for the floor.
- Dependabot/renovate could keep the pinned SHA fresh; deferred — not worth
  the config for one action.
