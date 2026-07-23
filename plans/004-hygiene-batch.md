# Plan 004: Hygiene batch — document `silent-smoke`, hoist duplicated `isNoiseCall`, add repo `CLAUDE.md`, make CI fail on silently-skipped TS tests

> **Executor instructions**: Follow this plan step by step. The four tasks are
> independent — verify each on its own before the next. If anything in the
> "STOP conditions" section occurs, stop and report — do not improvise. When
> done, update the status row for this plan in `plans/README.md` — unless a
> reviewer dispatched you and told you they maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- README.md skills/captain-obvious/references/ skills/captain-obvious/scripts/co_ts/analyzer.mjs .github/workflows/tests.yml`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (but see index note — plans 004/005/008 all touch
  `.github/workflows/tests.yml`; execute them in index order to avoid
  conflicts)
- **Category**: docs / tech-debt / dx
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

Four small, unrelated frictions:

1. **`silent-smoke` is undocumented.** It is the newest detector (commit
   `75f67da`), emits `proven` / `deletable: safe` — i.e. `--fix`
   auto-deletes under this category — yet `rg silent-smoke` over `README.md`,
   `skills/captain-obvious/SKILL.md`, and
   `skills/captain-obvious/references/detectors.md` returns zero hits. Users
   see deletions under a category no documentation explains, undermining the
   "we only delete what we can prove, here's the catalog" trust story.
2. **`isNoiseCall` is copy-pasted.** The identical ~23-line closure appears
   twice inside `analyzeTest` (`co_ts/analyzer.mjs:176-198` and `:235-257`).
   Any noise-list change must be made twice or the copies drift.
3. **No repo `CLAUDE.md`.** Contributors (mostly agents, per git history)
   re-derive the non-obvious rules every session: the py/ts parity
   constraint, the unittest-not-pytest test incantation, the stdlib-only /
   zero-dep constraint.
4. **A green local/CI run doesn't prove the TS half ran.** Every TS-invoking
   test class is `@unittest.skipUnless(_ts_resolvable(), ...)`; without
   `npm install typescript`, all silently skip and the suite still exits 0.
   CI installs typescript today, but nothing fails if that step regresses.

## Current state

- `README.md:129-148` — "🔍 Detector catalog" markdown table; each row is
  `| category | example | level |`. No `silent-smoke` row.
- `skills/captain-obvious/references/detectors.md:10-31` — "## Categories"
  table, same shape, richer prose. No `silent-smoke` row. Line 23 has the
  adjacent `never-asserts` row for wording reference.
- The detector itself, `co_py/analyzer.py:175-184` (TS equivalent at
  `co_ts/analyzer.mjs:213-218`): fires on assertion-free tests where either
  (a) every call is wrapped in try/except with a silent catch, or (b) the
  test contains no calls at all. Reasons emitted:
  - "assertion-free test where every call is wrapped in a try/except with a
    silent catch — the test can never fail"
  - "assertion-free test containing no calls — the test does nothing and can
    never fail"
  Both proven / safe. Contrast: plain `no-assert` (a smoke test with real,
  unguarded calls) stays advisory/report-only per ICSE'19 — that distinction
  is the whole point of the new category and MUST be stated in the docs.
- `co_ts/analyzer.mjs:176-198` and `:235-257` — two byte-identical
  `const isNoiseCall = (expr) => { ... }` closures inside `analyzeTest`
  (walk down through call/property/element/nonnull/await/paren wrappers;
  return true for console/log/logger/logging roots). They close over `ts`
  only.
- No `CLAUDE.md` / `AGENTS.md` at repo root.
- `.github/workflows/tests.yml` (17 lines): checkout → `npm install
  typescript@^5 --no-save --no-audit --no-fund` → `python3 -m unittest
  discover -v tests`.
- The skip guard pattern, `tests/test_literal_tautology_ts.py:28-34,47`:
  `_ts_resolvable()` runs `node -e "import('typescript')..."` with
  `cwd=SCRIPTS`; typescript resolvable ⇒ those classes do not skip.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `python3 -m unittest discover -v tests` | `OK` |
| TS present | `npm install typescript@^5 --no-save --no-audit --no-fund` | exit 0 |
| Doc grep | `rg -c "silent-smoke" README.md skills/captain-obvious/references/detectors.md` | ≥1 per file |

## Scope

**In scope**:

- `README.md` (one table row)
- `skills/captain-obvious/references/detectors.md` (one table row)
- `skills/captain-obvious/scripts/co_ts/analyzer.mjs` (hoist only — zero
  behavior change)
- `CLAUDE.md` (create, repo root)
- `.github/workflows/tests.yml` (add one verification step)
- `plans/README.md` (status row)

**Out of scope**:

- `skills/captain-obvious/SKILL.md` — it names categories only in passing;
  don't grow it here.
- Detector behavior, `co_py/analyzer.py`, hook files.
- `permissions:`/action-pinning in the workflow — that is plan 005; don't do
  it here even though you're editing the same file.

## Git workflow

- Branch: `chore/hygiene-batch`
- One conventional commit per task: `docs(detectors): document silent-smoke`,
  `refactor(ts): hoist duplicated isNoiseCall`, `docs: add repo CLAUDE.md`,
  `ci: fail when TS scanner tests are skipped`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Document `silent-smoke`

Add to the `README.md` detector table (place directly above the `no-assert`
row so the contrast reads naturally):

```markdown
| `silent-smoke` | assertion-free test whose every call is silently try/caught — or that contains no calls at all — it can do nothing and can never fail | proven |
```

Add to `references/detectors.md` "## Categories" table, same position
(above `no-assert`):

```markdown
| `silent-smoke` | Assertion-free test that cannot even fail by raising: every call it makes is wrapped in a `try`/`except`-`pass` (the handler absorbs any raise), or the body contains no calls at all. Unlike `no-assert` — a legitimate smoke test whose unguarded calls still fail the test on raise — a silent-smoke test has no failure path whatsoever | proven |
```

**Verify**: `rg -c "silent-smoke" README.md skills/captain-obvious/references/detectors.md` → `1` and `1` (or more). Render check: `rg -A2 "silent-smoke" README.md` shows an intact `| ... | ... | proven |` row.

### Step 2: Hoist `isNoiseCall`

In `co_ts/analyzer.mjs`: add ONE module-level function (place it near the
other top-level helpers, e.g. after `isOutermostAssertCall`):

```js
export function isNoiseCall(ts, expr) {
  /* body: byte-identical to the current closures at lines 176-198 / 235-257,
     with the same walk-down loop — only the signature changes */
}
```

Delete both inner `const isNoiseCall = (expr) => {...}` closures and replace
their call sites with `isNoiseCall(ts, <expr>)`. Before editing, confirm the
two closures are still byte-identical:
`sed -n '176,198p' skills/captain-obvious/scripts/co_ts/analyzer.mjs > /tmp/a; sed -n '235,257p' skills/captain-obvious/scripts/co_ts/analyzer.mjs > /tmp/b; diff /tmp/a /tmp/b` → no output. (If line numbers have drifted,
locate both with `rg -n "const isNoiseCall" ...analyzer.mjs` first.)

**Verify**: `rg -c "isNoiseCall" skills/captain-obvious/scripts/co_ts/analyzer.mjs` → one definition + its call sites, no `const isNoiseCall` remaining; full suite → `OK` (the silent-smoke and never-asserts TS tests in `tests/test_never_asserts_ts.py` exercise this path).

### Step 3: Repo `CLAUDE.md`

Create `CLAUDE.md` at repo root with exactly this content (adjust nothing
else in):

```markdown
# captain-obvious — contributor notes

- **Run the self-tests**: `npm install typescript@^5 --no-save --no-audit --no-fund`
  once, then `python3 -m unittest discover -v tests`. The tests are unittest
  TestCase classes with pytest-style filenames — plain `pytest` also works,
  but CI runs unittest discover; keep new tests compatible with it (no pytest
  fixtures/parametrize).
- **TS tests skip silently** when `typescript` is not installed at the repo
  root. A fully green run must show zero `skipped` lines.
- **Parity rule**: the Python (`skills/captain-obvious/scripts/co_py/`) and
  TypeScript (`.../co_ts/`) engines implement the same detector catalog.
  A detector change lands on BOTH sides (analyzer.py + analyzer.mjs /
  classifier.mjs) plus a row in `references/detectors.md` and the README
  table, or states in the PR why it is language-specific.
- **Zero dependencies, on purpose**: the Python scripts are stdlib-only; the
  Node scripts import nothing but `node:*` and the TARGET repo's own
  `typescript`. The skill must run when copied bare into `~/.claude/skills/`.
  Never add a manifest dependency to `skills/`.
- **Finding contract**: every finding is `{file, line, test, category, level:
  proven|advisory, deletable: safe|aggressive|report-only, reason}`. Only
  `proven` + `deletable: safe` is auto-deleted by `--fix` — treat any change
  that widens that set as high-risk and test it in `tests/`.
- **Write-time hook**: `hooks/prevent.py`, off by default; enable with
  `CAPTAIN_OBVIOUS_HOOK=block|warn`. It must always fail open.
```

**Verify**: `python3 -c "print(open('CLAUDE.md').read().count('#'))"` → ≥1; file exists at repo root.

### Step 4: CI guard against silently-skipped TS tests

In `.github/workflows/tests.yml`, replace the last step with a
resolvability check + a skip-count assertion:

```yaml
      - name: Verify the TS scanner tests will run (typescript resolvable)
        run: node -e "import('typescript').then(()=>process.exit(0),e=>{console.error(e);process.exit(1)})"
        working-directory: skills/captain-obvious/scripts
      - name: Run self-tests
        run: |
          python3 -m unittest discover -v tests 2>&1 | tee /tmp/unittest.log
          test "${PIPESTATUS[0]}" -eq 0
          ! grep -E "skipped" /tmp/unittest.log
```

(`unittest -v` prints `... skipped '<reason>'` for each skipped test; the
suite currently has no legitimate always-skip tests, so any `skipped` line in
CI is a silent coverage hole. `bash` is the default shell on ubuntu runners,
so `PIPESTATUS` is available.)

**Verify** locally: `python3 -m unittest discover -v tests 2>&1 | grep -c skipped` → `0` (with typescript installed). Then `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/tests.yml'))" 2>/dev/null || npx --yes yaml-lint .github/workflows/tests.yml 2>/dev/null || echo "validate YAML by eye"` — at minimum confirm indentation matches the existing steps.

## Test plan

No new test files. Full suite green after each step:
`python3 -m unittest discover -v tests` → `OK`, zero `skipped`.

## Done criteria

- [ ] `rg -c "silent-smoke" README.md skills/captain-obvious/references/detectors.md` → both ≥1
- [ ] `rg -c "const isNoiseCall" skills/captain-obvious/scripts/co_ts/analyzer.mjs` → 0 matches; exactly one `function isNoiseCall` (or one module-level definition)
- [ ] `CLAUDE.md` exists at repo root with the parity rule and the test command
- [ ] `.github/workflows/tests.yml` contains the resolvability step and the `skipped` grep
- [ ] `python3 -m unittest discover -v tests` exits 0, zero `skipped` lines
- [ ] `git status --porcelain` shows only in-scope files
- [ ] `plans/README.md` status row updated

## STOP conditions

- The two `isNoiseCall` closures are NOT identical when diffed — they have
  already drifted; hoisting would silently change behavior on one path.
  Report the diff instead.
- Any test fails after the hoist.
- The skipped-grep step would fail in CI for a *legitimately* skipped test
  added since this plan — report rather than deleting the guard.

## Maintenance notes

- If a future test class gains a legitimate environment-dependent skip, the
  CI grep in step 4 must be updated to an allowlist — that is intentional
  friction: silent skips are how the TS half went dark in the first place.
  Heads-up: `tests/test_readonly_tree.py:46` ALREADY carries such a skip
  (`@unittest.skipIf(os.geteuid() == 0, ...)`); on a root/container runner it
  skips and step 4's `! grep -E "skipped"` gate would fail. Default
  `ubuntu-latest` runs non-root so it passes today — but if CI ever runs as
  root, allowlist that one skip rather than dropping the gate.
- The CLAUDE.md parity rule is enforced socially, not mechanically; plan 007
  adds the parity corpus that makes it partially mechanical.
