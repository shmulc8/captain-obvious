# Plan 012: SPIKE — evaluate a Go back-end (`go/parser` + `go/types` as the third engine)

> **Executor instructions**: This is a SPIKE, not a build. The deliverable is
> a WRITTEN REPORT plus throwaway prototype code that never merges to main
> tooling paths. Timebox: stop when the report can answer its questions, even
> if the prototype is ugly. If anything in the "STOP conditions" section
> occurs, stop and report. When done, update the status row for this plan in
> `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 0eaabb5..HEAD -- skills/captain-obvious/references/detectors.md skills/captain-obvious/scripts/`
> Drift here only matters if the finding JSON contract changed; compare
> "Current state" below against `references/detectors.md:3-8`.

## Status

- **Priority**: P3 (lowest — run last, or not at all; "not worth it yet" is
  an acceptable spike verdict)
- **Effort**: L (timebox: ~2 focused days)
- **Risk**: N/A at spike stage (no production code is touched)
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `0eaabb5`, 2026-07-22

## Why this matters

The tool's cited lineage is not TS/Python: Delplanque ICSE'19 is Pharo, RTj
is Java/JUnit, Robinson ESEC/FSE'23 is C++/Google Test
(`references/detectors.md:162-178`). The codebase is cleanly factored into
per-language engine pairs (`co_py/*`, `co_ts/*`) behind one JSON finding
contract, so a third engine is architecturally additive. Go is the cleanest
candidate: `go/parser` + `go/types` mirror the existing "AST + type oracle"
architecture, `testing.T` test discovery is conventional (`*_test.go`,
`func TestXxx(t *testing.T)`), and the ecosystem is large. But the assert
vocabulary differs radically (no assert statement; `t.Error/t.Fatal` +
testify), so whether the detector catalog transfers is a genuine open
question — hence a spike, not a build plan.

## Current state

The contract a third engine must emit (`references/detectors.md:3-8`):

```
{findings: [{file, line, test, category, level, deletable, reason}], summary, plan/fixed}
```

`level`: `proven` | `advisory`; `deletable`: `safe` | `aggressive` |
`report-only`. Existing engine layout to mirror: entry script
(`captain_obvious_py.py` / `captain_obvious_ts.mjs`) + engine dir
(`co_py/` / `co_ts/`) with discovery / analyzer / duplicates / fixer /
gitguard modules (plus TS-only `classifier`, Python-only `mypy_pass`).

Go-specific mapping questions the spike must answer (these ARE the
deliverable):

- **What is an "assertion" in Go?** Candidates: `t.Error*/t.Fatal*` calls,
  `t.Fail()`, testify `assert.*/require.*`, plain `if x != want {
  t.Errorf(...) }` blocks. The last is the dominant stdlib idiom — and it is
  a *conditional* assert by construction, which collides with the
  `conditional-assert` category's semantics. How is the catalog re-drawn so
  idiomatic Go isn't 100% "rotten green"?
- **Which categories transfer syntactically?** Expected transfers:
  `constant-assert` (testify `assert.True(t, true)`, `assert.Equal(t, 1,
  1)`), `self-compare-call`, `duplicate-test`, `dead-assert` (code after
  `t.Fatal`/`return`), `no-assert`/`silent-smoke` (a `TestXxx` that calls
  nothing or only `t.Log`), `skipped-test` (`t.Skip()` unconditionally).
  Expected NON-transfers: `swallowed-assert` (no try/catch; recover()-based
  swallowing is rare — check), `floating-async-assert` (no promises).
- **Is `go/types` a workable `type-guaranteed` oracle?** The analogs:
  testify `assert.NotNil(t, x)` where `x`'s type cannot be nil (non-pointer,
  non-interface, non-map/slice/chan/func) — provable from `types.Type`;
  `assert.IsType(t, T{}, x)` where `x`'s static type is exactly `T`. What
  are the escape hatches (interface{}/any, generics, reflection,
  `unsafe`) and can they be guarded as conservatively as the TS/Python
  sides guard casts/Any?
- **Distribution**: the skill runs scripts bare (stdlib Python / zero-dep
  node). A Go engine needs either a compiled binary per platform (breaks
  the "copy the folder" install) or `go run` (requires a Go toolchain on
  the user's machine). Which is acceptable, and what does the plugin
  manifest need?

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Go available | `go version` | go1.2x+ (if absent, STOP — spike needs a toolchain) |
| Prototype run | `go run ./spike/co_go --path <fixture>` | JSON on stdout |

## Scope

**In scope**:

- `spike/co_go/` (create — throwaway prototype, gitignored or clearly
  marked; NEVER wired into `skills/`)
- `plans/012-spike-report.md` (create — the actual deliverable)
- `plans/README.md` (status row)

**Out of scope**:

- ANYTHING under `skills/captain-obvious/` — the spike must not touch the
  shipping skill.
- CI wiring, docs, manifest changes.
- Polishing the prototype (error handling, flags, --fix — none of it).

## Git workflow

- Branch: `spike/go-backend`
- One commit; message `spike(go): third-engine feasibility prototype + report`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Fixture corpus

Write `spike/co_go/fixtures/` — one `_test.go` file per candidate category
(constant-assert via testify AND via stdlib `if 1 != 1 { t.Error(...) }`
inverse, self-compare, dead-after-Fatal, no-assert, unconditional
`t.Skip`, duplicate bodies, a `type-guaranteed` NotNil-on-value case).
Each fixture states its expected verdict in a comment.

**Verify**: `go vet ./spike/co_go/fixtures/...` → compiles.

### Step 2: Discovery + syntactic prototype

`spike/co_go/main.go`: walk for `*_test.go`, parse with `go/parser`, find
`func TestXxx(*testing.T)`, implement the 3 cheapest detectors
(constant-assert on testify literals, dead-assert after `t.Fatal`/`return`,
duplicate-test via normalized-body hash), emit the standard JSON contract.

**Verify**: running against the fixtures flags the expected cases and only
those; paste the JSON into the report.

### Step 3: `go/types` probe

Extend the prototype with ONE typed detector: `assert.NotNil(t, x)` where
`types.Type` of `x` is a non-nillable kind → proven. Use
`golang.org/x/tools/go/packages` for loading iff needed (note the dependency
in the report — it bears on distribution).

**Verify**: a fixture with `var x int = f(); assert.NotNil(t, x)`... —
testify NotNil on an `int` is itself a vet-flagged mistake; pick the
realistic form (`assert.NotNil(t, ptr)` where `ptr` is `*T` vs a
non-pointer struct value) and show the oracle distinguishes them.

### Step 4: The report

Write `plans/012-spike-report.md` answering, with evidence from steps 1-3:

1. Category transfer table: each existing category → transfers / adapts /
   n/a in Go, one line of reasoning each — including the
   `if !cond { t.Error }` vs `conditional-assert` collision and the
   proposed resolution.
2. The type-oracle verdict: is proven-tier `type-guaranteed` honest in Go,
   and what is the escape-hatch guard list?
3. Distribution recommendation (binary vs `go run` vs "don't ship Go").
4. Effort estimate for a real engine (with the spike as calibration).
5. **Go / no-go recommendation.**

**Verify**: the report exists and answers all five; the prototype JSON
excerpts are included.

## Test plan

None beyond fixture verification — spike code gets no test suite.

## Done criteria

- [ ] `plans/012-spike-report.md` exists and answers the five questions
- [ ] Prototype runs against the fixtures and its JSON matches the shared contract shape
- [ ] Nothing under `skills/` modified (`git diff --stat -- skills/` empty)
- [ ] `plans/README.md` status row updated with the go/no-go verdict

## STOP conditions

- No Go toolchain and none installable — record BLOCKED.
- The timebox (~2 days) expires — write the report with what exists; an
  incomplete prototype with an honest report IS a valid spike outcome.
- You catch yourself building the real engine (flags, fixer, gitguard) —
  stop, that decision belongs to the maintainer after the report.

## Maintenance notes

- If the verdict is GO: the real engine gets its own plan set (engine,
  parity-corpus rows, detectors.md sections, distribution). If NO-GO:
  record the reasons in `plans/README.md` "considered and rejected" so the
  next audit doesn't re-propose it.
- Java/JUnit (the RTj lineage) is the alternate third engine; the report
  should say in one paragraph whether anything learned changes that
  calculus.
