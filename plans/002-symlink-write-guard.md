# Plan 002: Refuse to write through symlinked test files in `--fix` and the mypy shadow pass

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/co_py/fixer.py skills/captain-obvious/scripts/co_py/mypy_pass.py skills/captain-obvious/scripts/co_ts/fixer.mjs tests/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition. NOTE: plan 001 rewrites the same
> region of `fixer.py` — if `plans/README.md` shows 001 DONE, expect the
> `lines_of`/write excerpts below to differ in read mechanics (that is
> expected drift, not a STOP); the write call and overall structure remain.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/001-fixer-line-integrity.md (same file; land 001 first)
- **Category**: security
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

`--fix` promises two things: it only touches files inside the scanned tree,
and everything it does is undoable with `git checkout -- <files>`
(`gitguard.py` refuses to run on a dirty tree for exactly this reason). A
test-named **symlink** breaks both promises: discovery lists symlinked
*files* (both walkers), the fixer opens them with a plain write — which
follows the link — and if the link target lives outside the repository,
`git status` was clean, the guard passed, and the out-of-tree write is
unrecoverable. The mypy shadow pass has a smaller variant: it writes
`_cap_obv_shadow_<name>` next to each test file; if that exact name already
exists as a symlink, the write follows it. This is defensive hardening
against pathological trees, not an observed incident; the fix is one shared
check at each write boundary.

## Current state

- `skills/captain-obvious/scripts/co_py/discovery.py:18-25` — `os.walk`
  (default `followlinks=False`) skips symlinked directories but still lists
  symlinked *files* in `filenames`; they match `is_test_filename` and are
  returned.
- `skills/captain-obvious/scripts/co_ts/discovery.mjs:12-18` — dirents that
  are not directories fall through to the filename match, so symlinked
  `*.test.ts` entries are collected too.
- `skills/captain-obvious/scripts/co_py/fixer.py` (`apply_fix`) writes each
  edited file in place. As of `0eaabb5`:

```python
        with open(file, "w", encoding="utf-8") as fh:
            fh.writelines(new)
        files_changed += 1
```

- `skills/captain-obvious/scripts/co_ts/fixer.mjs:79-85`:

```js
    for (const [file, edits] of editsByFile) {
      let text = fs.readFileSync(file, 'utf8');
      edits.sort((a, b) => b.start - a.start);
      for (const e of edits) text = text.slice(0, e.start) + text.slice(e.end);
      fs.writeFileSync(file, text);
      filesChanged++;
    }
```

- `skills/captain-obvious/scripts/co_py/mypy_pass.py:157-160` — shadow write:

```python
            shadow = os.path.join(os.path.dirname(file),
                                  SHADOW_PREFIX + os.path.basename(file))
            with open(shadow, "w", encoding="utf-8") as f:
                f.write("\n".join(out_lines) + "\n")
```

There is no `islink`/`lstat`/`realpath`/`O_NOFOLLOW` anywhere in the tree.

Conventions: Python stdlib-only; Node zero-dep ESM; degradation paths always
print a note rather than crash (see the `mypy not runnable` /
`cannot write reveal_type() shadow files` notes in `mypy_pass.py` for the
house style: skip + say so).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `python3 -m unittest discover -v tests` | `OK` |
| New tests only | `python3 -m unittest tests.test_symlink_guard -v` | `OK` |
| TS tests present | `npm install typescript@^5 --no-save --no-audit --no-fund` once at repo root, then full suite | TS-tagged tests not skipped |

## Scope

**In scope**:

- `skills/captain-obvious/scripts/co_py/fixer.py` (guard in `apply_fix`)
- `skills/captain-obvious/scripts/co_ts/fixer.mjs` (guard in the write loop)
- `skills/captain-obvious/scripts/co_py/mypy_pass.py` (guard before the shadow write)
- `tests/test_symlink_guard.py` (create)
- `plans/README.md` (status row)

**Out of scope**:

- Discovery filtering — scanning (reading) a symlinked test is fine and
  useful; only *writes* must refuse. Do not "fix" discovery.
- `gitguard.py` — its contract (clean tree) is orthogonal.

## Git workflow

- Branch: `fix/symlink-write-guard`
- Conventional commit, e.g. `fix(fixer): refuse to write through symlinked test files`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Python fixer guard

In `apply_fix` in `fixer.py`, before reading/writing each file in the
`for file, spans in edits_by_file.items():` loop, add:

```python
        if os.path.islink(file):
            print(f"captain-obvious: skipping {file} — symlinked test files "
                  "are never rewritten (the write would follow the link)",
                  file=sys.stderr)
            continue
```

Add `import sys` at the top of `fixer.py` (it currently imports only `ast`,
`os`, and models).

**Verify**: `python3 -m unittest tests.test_fix_plan tests.test_fix_guard -v` → `OK` (no regression).

### Step 2: mypy shadow-write guard

In `mypy_pass.py`, immediately after `shadow = os.path.join(...)` and before
`with open(shadow, "w", ...)`, add:

```python
            if os.path.islink(shadow):
                # a pre-existing symlink under the shadow name would be
                # written through — skip this file's probes instead
                continue
```

(The probes for that file simply never get a shadow, keep `revealed=None`,
and `resolve_probes` counts them nonredundant — the established degradation
behavior.)

**Verify**: `python3 -m unittest tests.test_mypy_degradation tests.test_flat_layout_laundering -v` → `OK`.

### Step 3: TS fixer guard

In `fixer.mjs`, inside the write loop, before `fs.readFileSync`:

```js
      if (fs.lstatSync(file).isSymbolicLink()) {
        console.error(`captain-obvious: skipping ${file} — symlinked test files are never rewritten (the write would follow the link)`);
        continue;
      }
```

**Verify**: full suite after `npm install typescript@^5 --no-save` → `OK`.

### Step 4: Regression test

Create `tests/test_symlink_guard.py` (pattern: `tests/test_fix_plan.py` —
tempdir, subprocess CLI, `--no-types --fix --force`). Guard the class with
`@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")`.

Fixture: in tempdir `d`, create `outside/` next to it (a second
`tempfile.mkdtemp`), write the deletable fixture there as `target.py`:

```python
def test_dead():
    assert True
```

Then `os.symlink(target_path, os.path.join(d, "test_link.py"))`. Run the
Python CLI `--path d --no-types --fix --force`. Assert:

1. The content of `target.py` (read via its real path) is **unchanged**.
2. stderr of the CLI run contains `"symlinked test files are never rewritten"`.
3. The CLI exit code is 0 (skipping is not an error).

Add a second test doing the same for the TS CLI (fixture `lit.test.ts`
containing `test("truth", () => { expect(true).toBe(true); });` symlinked
into the scanned dir), guarded by the `_ts_resolvable()` helper — copy that
helper verbatim from `tests/test_literal_tautology_ts.py:28-34`. Invoke the
TS CLI as `--project <dir> --fix --force` — **not** `--path <dir> --no-types`:
the TS CLI has no `--path` (it is `--project`, default `'.'` —
`skills/captain-obvious/scripts/captain_obvious_ts.mjs:24`) and no `--no-types`
(arg parsing at `:24-31`); unknown flags are silently ignored, so
`--path`/`--no-types` would leave the scan running against the subprocess cwd
(repo root) instead of the symlink fixture dir — the symlinked `lit.test.ts`
is never scanned, the Step-3 guard never fires, and the test would pass green
while proving nothing (worse, `--fix --force` from repo root could mutate the
repo's own tests). Assert the same three things: symlink target unchanged,
stderr contains `"symlinked test files are never rewritten"`, exit code 0.

**Verify**: `python3 -m unittest tests.test_symlink_guard -v` → `OK`, and
temporarily reverting step 1 (`git stash` the fixer.py hunk) makes the Python
test FAIL (proves the test bites); restore with `git stash pop`. Mirror the
bite-check for TS: temporarily revert step 3 (the `fixer.mjs` guard) and
confirm the TS test FAILs, then restore — without this a vacuous pass from a
mis-invoked CLI would go undetected.

## Test plan

- `tests/test_symlink_guard.py`: py CLI + ts CLI symlink-skip cases as above.
- Verification: `python3 -m unittest discover -v tests` → all pass.

## Done criteria

- [ ] `python3 -m unittest discover -v tests` exits 0
- [ ] `rg -n "islink|isSymbolicLink" skills/captain-obvious/scripts/` shows the three guards (fixer.py, mypy_pass.py, fixer.mjs)
- [ ] `git status --porcelain` shows only in-scope files modified
- [ ] `plans/README.md` status row updated

## STOP conditions

- The `apply_fix` write loop no longer matches the excerpt AND plan 001 is
  not marked DONE — unexpected drift.
- The symlink test fails with the guard in place (platform semantics differ
  from the plan's model).
- You find yourself editing discovery to filter symlinks — out of scope.

## Maintenance notes

- Any NEW write path added later (e.g. a future `--fix` for another language)
  must add the same lstat/islink refusal; reviewers should look for it.
- Deliberately NOT enforced: containment of resolved paths under the scan
  root for regular files (realpath checks) — symlink refusal already covers
  the escape vector discovery can produce; revisit only if a future feature
  writes to paths it did not discover itself.
