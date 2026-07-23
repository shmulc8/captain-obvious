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
