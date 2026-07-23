# Plan 007: Deterministic TS discovery order, documented JSON shape (incl. `snippet`), and a cross-language parity corpus

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/co_ts/discovery.mjs skills/captain-obvious/references/detectors.md tests/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S–M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

Three consistency gaps between the two engines:

1. **Discovery order.** Python sorts (`co_py/discovery.py:25` returns
   `sorted(out)`); TS returns raw `fs.readdirSync` order
   (`co_ts/discovery.mjs:21`). The proven-deletable SET is order-independent,
   but finding order and which file gets cited as "first" in cross-file
   advisory-duplicate messages (`co_ts/duplicates.mjs` `seenGlobal`) vary by
   machine. The repo's stated goal is "fully deterministic" detection —
   reports should be byte-stable across machines too.
2. **Undocumented `snippet` field.** TS findings carry `snippet`
   (`co_ts/analyzer.mjs:67`); Python findings don't (`co_py/models.py:11-20`).
   The documented JSON shape (`references/detectors.md:3-4`) lists neither.
   A consumer parsing both outputs hits an undocumented asymmetry.
3. **No parity harness.** The two engines re-implement one catalog by hand;
   nothing mechanically catches a detector change landing on one side only.
   A small shared corpus (same logical test in both languages → same
   `category`/`level`) makes the `CLAUDE.md` parity rule partially
   mechanical.

## Current state

- `co_ts/discovery.mjs:7-22` — `findTestFiles` builds `found` via recursive
  `walk` and returns it unsorted:

```js
  })(root);
  return found;
}
```

- `co_py/discovery.py:18-25` — the Python counterpart ends
  `return sorted(out)`.
- `co_ts/analyzer.mjs:61-70` — `toReportFinding` includes
  `snippet: f.stmtRef ? f.stmtRef.getText(rec.sf).slice(0, 160) : undefined`.
- `references/detectors.md:3-8` — documents the shared shape:
  "Both scripts emit the same JSON shape: `{findings: [{file, line, test,
  category, level, deletable, reason}], summary, plan/fixed}`..." — no
  mention of `snippet`.
- Syntactic categories that exist on BOTH sides with the same names (fixture
  material for the corpus): `constant-assert`, `boundary-tautology`,
  `local-const-echo`, `self-compare-call`, `dead-assert`, `duplicate-test`
  (same-file), `skipped-test`, `silent-smoke`, `no-assert`. Python runs them
  with `--no-types`; TS runs them without a tsconfig (both CLIs degrade to
  syntactic-only and still emit these).
- CLI invocation patterns: Python — `tests/test_fix_plan.py`; TS —
  `tests/test_literal_tautology_ts.py` (incl. `_ts_resolvable()` guard).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| TS setup | `npm install typescript@^5 --no-save --no-audit --no-fund` | exit 0 |
| Full suite | `python3 -m unittest discover -v tests` | `OK` |
| New tests | `python3 -m unittest tests.test_parity_corpus -v` | `OK` |

## Scope

**In scope**:

- `skills/captain-obvious/scripts/co_ts/discovery.mjs` (one-line sort)
- `skills/captain-obvious/references/detectors.md` (JSON-shape sentence)
- `tests/test_parity_corpus.py` (create)
- `plans/README.md` (status row)

**Out of scope**:

- Adding `snippet` to the Python side (decided: document the asymmetry
  instead — cheaper, zero risk, and the field is genuinely TS-extra).
- Engine unification of any kind.
- Type-dependent categories in the corpus (they need mypy/tsconfig; plans
  003/008 cover them).

## Git workflow

- Branch: `chore/determinism-parity`
- Conventional commits: `fix(ts): sort discovered test files for deterministic reports`,
  `docs(detectors): document the TS-only snippet field`,
  `test: cross-language parity corpus for syntactic detectors`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Sort TS discovery

In `co_ts/discovery.mjs`, change the end of `findTestFiles` to:

```js
  })(root);
  return found.sort();
}
```

**Verify**: full suite → `OK`.

### Step 2: Document `snippet`

In `references/detectors.md`, extend the shape sentence (lines 3-8) with one
sentence after "…`plan/fixed}`":

```markdown
The TS scanner additionally attaches an optional `snippet` field (first 160
chars of the flagged statement) to findings that reference a concrete
statement; the Python scanner does not emit it — consumers must treat it as
optional.
```

**Verify**: `rg -n "snippet" skills/captain-obvious/references/detectors.md` → 1 match.

### Step 3: Parity corpus

Create `tests/test_parity_corpus.py`. Structure: a module-level list of
corpus entries, each `(name, py_src, ts_src, expected_category,
expected_level)`; one unittest method iterates it twice (one `subTest` per
entry per language), running the Python CLI with `--no-types` and the TS CLI
with no tsconfig in the fixture dir, and asserting the finding for the
corpus test carries the expected `(category, level)` in BOTH reports.

Corpus (6 entries — keep each fixture minimal, one test per file so findings
map 1:1):

| name | Python body | TS body | expected |
|---|---|---|---|
| constant | `assert True` | `expect(true).toBe(true);` | `constant-assert`, `proven` |
| boundary | `data = compute()` + `assert len(data) >= 0` | `const d = compute();` + `expect(d.length).toBeGreaterThanOrEqual(0);` | `boundary-tautology`, `proven` |
| const-echo | `x = 5` + `assert x == 5` | `const x = 5;` + `expect(x).toBe(5);` | `local-const-echo`, `proven` |
| self-compare | `assert compute(1) == compute(1)` | `expect(compute(1)).toEqual(compute(1));` | `self-compare-call`, `advisory` |
| dead-assert | `return` then `assert compute()` (top level of the test) | `return;` then `expect(compute()).toBe(1);` | `dead-assert`, `proven` |
| duplicate | two same-scope tests with identical ≥8-token bodies | same in TS | `duplicate-test`, `proven` (on the second test) |

Notes for the executor:

- TS `dead-assert`: the statements must be top-level statements of the test
  callback (`() => { return; expect(...)... }`). If the TS scanner reports a
  different category for this shape, record what it emits and treat per the
  STOP conditions — the point of the corpus is exactly to surface such
  divergence.
- The Python fixture files need `from app import compute` where used, like
  `tests/test_fix_plan.py`'s fixtures; the TS ones can `declare function
  compute(n?: number): any;`? No — no tsconfig means no type checking, a
  bare `function compute(n) { return n; }` in the same file is simplest and
  keeps the file parseable.
- Assert on the finding for the specific corpus test name; ignore unrelated
  findings in the same report.

**Verify**: `python3 -m unittest tests.test_parity_corpus -v` → `OK`
(12 subtests).

## Test plan

- `tests/test_parity_corpus.py` (step 3).
- Full suite: `python3 -m unittest discover -v tests` → all pass.

## Done criteria

- [ ] `rg -n "found.sort" skills/captain-obvious/scripts/co_ts/discovery.mjs` → 1 match
- [ ] `rg -c "snippet" skills/captain-obvious/references/detectors.md` → ≥1
- [ ] `python3 -m unittest discover -v tests` exits 0 (incl. 12 parity subtests)
- [ ] `git status --porcelain` shows only in-scope files
- [ ] `plans/README.md` status row updated

## STOP conditions

- Any corpus entry yields DIFFERENT categories/levels between the two
  engines: that is a real parity break, exactly what the corpus exists to
  catch. Do not bend the expected value to whichever side "wins" — report
  the divergence (both raw findings) and mark the plan BLOCKED on a
  maintainer decision.
- The sort in step 1 changes any existing test's expectations (none index on
  order today; if one does, report it).

## Maintenance notes

- New syntactic detectors should gain a corpus row in the same PR — the
  corpus is the mechanical half of the CLAUDE.md parity rule.
- The corpus deliberately excludes type-dependent categories; if a shared
  fixture harness for those emerges later (plans 003 + 008 fixtures), fold
  them in then.
