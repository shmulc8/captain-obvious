# Plan Review — plans/001–012

Reviewed 2026-07-23 against working tree at HEAD `378cbe6` (plans authored at `0eaabb5`).
Method: baseline commands run first; every cited path/function/line in each plan checked
against the current tree; quoted commands sanity-checked (read-only ones executed);
internal consistency checked against `plans/README.md`; cross-plan file-touch matrix
built from all 12 plans.

## Baseline & tree-state judgment call

- `git diff 0eaabb5..HEAD` touches only `.claude-plugin/marketplace.json` (added) and
  `.gitignore`. **Every file the plans cite is byte-identical between plan-time and HEAD**,
  so all line references were checked against live content.
- The working tree has three **uncommitted deletions**: `.claude-plugin/plugin.json`,
  `hooks/prevent.py`, `skills/captain-obvious/scripts/captain_obvious_py.py`.
- Baseline on the working tree: `npm install typescript@^5 --no-save` OK, then
  `python3 -m unittest discover -v tests` → **FAILED (62 tests, 27 failures + 20 errors)**,
  every failure tracing to the deleted `captain_obvious_py.py` (e.g.
  `tests/test_single_file_mode.py:57` — `can't open file '.../captain_obvious_py.py'`).
- Re-run on a clean extract of HEAD (git archive → scratchpad, same install): **62 tests, OK** —
  exactly the README baseline.
- **Judgment call:** the deletions are session-local damage, not repo state the plans must
  accommodate; plans were reviewed against HEAD content, and each plan that invokes the
  deleted CLI carries an execution precondition note (restore via
  `git checkout HEAD -- skills/captain-obvious/scripts/captain_obvious_py.py` — left to the
  user; file deletion/restoration is out of scope for this review).

---

## 001 — fixer-line-integrity

Verdict: APPROVE

All excerpts verbatim against live source: `co_py/fixer.py:52-55`, `:116-119`, `:126-139`,
`_dangling_edits` `:29-35` (defs at `:7/:41/:112`; file is 143 lines as claimed);
`co_py/analyzer.py:487-490` probe; `analyzer.py:41` correctly scoped out; `co_py/mypy_pass.py:145,:160`;
`split_lines_keepends` absent (to be added); import blocks match (`fixer.py:5`, `analyzer.py:7`,
`mypy_pass.py:10`). Motivation confirmed: `rg assertionsToRemove tests/` = 0 matches.
Behaviorally confirmed on a scratch copy (report-only): mixed fixture → `assertionsToRemove=1`;
`\f` fixture → whole-test removal — both match the plan's premises. The proposed splitter was
tested: byte-exact round-trip; on the bug fixture ast=6 vs `splitlines(keepends)`=7 — reproduces
and fixes the desync.

Non-blocking notes:
- Done-criterion #3 (plan `001:364`) is self-contradictory as worded: "rg splitlines …
  matches only lines that are comments, or nothing" is false after a correct implementation
  because `analyzer.py:41` (`lines = src.splitlines()`) legitimately stays. The bullet's own
  parenthetical admits this; one-line reword recommended.
- Execution precondition: the plan's tests subprocess the deleted `captain_obvious_py.py` —
  restore from HEAD first (see judgment call above).

## 002 — symlink-write-guard

Verdict: NEEDS-CHANGES

All code citations check out: `co_py/discovery.py:18-25` (followlinks default), `co_ts/discovery.mjs:12-19`,
write excerpts exact at `co_py/fixer.py:138-140`, `co_ts/fixer.mjs:79-85`, `co_py/mypy_pass.py:157-160`;
no `islink|lstat|realpath|O_NOFOLLOW` anywhere in the tree (rg = 0); `_ts_resolvable()` exactly at
`tests/test_literal_tautology_ts.py:28-34`; guard APIs and placements sound. Dependency on 001 matches
README (both touch the fixer write path).

Required changes (Step 4, TS test):
1. Plan `002:183-190` quotes the Python invocation `--path <d> --no-types --fix --force` then says
   do "the same for the TS CLI." The TS CLI has **no `--path`** (it's `--project`, default `'.'` —
   `skills/captain-obvious/scripts/captain_obvious_ts.mjs:24`) and **no `--no-types` at all**
   (arg parsing at `captain_obvious_ts.mjs:24-31`). Unknown flags are silently ignored, so the scan
   runs against the subprocess cwd (repo root) instead of the symlink fixture dir; the symlinked
   `lit.test.ts` is never scanned, the Step-3 guard never fires, and the TS test passes green while
   proving nothing. With `--fix --force` from repo root it could even mutate the repo's own tests.
   Fix: spell out the TS invocation as `--project <dir> --fix --force`.
2. The Python case has a bite-check (plan `002:196-197`: revert Step 1, confirm the test fails);
   the TS case has none, so the vacuous pass above would go undetected. Add a mirrored TS
   revert-and-confirm-FAIL step.

Non-blocking: all Python verify commands invoke the working-tree-deleted `captain_obvious_py.py`
(flags confirmed at HEAD `:64-77`) — execution precondition, not a plan defect.

## 003 — deletion-path-fixtures

Verdict: APPROVE

Every cited guard/branch resolves: `co_ts/classifier.mjs:117-131` typeof-proven (helper `:97-100`),
`hasUnsafeCast` guards `:125,:133`, index-signature `:144-146`, unchecked-element-access `:141-143`;
`co_ts/mock_echo.mjs` direct `:59-68` / self-call `:45-57`; `co_py/analyzer.py` boundary `:433-440`,
const-echo `:442-452`, mock-echo `:468-476/:477-483`, ctor set `:352`; coverage plumbing
`captain_obvious_ts.mjs:158-187,:200,:207`; all three coverage formats match `co_py/coverage.py:29-56`
and `co_ts/coverage.mjs:17-44`. No filename collision with 006 (README note confirmed: 006 creates
`tests/test_laundering_ts.py`; 003's four files are distinct).

Non-blocking notes:
- Fixture-precision: Step-1 assertion-3's "tg cast guard" fixture (`003:159-162`, assert `:180`)
  passes, but suppression actually flows through `isBadType(any)` (`classifier.mjs:127`,
  `co_ts/type_predicates.mjs:19-24`), not the `hasUnsafeCast` guard (`classifier.mjs:125`) it claims
  to lock — `typeof v` has a bare identifier as operand. To genuinely pin the cast guard, inline the
  cast (e.g. `expect(typeof (n as any)).toBe("number")`). Test is green either way; not a STOP.
- Wording nit: plan `003:262` says duplicate body "≥ 8 tokens"; actual threshold is 8 *chars* of the
  canonicalized key (`co_ts/duplicates.mjs:15`). Fixture clears it regardless.

## 004 — hygiene-batch

Verdict: APPROVE

All citations exact: catalog table `README.md:129-148` (`no-assert` at `:142`); `detectors.md:10-31`
(`never-asserts` `:23`); silent-smoke detectors `co_py/analyzer.py:175-184` and
`co_ts/analyzer.mjs:213-218`; the two `isNoiseCall` closures at `analyzer.mjs:176-198` and `:235-257`
are byte-identical (diff empty — the plan's STOP condition for drifted closures is safely inert),
call sites `:204/:264`; no `CLAUDE.md`/`AGENTS.md` at root; `tests.yml` last step is the unittest run.
CI step-4 YAML checks out (working-directory matches test cwd; PIPESTATUS valid on ubuntu bash).

Non-blocking note: `tests/test_readonly_tree.py:46` has `@unittest.skipIf(os.geteuid() == 0, ...)` —
under a root/container runner it skips and step 4's `! grep -E skipped` gate would fail. Fine on
default ubuntu-latest (non-root); the plan's maintenance note (`004:238-240`) anticipates the class
of issue but frames it as future. Worth an executor heads-up or an allowlist for that one skip.

## 005 — version-floors-and-ci-pinning

Verdict: APPROVE

All load-bearing citations accurate at HEAD: `ast.unparse` at `co_py/fixer.py:32` and
`co_py/analyzer.py:431`; `main()`/argparse at `captain_obvious_py.py` HEAD:62/78, entry HEAD:219-220;
`hooks/prevent.py` fail-open wrapper HEAD:135-140; `captain_obvious_ts.mjs:77-80` block byte-exact;
`projectDir` in scope (`:44`); `tests/test_version_floor.py` absent (clean create); checkout SHA
`11bd719...` consistent between prose and YAML (network-unverified in this environment — executor
should reconfirm via the plan's own `gh api` command).

Cosmetic mismatches (no behavior impact):
- Plan says `tests.yml` is "17 lines"; the file is 16 content lines + trailing newline.
- Plan cites `README.md:74-95` as the whole "Install & use" section; the section actually runs
  `:74-105` (next `##` at `:107`). Relatedly, Step 5 says "end of the section" but parenthesizes
  "(after the code block ending at line 95)" — following the parenthetical inserts the note
  mid-section, before the trust-boundary blockquote (`:102-105`). Grep-based verify passes either
  way; recommend retargeting to "after line 105".

## 006 — ts-laundering-transitivity

Verdict: APPROVE

Core claim verified from source: TS `callLaunders` (`co_ts/laundering.mjs:17-24`) walks only the
direct callee's return statements — no recursion into return-position callees — while Python
`propagate_laundering` (`co_py/mypy_pass.py:89-124`) is a transitive fixpoint; `detectors.md:64-69`
corroborates the asymmetry in prose. The one-level-indirection defeat is real, and the plan's fix
direction (depth-capped recursion that fails toward "assume laundering" → advisory → fewer
deletions) is the safe direction on an auto-delete gate. Excerpts byte-match (`laundering.mjs` is
41 lines as claimed; walk block `:17-24`; `subjectLaunders` `:27-41`, backward-compatible 3-arg call
at `:40`). Verify/decision gate present with three coherent branches, matching the README's "may
legitimately end REJECTED" note. Step-3 rewrite passes `node --check`.

Only nit: gate consumer cited as "near line 360" — actual `analyzer.mjs:361`; the plan instructs
grepping for it, so self-correcting.

## 007 — determinism-and-parity

Verdict: APPROVE

All citations exact: `co_py/discovery.py:25` returns `sorted(out)` vs `co_ts/discovery.mjs:21`
returning raw `found` (walk `:7-22`); `seenGlobal` in `co_ts/duplicates.mjs:6,34,35,47`; snippet
field `co_ts/analyzer.mjs:67` (`toReportFinding` `:61-70`); `co_py/models.py:11-20` omits snippet;
`detectors.md:3-4` shape sentence. All nine parity-corpus categories confirmed present in both
engines with the exact (category, level) pairs the plan asserts (verified at `analyzer.py:429-460/:167`,
`classifier.mjs:35/45/57`, `duplicates.py:62`, `duplicates.mjs:24`, `analyzer.mjs:316`).

Two executor gotchas, both already de-risked by the plan's referenced templates:
- Duplicate-test thresholds differ per engine — TS `bodyKey.length < 8` (`duplicates.mjs:15`) vs
  Python `len(body_dump) < 60` (`duplicates.py:48`); the fixture must clear both (the
  `tests/test_fix_plan.py:23-33` template does).
- `analyzer.py:459` suppresses self-compare when the test name matches
  `stable|determin|consistent|idempotent|same|pure` — don't rename the fixture into those words.

## 008 — real-mypy-e2e-test

Verdict: APPROVE

All citations exact: `REVEAL_RE` byte-identical at `co_py/mypy_pass.py:13`; mypy resolution order
`:164-170`; flag set `:196-200`; isinstance-int probe resolution `:300-303` incl. reason string;
flat-layout `laundering_visible` logic `:174-189` with demotion guard `:308-311`; shadow cleanup
(`SHADOW_PREFIX` `co_py/discovery.py:11`, `finally`-remove `mypy_pass.py:253-256`); `mypyNote` is the
real report key (HEAD `captain_obvious_py.py:174`; read by `tests/test_mypy_degradation.py:65,85`);
CI currently installs no mypy. Fixture logic traced end-to-end — all four Step-1 assertions
achievable. Step-2 insertion point unambiguous (`tests.yml:15-16`, indent matching `:13-14`).
Dependency framing ("after 004/005, shared tests.yml") agrees with README's soft-ordering note.

Cosmetic only: two fake-mypy citations sit a few lines high of the actual heredocs
(`test_flat_layout_laundering.py:44` ~vs~ actual 47-65 region); post-edit `rg -n "mypy" tests.yml`
returns 2 lines (step name + run) though it is genuinely one install step.

## 009 — unittest-assert-methods

Verdict: NEEDS-CHANGES

Citations and behavioral model are fully correct (recognition excerpt verbatim at
`co_py/analyzer.py:130-135`; triage loop `:258-281` with the `:280` comment exact;
`classify_assert` `:420-540`; `want()` gating at `co_py/fixer.py:57-58/:62-64/:95`;
`live_assert_count` invariant `fixer.py:69-70`; all six Step-3 fixture traces verified against
`ast_utils.py:27-39` and `analyzer.py:463-467`). Dependency on 001 agrees with README.

Required changes:
1. Step 2 ships two contradictory implementations. The first-shown code block (plan `009:201-212`)
   passes the real `probes` list and treats `QUEUED` as pass-through; the later bolded Decision
   (`009:244-255`) overrides it — throwaway `[]`, `QUEUED` → `nonredundant`. An executor following
   the first block lets `assertIsInstance`/`assertIsNotNone` queue a real probe; when
   `resolve_probes` resolves it, the loop forces `deletable="report-only"` only on the finding arm,
   not the QUEUED arm, so a resolved type-guaranteed finding keeps the resolver's
   `deletable="safe"` — and `--fix` would auto-delete an `Expr(Call)` the fixer has no safe-removal
   support for. That violates the plan's own hard report-only constraint (`009:37-42`) and STOP
   condition (`009:336`). Fix: delete the superseded block; present one reconciled loop
   (`probes=[]`, QUEUED→nonredundant).
2. Done-criterion at `009:325` is unsatisfiable: it greps
   `rg "report-only.*auto-fix not yet supported" analyzer.py`, but the plan's own code
   (`009:209-210`) writes `deletable="report-only"` and the `auto-fix not yet supported` reason on
   two separate lines — line-oriented rg returns 0 even after a correct implementation. Reword to
   `rg "auto-fix not yet supported"` (or fold onto one line).

Cosmetic: `009:231-243` contains two stream-of-consciousness "STOP:" false starts before the
Decision; fake-mypy pattern cited `:40-60`, actual heredoc `:47-65`.

## 010 — plain-js-test-files

Verdict: APPROVE

All citations exact: `TEST_RE` `co_ts/discovery.mjs:5`; `__tests__` arm `:15-16`; `TS_TEST_RE`
`hooks/prevent.py:33` (at HEAD); ScriptKind `captain_obvious_ts.mjs:89`; program roots + `noEmit`
`:134-137`; checkJs/allowJs sourcing `:124-129`. The load-bearing gate holds:
`co_ts/classifier.mjs:90` (`if (!typesAvailable) return null;`) sits above the type-guaranteed
family (`:92-101`) and below every syntactic check (`:29-88`), so passing `fileTypes=false` for JS
disables type-guaranteed while keeping syntactic categories — exactly the Hard-constraint/STOP
behavior. Step-1 verify one-liner run live: current `false false true false`; plan's expected
`true true true false` is the correct post-change output. No existing test pins `.js` exclusion —
widening is purely additive.

Non-blocking tension the author should reconcile: the Step-2.3 gate
`typesAvailable && (!isJs || options.checkJs === true)` (plan `010:152`) actively *enables*
type-guaranteed on JS when `checkJs: true` — consistent with the Hard constraint (`010:40-44`) and
safe, but contradicting the Out-of-scope wording (`010:107-108`, "only gates (disables) types for
JS") and the Maintenance note (`010:246-248`) that defers checkJs honoring; that branch also ships
untested (case 2 covers only the disable path). Either tighten to `typesAvailable && !isJs` or
reword the scope/maintenance text.

## 011 — ci-check-gate

Verdict: APPROVE

All citations check out: exit-code inventory verified (`captain_obvious_py.py:216` return 0, `:96`
return 2, single-file `:26-59`; `captain_obvious_ts.mjs` exit-2 at `:35,39,53,73,79`, exit-0 `:109`;
exit 1 genuinely unclaimed on both engines); `--check`/`--base` correctly framed as new — absent
from both parsers today (py argparse `:64-76`, ts `:24-31`); prevent.py dedup keying
`(category,test)` exact at `hooks/prevent.py:107-109`; all three CREATE targets absent; README
anchor "🛡️ Prevention (write-time hook)" at `README.md:107` with zero `--check` occurrences.
Step-1/2 verify commands are self-verifying (runnable only after their own step adds the flags) —
by design. Fail-open direction and exit-code contract internally consistent.

Cosmetic: prevent.py excerpt cited as `:103-109` actually spans `:102-109` (first quoted line at
`:102`); `mypy_pass.py:212-221` reference omits the `skills/captain-obvious/scripts/co_py/` path
segment. Framing note: README says 011 "begins with a verify/decision gate … may legitimately end
REJECTED"; the plan encodes the escape hatch via its Doc-tension scoping section plus three STOP
conditions (`011:262-268`) but reads as a committed build plan with no explicit first-step
decide-or-REJECT gate — minor divergence from the README's wording, not a defect.

## 012 — go-backend-spike

Verdict: APPROVE

Every tree-verifiable claim holds: JSON contract byte-matches `detectors.md:3-8`; academic lineage
citations land at `detectors.md:166,170-173,174-178`; all eleven category names grep-verified in
`scripts/`; Go-ecosystem claims (go/parser, go/types, testify, x/tools) factually correct and the
`if x != want { t.Errorf }` vs conditional-assert collision is a genuine, well-posed design
question. Correctly scoped as feasibility-only: `spike/co_go/` never wired into `skills/`
(`012:93,100`), STOP forbids building the real engine (`012:178`), done-criterion asserts
`git diff --stat -- skills/` stays empty (`012:170`). `go` toolchain absent in this environment
(`which go` → not found) — the plan explicitly makes that a BLOCKED outcome (`012:86,175`), so
acceptable. Zero cross-plan conflict surface.

Cosmetic: `012:50-51` lists a `classifier` module for both engines; classifier exists only in TS
(`co_ts/classifier.mjs`) — `co_py` has none (its modules: analyzer, ast_utils, coverage, discovery,
duplicates, fixer, gitguard, models, mypy_pass). Doesn't affect the spike's reasoning.

---

## Cross-plan conflict check (beyond README's dependency notes)

README's notes cover: 001→002 (`co_py/fixer.py`), 001→009, 007→010 (`co_ts/discovery.mjs`),
004→005→008 (`tests.yml`), and 003-vs-006 fixture-file separation. Building the full touch matrix
from all 12 plans surfaced these additional same-file overlaps — **none is a hard conflict**
(all disjoint regions), but they are not in the README notes:

- `captain_obvious_py.py`: **005** (version floor at top of `main()`) and **011** (argparse flags +
  post-report `--check` block). Disjoint regions; 011's own status line says "conceptually after
  005". Execute in index order.
- `captain_obvious_ts.mjs`: **005** (`:77-80` floor block area), **010** (`:89`, `:136`,
  `:148-151`), **011** (mirror `--check`). Three plans, disjoint regions; index order suffices.
- `hooks/prevent.py`: **005** (version floor) and **010** (`:33` regex). Disjoint.
- `co_py/mypy_pass.py`: **001** (`:145`, `:160`) and **002** (islink guard before the `:157-160`
  shadow write) — adjacent/overlapping region; the README's 001→002 ordering (stated for
  `fixer.py`) also resolves this, but the note names only fixer.py.
- `co_py/analyzer.py`: **001** (`:487-490`) and **009** (canonicalizer near `:130-140` + second
  classify loop near `:258-281`). Disjoint; already sequenced by the 001→009 dependency.
- Doc tables: `README.md` rows/sections touched by 004, 005, 009, 010, 011 and
  `references/detectors.md` lines touched by 004, 007, 009 — all distinct rows/sentences;
  trivially mergeable, index order avoids even line-shift noise.
- New test files: all 13 created test filenames across plans are distinct — no collisions.
- `plans/README.md` status rows: every plan touches its own row (coordination file, by design).

Conclusion: no plan pair conflicts in a way that changes any verdict; executing in the README's
index order resolves every overlap above.

## Summary

| Plan | Verdict |
|------|---------|
| 001 | APPROVE |
| 002 | NEEDS-CHANGES |
| 003 | APPROVE |
| 004 | APPROVE |
| 005 | APPROVE |
| 006 | APPROVE |
| 007 | APPROVE |
| 008 | APPROVE |
| 009 | NEEDS-CHANGES |
| 010 | APPROVE |
| 011 | APPROVE |
| 012 | APPROVE |

Both NEEDS-CHANGES items are in-place plan edits (002: fix Step-4 TS invocation + add TS
bite-check; 009: collapse the contradictory Step-2 implementations + fix the `:325` grep) — neither
invalidates its plan's approach.
