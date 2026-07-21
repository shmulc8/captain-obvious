#!/usr/bin/env node
/**
 * captain-obvious — TypeScript detector
 *
 * Deterministically finds tests (Jest/Vitest style) that can never fail or
 * check nothing, and optionally deletes them.
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

import { findTestFiles } from './co_ts/discovery.mjs';
import { walk } from './co_ts/ast_utils.mjs';
import { isTestBlock, analyzeTest, toReportFinding } from './co_ts/analyzer.mjs';
import { loadCoverage } from './co_ts/coverage.mjs';
import { markDuplicates } from './co_ts/duplicates.mjs';
import { decideRemovals } from './co_ts/fixer.mjs';
import { fixBlocker } from './co_ts/gitguard.mjs';

// ---------------------------------------------------------------- args
const argv = process.argv.slice(2);
const argVal = (f) => { const i = argv.indexOf(f); return i >= 0 ? argv[i + 1] : undefined; };
const projectArg = argVal('--project') ?? '.';
const doFix = argv.includes('--fix');
const jsonOut = argVal('--json');
const coverageArg = argVal('--coverage');

const force = argv.includes('--force');

const projectPath = path.resolve(projectArg);
const isFile = fs.existsSync(projectPath) && fs.statSync(projectPath).isFile();
const projectDir = isFile ? path.dirname(projectPath) : projectPath;

if (doFix && !force) {
  const blocker = fixBlocker(projectDir);
  if (blocker) {
    console.error(
      `captain-obvious: refusing to --fix — ${blocker}.\n` +
      '  --fix rewrites test files in place with no backup. Commit or stash\n' +
      '  first so `git checkout -- <files>` can undo it, or pass --force.');
    process.exit(2);
  }
}

// ------------------------------------------------- load project's TypeScript
let ts;
try {
  ts = createRequire(path.join(projectDir, 'package.json'))('typescript');
} catch {
  try { ts = (await import('typescript')).default; } catch {
    console.error(`captain-obvious: cannot resolve "typescript" from ${projectDir}. Install it there (npm i -D typescript).`);
    process.exit(2);
  }
}

const testFiles = findTestFiles(projectDir);
if (testFiles.length === 0) {
  console.error(`captain-obvious: no test files (*.test.ts / *.spec.ts / __tests__) under ${projectDir}`);
  process.exit(0);
}

// ---------------------------------------------------------------- program
const tsconfigPath = isFile ? projectPath
  : ts.findConfigFile(projectDir, ts.sys.fileExists, 'tsconfig.json');

let options = {};
let configFileNames = [];
if (tsconfigPath) {
  const raw = ts.readConfigFile(tsconfigPath, ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(raw.config ?? {}, ts.sys, path.dirname(tsconfigPath));
  options = parsed.options;
  configFileNames = parsed.fileNames;
}
const strictNull = options.strictNullChecks ?? options.strict ?? false;
const uncheckedIndex = options.noUncheckedIndexedAccess ?? false;
const typesAvailable = !!tsconfigPath;

const program = ts.createProgram(
  [...new Set([...configFileNames, ...testFiles.map(f => path.resolve(f))])],
  { ...options, noEmit: true },
);
const checker = program.getTypeChecker();
const printer = ts.createPrinter({ removeComments: true });

const allFindings = [];
const testRecords = [];

// ------------------------------------------------------------ run analysis
for (const file of testFiles) {
  const sf = program.getSourceFile(path.resolve(file));
  if (!sf) continue;
  walk(ts, sf, n => {
    const t = ts.isExpressionStatement(n) ? isTestBlock(ts, n) : null;
    if (t) analyzeTest(ts, checker, typesAvailable, strictNull, uncheckedIndex, sf, t, allFindings, testRecords, projectDir);
  });
}

// duplicate-test pass
markDuplicates(ts, printer, testRecords, allFindings, projectDir);

// ------------------------------------------------------------ coverage confirm
const coverage = coverageArg ? loadCoverage(coverageArg, projectDir) : null;
let coveragePromoted = 0, coverageSuppressed = 0;
let coverageWarning = null;
if (coverage) {
  const kept = [];
  const inertFiles = new Set();
  for (const f of allFindings) {
    if (f.category === 'conditional-assert') {
      if (!coverage.hasFile(f.file)) inertFiles.add(f.file);
      const hits = coverage.hits(f.file, f.line);
      if (hits === 0) {
        f.level = 'proven';
        f.reason += " — coverage confirms it ran 0 times: rotten (ICSE'19). Fix the guard so it fires, or remove it";
        coveragePromoted++;
      } else if (hits > 0) {
        coverageSuppressed++;
        continue;
      }
    }
    kept.push(f);
  }
  allFindings.splice(0, allFindings.length, ...kept);
  if (inertFiles.size) {
    // coverage configs usually collect only src/ — then test-file lines are
    // absent and this whole mode silently confirms nothing
    coverageWarning = `coverage data has no lines for ${inertFiles.size} test file(s) ` +
      `(${[...inertFiles].sort().slice(0, 3).join(', ')}...) — coverage mode is inert for them; ` +
      'include test files in coverage collection (e.g. collectCoverageFrom over the whole repo, not just src/)';
  }
}

// ------------------------------------------------------------ decide removals & plan
const summary = {};
for (const f of allFindings) {
  summary[f.category] = summary[f.category] ?? { proven: 0, advisory: 0 };
  summary[f.category][f.level]++;
}

const report = {
  tool: 'captain-obvious/ts',
  project: projectDir,
  tsconfig: tsconfigPath ?? null,
  typeChecksEnabled: typesAvailable,
  strictNullChecks: strictNull,
  testFilesScanned: testFiles.length,
  testsScanned: testRecords.length,
  findings: allFindings,
  summary,
  coverage: coverage
    ? { file: coverageArg, conditionalAssertsPromoted: coveragePromoted, conditionalAssertsSuppressed: coverageSuppressed, warning: coverageWarning }
    : (coverageArg ? { file: coverageArg, error: 'could not parse coverage (expected lcov / istanbul json / coverage.py json)' } : null),
  plan: null,
  fixed: null,
};

decideRemovals(ts, testRecords, doFix, report, fs, projectDir);

// ------------------------------------------------------------ output
if (jsonOut) fs.writeFileSync(jsonOut, JSON.stringify(report, null, 2));

const pad = (s, n) => String(s).padEnd(n);
console.log(`\ncaptain-obvious (TS) — ${testRecords.length} tests scanned in ${testFiles.length} files`);
if (!typesAvailable) console.log('  (no tsconfig.json found — type-guaranteed checks disabled, syntactic checks only)');
else if (!strictNull) console.log('  (strictNullChecks is OFF — null/undefined-related checks disabled)');
console.log('');
for (const [cat, c] of Object.entries(summary)) {
  console.log(`  ${pad(cat, 20)} proven: ${c.proven}  advisory: ${c.advisory}`);
}
if (report.plan) {
  console.log(`\n  tests fully removable: ${report.plan.testsToRemove.length}`);
  console.log(`  individual assertions removable: ${report.plan.assertionsToRemove}`);
}
if (coverage) {
  console.log(`  coverage: ${coveragePromoted} conditional-assert(s) confirmed rotten, ${coverageSuppressed} confirmed reached (dropped)`);
  if (coverageWarning) console.log(`  coverage warning: ${coverageWarning}`);
} else if (coverageArg) {
  console.log('  coverage: could not parse the coverage file (expected lcov / istanbul json / coverage.py json)');
}
if (allFindings.length) {
  console.log('\nFindings:');
  for (const f of allFindings) {
    console.log(`  [${f.level === 'proven' ? 'PROVEN  ' : 'ADVISORY'}] ${f.file}:${f.line} (${f.category}) "${f.test}"`);
    console.log(`             ${f.reason}`);
  }
}
if (report.fixed) {
  console.log(`\nFixed: removed ${report.fixed.testsRemoved} tests and ${report.fixed.assertionsRemoved} assertions across ${report.fixed.filesChanged} files.`);
  console.log('Re-run your typechecker and test suite now.');
}
