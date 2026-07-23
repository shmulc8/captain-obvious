# Plan 008: One opt-in end-to-end test against real mypy (lock the `reveal_type` contract)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/scripts/co_py/mypy_pass.py tests/ .github/workflows/tests.yml`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (execute after 004/005 if they are pending — all
  three touch `.github/workflows/tests.yml`)
- **Category**: tests
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

The Python scanner's flagship path — `type-guaranteed` via batched
`reveal_type` probes — is currently tested ONLY against hand-written fake
mypy executables (`tests/test_flat_layout_laundering.py:47-65`,
`tests/test_mypy_degradation.py:51` build tiny scripts that print canned
mypy-shaped output). That is a sound hermetic-CI choice, but it means the
suite validates the code against a *mock of mypy's output format*. If a real
mypy release changes the `Revealed type is "..."` note shape, the invocation
flags, or path reporting, every probe silently resolves to nothing and the
whole category vanishes — with all tests green. One opt-in test that runs
real mypy locks the actual contract.

## Current state

- `co_py/mypy_pass.py:13` — the contract being locked:

```python
REVEAL_RE = re.compile(r'^(.*?):(\d+):(?:\d+:)?\s*note: Revealed type is "(.*)"\s*$')
```

- `co_py/mypy_pass.py:164-201` — mypy command resolution: explicit
  `--mypy` → `uv run mypy` (if `uv.lock`) → `mypy` on PATH →
  `python3 -m mypy`; flags used: `--no-error-summary --no-pretty
  --check-untyped-defs --warn-return-any --show-error-codes
  --show-column-numbers`.
- `co_py/mypy_pass.py:296-303` (`resolve_probes`): a probe on `assert
  isinstance(result, int)` whose revealed type is `int` (or
  `builtins.int*`) becomes `type-guaranteed` / `proven` / `safe`.
- The laundering-visible mechanics: `run_mypy_probes` needs source targets
  (`src/`, packages, or flat top-level modules — `mypy_pass.py:174-189`); a
  flat `app.py` next to the test file satisfies this
  (`laundering_visible=True`), so proven findings are not demoted.
- Skip-guard house style: `tests/test_never_asserts_ts.py:90` uses
  `@unittest.skipUnless(...)` with a probe helper.
- CI (`.github/workflows/tests.yml`) installs no mypy today.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Is real mypy available locally | `mypy --version` | prints a version (else the new test skips) |
| New test | `python3 -m unittest tests.test_real_mypy -v` | `OK` (or `skipped` without mypy) |
| Full suite | `python3 -m unittest discover -v tests` | `OK` |

## Scope

**In scope**:

- `tests/test_real_mypy.py` (create)
- `.github/workflows/tests.yml` (add a mypy install so the test bites in CI)
- `plans/README.md` (status row)

**Out of scope**:

- `co_py/mypy_pass.py` or any scanner code. If the real-mypy test FAILS,
  that is a genuine contract break to report, not to patch around here.
- Pinning mypy exactly (floats intentionally: the test's purpose is to catch
  real-mypy drift early).

## Git workflow

- Branch: `test/real-mypy-e2e`
- Conventional commit: `test(py): opt-in end-to-end test against real mypy`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: The test

Create `tests/test_real_mypy.py` (imports/constants pattern:
`tests/test_fix_plan.py`). Guard:

```python
@unittest.skipUnless(shutil.which("mypy"), "real mypy not on PATH")
```

Fixture in a tempdir:

`app.py`:

```python
def compute() -> int:
    return 5
```

`test_app.py`:

```python
from app import compute

def test_typed():
    result = compute()
    assert isinstance(result, int)
```

Run the CLI **without** `--no-types` and with the explicit command
`--mypy mypy` (pins the resolution order out of the test):
`subprocess.run([sys.executable, CLI, "--path", d, "--json", out, "--mypy", "mypy"], ...)`.

Assert:

1. `report["mypyNote"]` is `None` (mypy ran; no degradation note).
2. Exactly one finding with `category == "type-guaranteed"`,
   `level == "proven"`, `deletable == "safe"`, `test == "test_typed"`.
3. The finding's `reason` contains `Revealed type` capture material —
   assert `"mypy already guarantees isinstance"` is in `reason` and
   `"int"` is in `reason`.
4. No `_cap_obv_shadow_*` file remains in the tempdir after the run
   (`glob` for it) — the shadow cleanup contract.

**Verify**: `python3 -m unittest tests.test_real_mypy -v` → `OK` locally if
mypy is installed; otherwise shows `skipped 'real mypy not on PATH'` —
install it for a true run (`python3 -m pip install --user mypy` or
`uv tool install mypy`) and re-verify `OK` before calling this step done.

### Step 2: Make it bite in CI

In `.github/workflows/tests.yml`, add before the self-tests step:

```yaml
      - name: Install mypy for the real-mypy end-to-end test
        run: python3 -m pip install --quiet mypy
```

(If plan 004's skipped-grep guard is present, this also upgrades the
real-mypy test from opt-in to enforced in CI — the grep fails on its
`skipped` line if mypy is missing. That interaction is intended.)

**Verify**: YAML indentation matches sibling steps;
`rg -n "mypy" .github/workflows/tests.yml` → 1 install step.

## Test plan

- `tests/test_real_mypy.py` as above (4 assertions in one test method is
  fine; split if you prefer, keeping the single subprocess run shared via
  `setUpClass`).
- Full suite: `python3 -m unittest discover -v tests` → all pass, no
  `skipped` for the new test when mypy is installed.

## Done criteria

- [ ] `python3 -m unittest tests.test_real_mypy -v` → `OK` with real mypy installed
- [ ] `python3 -m unittest discover -v tests` exits 0
- [ ] CI workflow installs mypy
- [ ] `git status --porcelain` shows only in-scope files
- [ ] `plans/README.md` status row updated

## STOP conditions

- The test fails against the CURRENT real mypy: that means the
  `REVEAL_RE`/invocation contract is ALREADY broken for this mypy version —
  a live bug worth its own fix plan. Report the raw CLI output and the mypy
  version; do not modify scanner code in this plan.
- `mypyNote` comes back non-None with mypy installed (resolution or flag
  incompatibility) — same handling: report, don't patch.

## Maintenance notes

- This test intentionally floats with mypy releases; when it breaks in CI,
  the fix belongs in `co_py/mypy_pass.py` (parsing/flags), and the fake-mypy
  tests should be updated to the new real output shape in the same PR.
- Keep exactly ONE real-mypy test — the hermetic fakes remain the primary
  suite; this is a canary, not a second test bed.
