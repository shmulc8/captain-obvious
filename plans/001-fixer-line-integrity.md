# Plan 001: Make Python `--fix` edit by real newlines, preserve line endings, and cover the partial-removal path

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/co_py/ tests/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

`--fix` is this tool's most dangerous operation: it rewrites a *user's* test
files in place, and its entire safety story is "we delete exactly the proven
lines, and `git checkout` can undo it." Two reproduced bugs break that story:

1. **Wrong-line deletion (silent corruption).** The fixer reads files with
   `str.splitlines(keepends=True)` and then indexes the resulting list with
   `ast` line numbers. `str.splitlines()` splits on characters `ast` does NOT
   count as line breaks — form feed `\f`, vertical tab `\v`, `\x1c`–`\x1e`,
   NEL `\x85`, ` `, ` `. Any such byte earlier in a file (e.g. a
   form feed inside a string literal) desynchronizes the two numberings, and
   `--fix` deletes the wrong lines. The mangled output usually still parses,
   so nothing surfaces the corruption. This was reproduced end-to-end against
   the shipped CLI.
2. **CRLF destruction.** The fixer reads and writes in text mode. On
   macOS/Linux, a CRLF file comes back all-LF after `--fix` — every line of
   the file shows as changed in the diff, defeating review of the minimal
   deletion diff.

Additionally, the *partial-removal* branch (removing individual assertions
while keeping the test, including the `x = f()` → `f()` dangling-binding
rewrite that goes through `ast.unparse`) has **zero test coverage** — no test
in `tests/` ever exercises `assertionsToRemove > 0`. This plan adds
characterization tests first, then fixes both bugs.

The TS fixer (`co_ts/fixer.mjs`) is immune — it edits by character offsets
(`getFullStart()`/`getEnd()`) and does a byte-preserving string splice. It is
the reference model and is out of scope.

## Current state

Relevant files:

- `skills/captain-obvious/scripts/co_py/fixer.py` — the whole file (143
  lines). `plan_removals()` decides what to delete; `apply_fix()` rewrites
  files; `_dangling_edits()` rewrites orphaned `x = f()` bindings.
- `skills/captain-obvious/scripts/co_py/ast_utils.py` — shared AST helpers;
  the new line-splitting helper goes here.
- `skills/captain-obvious/scripts/co_py/analyzer.py` — line 488 computes probe
  indentation with the same buggy `splitlines()` (degrades safely today; fix
  for consistency).
- `skills/captain-obvious/scripts/co_py/mypy_pass.py` — line 145 builds shadow
  files with the same `splitlines()` (degrades safely today; fix for
  consistency).
- `tests/test_fix_plan.py` — the structural pattern to copy for new tests.

Key excerpts as of commit `0eaabb5`:

`fixer.py:52-55` (same pattern again at `fixer.py:116-119`):

```python
    def lines_of(f):
        if f not in file_lines:
            file_lines[f] = open(f, encoding="utf-8").read().splitlines(keepends=True)
        return file_lines[f]
```

`fixer.py:126-139` — 1-based `ast` line numbers (`s`, `e` come from
`node.lineno` / `node.end_lineno`) index into that list, then a plain
text-mode write:

```python
        replace = {}   # 1-based start line -> replacement text
        drop = set()
        for s, e, repl in spans:
            drop.update(range(s, e + 1))
            if repl is not None:
                replace[s] = repl
        new = []
        for i, l in enumerate(lines, 1):
            if i in replace:
                new.append(replace[i])
            elif i not in drop:
                new.append(l)
        with open(file, "w", encoding="utf-8") as fh:
            fh.writelines(new)
```

`fixer.py:29-35` — `_dangling_edits` computes indent from the same `lines`
list and hardcodes `"\n"` on the replacement line:

```python
        indent = " " * (len(lines[stmt.lineno - 1]) - len(lines[stmt.lineno - 1].lstrip()))
        if rhs is not None and any(isinstance(n, ast.Call) for n in ast.walk(rhs)):
            try:
                text = indent + " ".join(ast.unparse(rhs).split()) + "\n"
            except Exception:
                continue
            edits.append((stmt.lineno, stmt.end_lineno, text))
```

`analyzer.py:487-490` (indent for mypy probes — same splitlines pattern):

```python
            lines = open(path, encoding="utf-8").read().splitlines()
            indent = len(lines[a.lineno - 1]) - len(lines[a.lineno - 1].lstrip())
```

`mypy_pass.py:145` (shadow-file build — same pattern):

```python
            src_lines = open(file, encoding="utf-8").read().splitlines()
```

Why the desync happens: `ast` line numbers count only `\n`, `\r\n`, and `\r`
as line terminators. `str.splitlines()` additionally splits on
`\v \f \x1c \x1d \x1e \x85    `. One `\f` inside any string literal
above a deletion target shifts every subsequent index by one.

Repo conventions that apply:

- **Stdlib only** — the Python scripts must run bare (`python3 script.py`)
  with no pip installs. Do not add dependencies.
- Self-tests are unittest `TestCase` classes with pytest-style file names, run
  by `python3 -m unittest discover -v tests`. Model new tests on
  `tests/test_fix_plan.py`: tempdir fixture, write a small `test_app.py`,
  invoke the CLI via `subprocess.run([sys.executable, CLI, ...])`, parse the
  `--json` report.
- Commit style: conventional commits, e.g.
  `fix(discovery): ignore pytest fixtures starting with test_`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full self-test suite | `python3 -m unittest discover -v tests` | `OK` (62 tests at plan time; more after this plan) |
| Just the new tests | `python3 -m unittest tests.test_fixer_partial tests.test_fixer_line_integrity -v` | `OK` |
| Confirm ast vs splitlines mismatch (background fact) | `python3 -c "print(len('a\fb\nc'.splitlines()), len(__import__('ast').parse('a=1\nb=2').body))"` | prints `3 2` (splitlines saw 3 lines in a 2-newline-free string) |

No TypeScript needed for this plan.

## Scope

**In scope** (the only files you may modify):

- `skills/captain-obvious/scripts/co_py/fixer.py`
- `skills/captain-obvious/scripts/co_py/ast_utils.py` (add one helper)
- `skills/captain-obvious/scripts/co_py/analyzer.py` (only lines 487-490)
- `skills/captain-obvious/scripts/co_py/mypy_pass.py` (only line 145 and the
  `"\n".join(out_lines)` write that follows)
- `tests/test_fixer_partial.py` (create)
- `tests/test_fixer_line_integrity.py` (create)
- `plans/README.md` (status row)

**Out of scope** (do NOT touch):

- `skills/captain-obvious/scripts/co_ts/fixer.mjs` — offset-based, immune.
- Any detector/classifier logic in `analyzer.py` beyond the two indent lines.
- `gitguard.py`, entry scripts, hooks.

## Git workflow

- Branch: `fix/fixer-line-integrity`
- Conventional commits; suggested: one commit for the characterization tests,
  one for the fix. Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Characterization tests for the partial-removal path

Create `tests/test_fixer_partial.py` (model on `tests/test_fix_plan.py` —
same imports, same `REPO`/`CLI` constants, same tempdir + subprocess shape).
Fixture test file to write into the tempdir as `test_app.py`:

```python
from app import compute

def test_mixed():
    result = compute()
    resp = compute()
    assert len(resp) >= 0
    assert result == "expected"
```

`assert len(resp) >= 0` is a proven `boundary-tautology`; `assert result ==
"expected"` is real. So `--fix` must remove ONLY the tautology line, keep the
test, and rewrite the orphaned `resp = compute()` to a bare `compute()` (the
`_dangling_edits` path — `resp` was used only by the removed assert).

Test cases (all run the CLI with `--no-types`, using `--json` for the report
and `--fix --force` for the mutation, exactly as `test_fix_plan.py` does):

1. `plan.assertionsToRemove == 1` and `plan.testsToRemove == []` in the
   report-only run.
2. After `--fix --force`: the file still contains `def test_mixed`, still
   contains `assert result == "expected"`, no longer contains `len(resp)`,
   no longer contains `resp =`, but DOES still contain a bare `compute()`
   line (the dangling rewrite preserved the call).
3. The rewritten file re-parses: `ast.parse(open(path).read())` raises
   nothing.

**Verify**: `python3 -m unittest tests.test_fixer_partial -v` → `OK`. These
must pass BEFORE any fixer change — they characterize current behavior. If
any fails on unmodified code, STOP: the plan's model of `_dangling_edits` is
wrong, report what actually happened.

### Step 2: Failing regression tests for the two bugs

Create `tests/test_fixer_line_integrity.py`, same structural pattern. Two
test classes:

**(a) Control-byte desync.** Fixture (note the `\f` — write it via a Python
escape in the test source, e.g. `TEST_SRC = 'def test_keep():\n    x = "a\fb"\n    assert x\n\ndef test_dead():\n    assert True\n'`):

```python
def test_keep():
    x = "a\fb"
    assert x

def test_dead():
    assert True
```

`test_dead` is a proven whole-test removal (single constant assert). Run
`--no-types --fix --force`, then assert on the resulting file content:

- `"def test_dead"` absent
- `"assert True"` absent (with the bug, this line survives — it gets grafted
  into `test_keep` because deletion removed the wrong range)
- `"def test_keep"` present and `'x = "a\fb"'` present
- the file re-parses with `ast.parse`

**(b) CRLF preservation.** Write the `test_fix_plan.py`-style duplicate
fixture but with `\r\n` line endings (`open(path, "w", newline="")` and join
lines with `"\r\n"`). Run `--no-types --fix --force`. Assert the remaining
content still contains `"\r\n"` and contains no bare-`\n`-only lines (i.e.
`content.count("\n") == content.count("\r\n")`), and that the planned test
was removed.

**Verify**: `python3 -m unittest tests.test_fixer_line_integrity -v` → both
FAIL on unmodified code (this proves they reproduce the bugs). If either
PASSES before the fix, STOP and report — the repro doesn't match reality.

### Step 3: Add the newline-faithful splitter to `ast_utils.py`

Append to `skills/captain-obvious/scripts/co_py/ast_utils.py`:

```python
def split_lines_keepends(text: str) -> list[str]:
    """Split on \\n / \\r\\n / \\r ONLY — the exact line terminators the ast
    module counts — keeping the terminator on each line. str.splitlines()
    also splits on \\f, \\v, \\x1c-\\x1e, \\x85, \\u2028, \\u2029, which ast
    does not, so indexing splitlines() output by ast line numbers corrupts
    files containing those bytes."""
    lines = []
    start = i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            lines.append(text[start:i + 1])
            i += 1
            start = i
        elif c == "\r":
            j = i + 2 if i + 1 < n and text[i + 1] == "\n" else i + 1
            lines.append(text[start:j])
            i = j
            start = i
        else:
            i += 1
    if start < n:
        lines.append(text[start:])
    return lines
```

**Verify**: `python3 -c "import sys; sys.path.insert(0, 'skills/captain-obvious/scripts'); from co_py.ast_utils import split_lines_keepends as s; assert s('a\fb\nc\r\nd\re') == ['a\fb\n', 'c\r\n', 'd\r', 'e']; print('ok')"` → `ok`

### Step 4: Switch the fixer to raw reads + the new splitter

In `fixer.py`:

1. Import the helper: `from .ast_utils import split_lines_keepends` (extend
   the existing `from .models import ...` import block area).
2. Both `lines_of` closures (in `plan_removals` and `apply_fix`) become:

```python
    def lines_of(f):
        if f not in file_lines:
            file_lines[f] = split_lines_keepends(
                open(f, encoding="utf-8", newline="").read())
        return file_lines[f]
```

   `newline=""` disables universal-newline translation, so CRLF survives the
   round trip, and `split_lines_keepends` counts lines exactly as `ast` does
   (`\r\n` in the raw text is one terminator both ways).
3. In `apply_fix`, the write stays `open(file, "w", encoding="utf-8", newline="")`
   — add `newline=""` so Python does not re-translate `\n` on Windows.
4. In `_dangling_edits`, replace the hardcoded `"\n"` on the replacement line:
   derive the terminator from the line being replaced —

```python
            src_line = lines[stmt.lineno - 1]
            eol = "\r\n" if src_line.endswith("\r\n") else ("\r" if src_line.endswith("\r") else "\n")
            text = indent + " ".join(ast.unparse(rhs).split()) + eol
```

   (The `indent` computation above it already reads from `lines` — it now
   sees raw lines, and `len(line) - len(line.lstrip())` still yields leading
   whitespace count; leave it as is.)

**Verify**: `python3 -m unittest tests.test_fixer_line_integrity tests.test_fixer_partial -v` → ALL pass now.

### Step 5: Same splitter in analyzer probe-indent and mypy shadow build

- `analyzer.py:488`: change
  `lines = open(path, encoding="utf-8").read().splitlines()` to
  `lines = split_lines_keepends(open(path, encoding="utf-8", newline="").read())`
  and add the import `split_lines_keepends` to the existing
  `from .ast_utils import (...)` block at the top. The indent expression
  below it needs `.lstrip()` unchanged (trailing terminator doesn't affect
  the leading-whitespace subtraction... it does affect `len(lines[...])`, so
  strip the terminator for the indent computation:
  `raw = lines[a.lineno - 1].rstrip("\r\n")` then
  `indent = len(raw) - len(raw.lstrip())`).
- `mypy_pass.py:145`: change
  `src_lines = open(file, encoding="utf-8").read().splitlines()` to
  `src_lines = [l.rstrip("\r\n") for l in split_lines_keepends(open(file, encoding="utf-8", newline="").read())]`
  and add the corresponding import (`from .ast_utils import walk_no_nested_funcs, call_name` already exists — extend it).
  The shadow file is still joined with `"\n"` (line 160) — that is fine, the
  shadow is a throwaway mypy input, not user code.

**Verify**: `python3 -m unittest discover -v tests` → `OK`, no regressions.

## Test plan

- `tests/test_fixer_partial.py` — 3 characterization cases (step 1).
- `tests/test_fixer_line_integrity.py` — control-byte desync + CRLF
  preservation (step 2), passing after steps 3-4.
- Pattern: `tests/test_fix_plan.py`.
- Verification: `python3 -m unittest discover -v tests` → all pass (62 old +
  ≥5 new).

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `python3 -m unittest discover -v tests` exits 0
- [ ] `rg -n "splitlines" skills/captain-obvious/scripts/co_py/fixer.py` returns no matches
- [ ] `rg -n "splitlines" skills/captain-obvious/scripts/co_py/analyzer.py skills/captain-obvious/scripts/co_py/mypy_pass.py` shows the two indexed call sites (analyzer.py:488 / mypy_pass.py:145) gone, leaving exactly one legitimate match: `src.splitlines()` at analyzer.py:41 (that one feeds nothing indexed by ast linenos — leave it untouched)
- [ ] `git status --porcelain` shows only in-scope files modified
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1 characterization tests fail on UNMODIFIED code — the plan's model of
  the partial-removal path is wrong.
- Step 2 regression tests PASS on unmodified code — the bugs don't reproduce
  as described.
- After step 4, any pre-existing test in `tests/` fails and the fix isn't an
  obvious import/typo — the splitter changed behavior the suite depends on.
- You find yourself wanting to modify `fixer.mjs` or detector logic.

## Maintenance notes

- Any future code that indexes a line list by `ast` linenos MUST use
  `split_lines_keepends`, never `str.splitlines()`. That is the invariant this
  plan establishes; a reviewer should reject new `splitlines()` near lineno
  arithmetic.
- Plan 002 (symlink write guard) touches `apply_fix`'s write site — land this
  plan first to avoid conflicts.
- Deferred: lone-`\r` (classic-Mac) files are handled by the splitter but not
  explicitly tested; acceptable.
