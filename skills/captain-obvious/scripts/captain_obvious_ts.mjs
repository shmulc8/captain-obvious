#!/usr/bin/env node
/**
 * captain-obvious — TypeScript detector
 *
 * Deterministically finds tests (Jest/Vitest style) that can never fail or
 * check nothing, and optionally deletes them.
 *
 * Categories:
 *   type-guaranteed   assertion is proven by the type checker (typeof,
 *                     toBeDefined, not.toBeNull, toBeInstanceOf, toBeTruthy,
 *                     Array.isArray, expect.any/anything, toHaveProperty)
 *   constant-assert   both sides are literals / identical side-effect-free
 *                     expressions (expect(true).toBe(true), expect(x).toBe(x))
 *   no-assert         test contains no assertion at all
 *   mock-echo         test asserts a mock does what it was just stubbed to do
 *   duplicate-test    identical body as an earlier test in the same suite
 *   dead-assert       assertion sits after an unconditional return/throw —
 *                     it never executes
 *   swallowed-assert  assertion sits in a try{} with an empty catch — a
 *                     failure is silently swallowed, the test cannot fail
 *   never-asserts     test has assertions but ALL are dead or swallowed
 *   conditional-assert  assertion sits inside if/loop — may never run
 *                     (reported only, never auto-deleted)
 *
 * Levels:
 *   proven    provably cannot fail — deleted by --fix
 *   advisory  almost certainly useless but not provable — deleted by
 *             --fix --aggressive (except report-only categories)
 *
 * Usage:
 *   node captain_obvious_ts.mjs --project <dir|tsconfig.json>
 *        [--fix] [--aggressive] [--json <out.json>]
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

// ---------------------------------------------------------------- args
const argv = process.argv.slice(2);
const argVal = (f) => { const i = argv.indexOf(f); return i >= 0 ? argv[i + 1] : undefined; };
const projectArg = argVal('--project') ?? '.';
const doFix = argv.includes('--fix');
const aggressive = argv.includes('--aggressive');
const jsonOut = argVal('--json');

const projectPath = path.resolve(projectArg);
const isFile = fs.existsSync(projectPath) && fs.statSync(projectPath).isFile();
const projectDir = isFile ? path.dirname(projectPath) : projectPath;

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

// ---------------------------------------------------------------- discovery
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'build', 'out', 'coverage', '.next', '.turbo']);
const TEST_RE = /\.(test|spec)\.(ts|tsx|mts|cts)$/;

function findTestFiles(root) {
  const found = [];
  (function walk(dir) {
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      if (e.isDirectory()) {
        if (!SKIP_DIRS.has(e.name) && !e.name.startsWith('.')) walk(path.join(dir, e.name));
      } else if (TEST_RE.test(e.name) ||
                 (/\.(ts|tsx)$/.test(e.name) && path.basename(dir) === '__tests__')) {
        found.push(path.join(dir, e.name));
      }
    }
  })(root);
  return found;
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

// ---------------------------------------------------------------- helpers
const TF = ts.TypeFlags;
const BAD_MASK = TF.Any | TF.Unknown | (TF.TypeVariable ?? 0) | (TF.Index ?? 0) |
                 (TF.Conditional ?? 0) | (TF.Substitution ?? 0);

const parts = (t) => (t.isUnion?.() ? t.types : [t]);
const isBadType = (t) =>
  t.isIntersection?.() || parts(t).some(p => p.isIntersection?.() || (p.flags & BAD_MASK));
const norm = (s) => s.replace(/\s+/g, '');

// Literal-preserving canonical key: tokenize and keep each token's raw text, so
// whitespace *inside* string/template/regex literals is significant while
// indentation, line breaks, and comments between tokens are not. Using norm()
// (a blanket whitespace strip) for equality is unsafe — it collapses tests that
// differ only by literal whitespace (e.g. "{{ name }}" vs "{{name}}") into one,
// which at proven level would delete a genuinely distinct test.
function canon(text) {
  const sc = ts.createScanner(ts.ScriptTarget.Latest, /*skipTrivia*/ true,
    ts.LanguageVariant.Standard, text);
  const toks = [];
  let k;
  try {
    while ((k = sc.scan()) !== ts.SyntaxKind.EndOfFileToken) {
      if (k === ts.SyntaxKind.Unknown) return norm(text); // lexer confused → fall back
      toks.push(sc.getTokenText());
    }
  } catch {
    return norm(text);
  }
  return toks.join('');
}

// Two identical-bodied tests with materially different titles usually mean a
// copy-paste bug: the second test's *named* behaviour is silently untested.
// Deleting is still coverage-safe, but the report should flag it loudly.
function nameTokens(title) {
  const words = (title || '').match(/[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])/g) || [];
  return new Set(words.map(w => w.toLowerCase()).filter(w => w && !/^\d+$/.test(w)));
}
function namesDiverge(a, b) {
  const ta = nameTokens(a), tb = nameTokens(b);
  if (!ta.size || !tb.size) return false;
  let inter = 0;
  for (const w of ta) if (tb.has(w)) inter++;
  const union = ta.size + tb.size - inter;
  return inter / union < 0.6;
}

function walk(node, fn) {
  fn(node);
  ts.forEachChild(node, c => walk(c, fn));
}

/** `as` casts (except `as const`), `<T>` assertions, and `!` make types lies. */
function hasUnsafeCast(node) {
  let found = false;
  walk(node, n => {
    if (found) return;
    if (ts.isAsExpression(n)) {
      const asConst = ts.isTypeReferenceNode(n.type) && n.type.typeName.getText() === 'const';
      if (!asConst) found = true;
    } else if (n.kind === ts.SyntaxKind.TypeAssertionExpression || ts.isNonNullExpression(n)) {
      found = true;
    }
  });
  return found;
}

/** catch clause that swallows failures: empty, or only console.* noise */
function isSilentCatch(catchClause) {
  return catchClause.block.statements.every(s =>
    ts.isExpressionStatement(s) && ts.isCallExpression(s.expression) &&
    rootIdentifier(s.expression.expression) === 'console');
}

function hasElementAccess(node) {
  let found = false;
  walk(node, n => { if (ts.isElementAccessExpression(n)) found = true; });
  return found;
}

function rootIdentifier(expr) {
  let n = expr;
  while (true) {
    if (ts.isCallExpression(n) || ts.isPropertyAccessExpression(n) ||
        ts.isElementAccessExpression(n) || ts.isNonNullExpression(n)) { n = n.expression; continue; }
    if (ts.isAwaitExpression(n) || ts.isParenthesizedExpression(n)) { n = n.expression; continue; }
    return ts.isIdentifier(n) ? n.text : null;
  }
}

const isLiteralish = (n) =>
  ts.isStringLiteralLike(n) || ts.isNumericLiteral(n) ||
  n.kind === ts.SyntaxKind.TrueKeyword || n.kind === ts.SyntaxKind.FalseKeyword ||
  n.kind === ts.SyntaxKind.NullKeyword ||
  (ts.isPrefixUnaryExpression(n) && n.operator === ts.SyntaxKind.MinusToken && ts.isNumericLiteral(n.operand)) ||
  (ts.isIdentifier(n) && n.text === 'undefined');

/** literal value for comparison, or symbol NO_VALUE */
const NO_VALUE = Symbol('no');
function literalValue(n) {
  if (ts.isStringLiteralLike(n)) return n.text;
  if (ts.isNumericLiteral(n)) return Number(n.text);
  if (n.kind === ts.SyntaxKind.TrueKeyword) return true;
  if (n.kind === ts.SyntaxKind.FalseKeyword) return false;
  if (n.kind === ts.SyntaxKind.NullKeyword) return null;
  if (ts.isIdentifier(n) && n.text === 'undefined') return undefined;
  if (ts.isPrefixUnaryExpression(n) && n.operator === ts.SyntaxKind.MinusToken && ts.isNumericLiteral(n.operand)) {
    return -Number(n.operand.text);
  }
  return NO_VALUE;
}

/** identifier / dotted property chain only — no calls, no indexing */
function isSimpleChain(n) {
  if (ts.isIdentifier(n) || n.kind === ts.SyntaxKind.ThisKeyword) return true;
  if (ts.isPropertyAccessExpression(n)) return isSimpleChain(n.expression);
  return false;
}

// -------------------------------------------------------- type predicates
const TYPEOF_CHECKS = {
  string:  t => !!(t.flags & TF.StringLike),
  number:  t => !!(t.flags & TF.NumberLike),
  boolean: t => !!(t.flags & TF.BooleanLike),
  bigint:  t => !!(t.flags & TF.BigIntLike),
  symbol:  t => !!(t.flags & TF.ESSymbolLike),
  undefined: t => !!(t.flags & TF.Undefined),
  function: t => !!(t.flags & TF.Object) &&
    (t.getCallSignatures().length > 0 || t.getConstructSignatures().length > 0),
};

function typeExcludes(type, flags) {
  return !parts(type).some(p => p.flags & flags);
}

function isAlwaysTruthy(type) {
  return parts(type).every(p => {
    if (p.flags & (TF.Null | TF.Undefined | TF.Void)) return false;
    if (p.isStringLiteral?.()) return p.value !== '';
    if (p.isNumberLiteral?.()) return p.value !== 0 && !Number.isNaN(p.value);
    if (p.flags & TF.BooleanLiteral) return checker.typeToString(p) === 'true';
    if (p.flags & TF.Object) return true;            // objects & functions
    return false;                                     // string/number/boolean/bigint wide types can be falsy
  });
}

function resolveSymbol(sym) {
  if (sym && (sym.flags & ts.SymbolFlags.Alias)) {
    try { return checker.getAliasedSymbol(sym); } catch { return sym; }
  }
  return sym;
}

function classDeclOf(sym) {
  if (!sym) return null;
  if (sym.valueDeclaration && ts.isClassLike(sym.valueDeclaration)) return sym.valueDeclaration;
  return (sym.declarations ?? []).find(d => ts.isClassLike(d)) ?? null;
}

function classChainIncludes(startSym, targetSym) {
  let sym = resolveSymbol(startSym), guard = 0;
  while (sym && guard++ < 50) {
    if (sym === targetSym) return true;
    const decl = classDeclOf(sym);
    if (!decl) return false;
    const ext = (decl.heritageClauses ?? []).find(h => h.token === ts.SyntaxKind.ExtendsKeyword);
    if (!ext || !ext.types[0]) return false;
    sym = resolveSymbol(checker.getTypeAtLocation(ext.types[0].expression)?.getSymbol());
  }
  return false;
}

/** structural forgery impossible iff some class in the chain has a private/protected/#member */
function chainHasPrivateMember(startSym) {
  let sym = resolveSymbol(startSym), guard = 0;
  while (sym && guard++ < 50) {
    const decl = classDeclOf(sym);
    if (!decl) return false;
    for (const m of decl.members ?? []) {
      if (m.name && ts.isPrivateIdentifier(m.name)) return true;
      const mods = ts.canHaveModifiers?.(m) ? ts.getModifiers(m) ?? [] : (m.modifiers ?? []);
      if (mods.some(x => x.kind === ts.SyntaxKind.PrivateKeyword || x.kind === ts.SyntaxKind.ProtectedKeyword)) return true;
    }
    const ext = (decl.heritageClauses ?? []).find(h => h.token === ts.SyntaxKind.ExtendsKeyword);
    if (!ext || !ext.types[0]) return false;
    sym = resolveSymbol(checker.getTypeAtLocation(ext.types[0].expression)?.getSymbol());
  }
  return false;
}

/** property reached through an index signature isn't a declared property — the
 *  type says T but the runtime object may simply lack the key. Not a guarantee. */
function isDeclaredPropertyAccess(subject) {
  if (!ts.isPropertyAccessExpression(subject)) return true;
  const objType = checker.getTypeAtLocation(subject.expression);
  return parts(objType).every(p => checker.getPropertyOfType(p, subject.name.text));
}

function instanceOfVerdict(subject, classArg) {
  const t = checker.getTypeAtLocation(subject);
  if (isBadType(t) || hasUnsafeCast(subject)) return null;
  const classSym = resolveSymbol(checker.getSymbolAtLocation(classArg));
  if (!classDeclOf(classSym)) return null;
  for (const p of parts(t)) {
    if (p.flags & (TF.Null | TF.Undefined)) return null;
    if (!classChainIncludes(p.getSymbol?.(), classSym)) return null;
  }
  const nominal = parts(t).every(p => chainHasPrivateMember(p.getSymbol?.()));
  return nominal
    ? { level: 'proven', reason: `type is ${checker.typeToString(t)} (nominal — has private members), instanceof cannot fail` }
    : { level: 'advisory', reason: `declared type is ${checker.typeToString(t)}, but TS is structural — a shaped non-instance could sneak in` };
}

function isArrayType(t) {
  if (typeof checker.isArrayLikeType === 'function') return checker.isArrayLikeType(t);
  const s = checker.typeToString(t);
  return /\[\]$/.test(s) || /^(Readonly)?Array</.test(s) || /^readonly .*\[\]$/.test(s);
}

// ------------------------------------------------------ annotation laundering
/** A project function with an EXPLICIT return annotation whose body returns a
 *  value typed `any` — the annotation is a promise, not a check. Assertions on
 *  its result are real coverage, not redundancy. (d.ts/lib functions have no
 *  body here and stay trusted.) */
function callLaunders(callExpr) {
  let sym = checker.getSymbolAtLocation(callExpr.expression);
  sym = resolveSymbol(sym);
  const decl = (sym?.declarations ?? []).find(d =>
    (ts.isFunctionDeclaration(d) || ts.isMethodDeclaration(d) || ts.isArrowFunction(d) ||
     ts.isFunctionExpression(d)) && d.body) ??
    (sym?.valueDeclaration && ts.isVariableDeclaration(sym.valueDeclaration) &&
     sym.valueDeclaration.initializer &&
     (ts.isArrowFunction(sym.valueDeclaration.initializer) ||
      ts.isFunctionExpression(sym.valueDeclaration.initializer))
      ? sym.valueDeclaration.initializer : null);
  if (!decl || !decl.body || !decl.type) return false;   // no annotation → inferred type can't lie
  let launders = false;
  walk(decl.body, n => {
    if (launders) return;
    if (ts.isReturnStatement(n) && n.expression) {
      const t = checker.getTypeAtLocation(n.expression);
      if ((t.flags & TF.Any) || hasUnsafeCast(n.expression)) launders = true;
    }
  });
  return launders;
}

/** does the subject's value come (directly or via one const binding) from a
 *  laundering function? */
function subjectLaunders(subject, statements) {
  const calls = [];
  walk(subject, n => { if (ts.isCallExpression(n)) calls.push(n); });
  const names = new Set();
  walk(subject, n => { if (ts.isIdentifier(n)) names.add(n.text); });
  for (const s of statements) {
    if (!ts.isVariableStatement(s)) continue;
    for (const d of s.declarationList.declarations) {
      if (ts.isIdentifier(d.name) && names.has(d.name.text) && d.initializer) {
        walk(d.initializer, n => { if (ts.isCallExpression(n)) calls.push(n); });
      }
    }
  }
  return calls.some(callLaunders);
}

// ------------------------------------------------------ expect-chain parse
/** Returns {subject, matcher, args, negated} | {unsupported:true} | null */
function parseExpectation(expr) {
  let call = expr;
  if (ts.isAwaitExpression(call)) call = call.expression;
  if (!ts.isCallExpression(call) || !ts.isPropertyAccessExpression(call.expression)) return null;
  const matcher = call.expression.name.text;
  let base = call.expression.expression;
  let negated = false;
  if (ts.isPropertyAccessExpression(base) && base.name.text === 'not') { negated = true; base = base.expression; }
  if (ts.isPropertyAccessExpression(base) && ['resolves', 'rejects'].includes(base.name.text)) return { unsupported: true };
  if (!ts.isCallExpression(base)) return null;
  const be = base.expression;
  const isExpect = (ts.isIdentifier(be) && be.text === 'expect') ||
    (ts.isPropertyAccessExpression(be) && ts.isIdentifier(be.expression) &&
     be.expression.text === 'expect' && be.name.text === 'soft');
  if (!isExpect || base.arguments.length !== 1) return null;
  return { subject: base.arguments[0], matcher, args: [...call.arguments], negated };
}

const EQ_MATCHERS = new Set(['toBe', 'toEqual', 'toStrictEqual']);

/** expect.any(X) / expect.anything() */
function parseExpectHelper(arg) {
  if (!ts.isCallExpression(arg) || !ts.isPropertyAccessExpression(arg.expression)) return null;
  const pa = arg.expression;
  if (!ts.isIdentifier(pa.expression) || pa.expression.text !== 'expect') return null;
  if (pa.name.text === 'anything') return { kind: 'anything' };
  if (pa.name.text === 'any' && arg.arguments.length === 1) return { kind: 'any', ctor: arg.arguments[0] };
  return null;
}

// ------------------------------------------------------------ classification
function classifyExpect(exp, constMap = new Map()) {
  const { subject, matcher, args, negated } = exp;
  const arg0 = args[0];

  // ---- boundary-tautology: a .length/.size can never be negative
  if (!negated && ts.isPropertyAccessExpression(subject) &&
      ['length', 'size'].includes(subject.name.text) && arg0 && isLiteralish(arg0)) {
    const v = literalValue(arg0);
    if ((matcher === 'toBeGreaterThanOrEqual' && v === 0) ||
        (matcher === 'toBeGreaterThan' && v === -1)) {
      return { category: 'boundary-tautology', level: 'proven', deletable: 'safe',
               reason: `${subject.getText()} can never be negative — the comparison always holds` };
    }
  }

  // ---- local-const-echo: const x = LIT; ... expect(x).toBe(LIT)
  if (!negated && EQ_MATCHERS.has(matcher) && arg0 &&
      ts.isIdentifier(subject) && constMap.has(subject.text) && isLiteralish(arg0)) {
    const bound = constMap.get(subject.text), asserted = literalValue(arg0);
    if (bound !== NO_VALUE && asserted !== NO_VALUE && bound === asserted) {
      return { category: 'local-const-echo', level: 'proven', deletable: 'safe',
               reason: `${subject.text} is a const bound to ${JSON.stringify(bound)} two lines up — the test asserts its own arrangement` };
    }
  }

  // ---- self-compare-call: expect(f(a)).toEqual(f(a)) — equal by construction.
  // Skipped when the test is deliberately checking determinism (its name says so).
  if (!negated && EQ_MATCHERS.has(matcher) && arg0 && !isSimpleChain(subject) &&
      canon(subject.getText()) === canon(arg0.getText()) &&
      !/stable|determin|consistent|idempotent|same|pure/i.test(exp.testTitle ?? '')) {
    return { category: 'self-compare-call', level: 'advisory', deletable: 'report-only',
             reason: `compares ${subject.getText().slice(0, 60)} to an identical expression — equal by construction unless nondeterministic` };
  }

  // ---- smoke-only: not.toThrow() — the bare call would fail the test anyway
  if (negated && (matcher === 'toThrow' || matcher === 'toThrowError')) {
    return { category: 'smoke-only', level: 'advisory', deletable: 'report-only',
             reason: 'not.toThrow() adds nothing — calling the function directly fails the test on throw anyway; assert on the return value instead' };
  }

  // ---- constant-assert (no type info needed)
  if (!negated && EQ_MATCHERS.has(matcher) && arg0) {
    if (isLiteralish(subject) && isLiteralish(arg0)) {
      const a = literalValue(subject), b = literalValue(arg0);
      if (a !== NO_VALUE && b !== NO_VALUE && a === b) {
        return { category: 'constant-assert', level: 'proven', deletable: 'safe',
                 reason: `compares constant ${JSON.stringify(a)} to itself` };
      }
      return null;
    }
    if (isSimpleChain(subject) && canon(subject.getText()) === canon(arg0.getText())) {
      return { category: 'constant-assert', level: 'proven', deletable: 'safe',
               reason: `compares ${subject.getText()} to itself` };
    }
  }
  if (!negated && matcher === 'toBeTruthy' && args.length === 0 && isLiteralish(subject)) {
    const v = literalValue(subject);
    if (v !== NO_VALUE && !!v) {
      return { category: 'constant-assert', level: 'proven', deletable: 'safe',
               reason: `literal ${JSON.stringify(v)} is always truthy` };
    }
  }

  if (!typesAvailable) return null;

  // ---- type-guaranteed family
  // If the checked value is produced by an inline call (`expect(fetch()).toBeDefined()`,
  // `expect(typeof build()).toBe(...)`), deleting the assertion would also delete
  // that call's execution — real smoke coverage. Such findings stay advisory
  // rather than auto-deletable; a value bound to a variable beforehand is unaffected.
  let subjectHasCall = false;
  if (subject) walk(subject, (n) => {
    if (ts.isCallExpression(n) || ts.isNewExpression(n)) subjectHasCall = true;
  });
  const proven = (reason) => subjectHasCall
    ? { category: 'type-guaranteed', level: 'advisory', deletable: 'report-only',
        reason: reason + ' — subject inlines a call, kept advisory to preserve smoke coverage' }
    : { category: 'type-guaranteed', level: 'proven', deletable: 'safe', reason };
  const advisory = (reason) => ({ category: 'type-guaranteed', level: 'advisory', deletable: 'aggressive', reason });

  // negated typeof: expect(typeof x).not.toBe('undefined') on a type that excludes undefined
  if (negated && ts.isTypeOfExpression(subject) && EQ_MATCHERS.has(matcher) &&
      arg0 && ts.isStringLiteralLike(arg0) && arg0.text === 'undefined' && strictNull) {
    const operand = subject.expression;
    if (!hasUnsafeCast(operand)) {
      const t = checker.getTypeAtLocation(operand);
      if (!isBadType(t) && typeExcludes(t, TF.Undefined | TF.Void) &&
          !(hasElementAccess(operand) && !uncheckedIndex) && isDeclaredPropertyAccess(operand)) {
        return proven(`type ${checker.typeToString(t)} already excludes undefined — typeof can never be "undefined"`);
      }
    }
    return null;
  }

  // typeof x / toBeTypeOf
  const typeofTarget =
    (!negated && ts.isTypeOfExpression(subject) && EQ_MATCHERS.has(matcher) &&
     arg0 && ts.isStringLiteralLike(arg0)) ? { operand: subject.expression, name: arg0.text }
    : (!negated && matcher === 'toBeTypeOf' && arg0 && ts.isStringLiteralLike(arg0))
      ? { operand: subject, name: arg0.text } : null;
  if (typeofTarget) {
    const check = TYPEOF_CHECKS[typeofTarget.name];
    if (!check || hasUnsafeCast(typeofTarget.operand)) return null;
    const t = checker.getTypeAtLocation(typeofTarget.operand);
    if (!isBadType(t) && parts(t).every(check)) {
      return proven(`compiler already guarantees typeof is "${typeofTarget.name}" (type: ${checker.typeToString(t)})`);
    }
    return null;
  }

  if (hasUnsafeCast(subject)) return null;
  const t = () => checker.getTypeAtLocation(subject);

  // toBeDefined / not.toBeUndefined / not.toBeNull / toBeNull / toBeUndefined
  if ((matcher === 'toBeDefined' && !negated) || (matcher === 'toBeUndefined' && negated)) {
    if (!strictNull) return null;
    const ty = t();
    if (!isBadType(ty) && typeExcludes(ty, TF.Undefined | TF.Void)) {
      if (hasElementAccess(subject) && !uncheckedIndex) {
        return advisory(`type ${checker.typeToString(ty)} excludes undefined, but indexed access without noUncheckedIndexedAccess can lie`);
      }
      if (!isDeclaredPropertyAccess(subject)) {
        return advisory(`type ${checker.typeToString(ty)} excludes undefined, but the property comes from an index signature — the type is a promise, not a check`);
      }
      return proven(`type ${checker.typeToString(ty)} already excludes undefined`);
    }
    return null;
  }
  if (matcher === 'toBeNull' && negated) {
    if (!strictNull) return null;
    const ty = t();
    if (!isBadType(ty) && typeExcludes(ty, TF.Null)) {
      return proven(`type ${checker.typeToString(ty)} already excludes null`);
    }
    return null;
  }

  // toBeTruthy / not.toBeFalsy
  if ((matcher === 'toBeTruthy' && !negated) || (matcher === 'toBeFalsy' && negated)) {
    if (!strictNull) return null;
    const ty = t();
    if (!isBadType(ty) && isAlwaysTruthy(ty)) {
      if ((hasElementAccess(subject) && !uncheckedIndex) || !isDeclaredPropertyAccess(subject)) {
        return advisory(`type ${checker.typeToString(ty)} is always truthy, but the value comes through an index signature or unchecked indexed access — the type may lie`);
      }
      return proven(`type ${checker.typeToString(ty)} is always truthy`);
    }
    return null;
  }

  // toBeInstanceOf
  if (matcher === 'toBeInstanceOf' && !negated && arg0) {
    const v = instanceOfVerdict(subject, arg0);
    return v ? { category: 'type-guaranteed', level: v.level,
                 deletable: v.level === 'proven' ? 'safe' : 'aggressive', reason: v.reason } : null;
  }

  // expect(Array.isArray(x)).toBe(true) / toBeTruthy
  if (!negated && ts.isCallExpression(subject) && ts.isPropertyAccessExpression(subject.expression) &&
      subject.expression.name.text === 'isArray' &&
      ts.isIdentifier(subject.expression.expression) && subject.expression.expression.text === 'Array' &&
      subject.arguments.length === 1 &&
      ((EQ_MATCHERS.has(matcher) && arg0?.kind === ts.SyntaxKind.TrueKeyword) ||
       (matcher === 'toBeTruthy' && args.length === 0))) {
    const inner = subject.arguments[0];
    if (hasUnsafeCast(inner)) return null;
    const ty = checker.getTypeAtLocation(inner);
    if (!isBadType(ty) && parts(ty).every(isArrayType)) {
      return proven(`type ${checker.typeToString(ty)} is already an array`);
    }
    return null;
  }

  // toEqual(expect.anything()) / toEqual(expect.any(Ctor))
  if (!negated && EQ_MATCHERS.has(matcher) && arg0) {
    const helper = parseExpectHelper(arg0);
    if (helper?.kind === 'anything') {
      if (!strictNull) return null;
      const ty = t();
      if (!isBadType(ty) && typeExcludes(ty, TF.Null | TF.Undefined | TF.Void)) {
        return proven(`expect.anything() matches everything non-null; type ${checker.typeToString(ty)} is never null/undefined`);
      }
      return null;
    }
    if (helper?.kind === 'any' && ts.isIdentifier(helper.ctor)) {
      const ctorName = helper.ctor.text;
      const prim = { Number: 'number', String: 'string', Boolean: 'boolean', Function: 'function', BigInt: 'bigint', Symbol: 'symbol' }[ctorName];
      const ty = t();
      if (prim) {
        if (!isBadType(ty) && parts(ty).every(TYPEOF_CHECKS[prim])) {
          return proven(`expect.any(${ctorName}) matches any ${prim}; type is ${checker.typeToString(ty)}`);
        }
        return null;
      }
      if (ctorName === 'Object') {
        if (!isBadType(ty) && parts(ty).every(p => p.flags & TF.Object)) {
          return proven(`expect.any(Object) matches any object; type is ${checker.typeToString(ty)}`);
        }
        return null;
      }
      const v = instanceOfVerdict(subject, helper.ctor);
      return v ? { category: 'type-guaranteed', level: v.level,
                   deletable: v.level === 'proven' ? 'safe' : 'aggressive',
                   reason: `expect.any = instanceof check; ${v.reason}` } : null;
    }
  }

  // toHaveProperty('name') with no value argument
  if (!negated && matcher === 'toHaveProperty' && args.length === 1 && arg0 && ts.isStringLiteralLike(arg0) &&
      !arg0.text.includes('.')) {
    const ty = t();
    if (isBadType(ty)) return null;
    const ok = parts(ty).every(p => {
      if (p.flags & (TF.Null | TF.Undefined)) return false;
      const prop = checker.getPropertyOfType(p, arg0.text);
      return prop && !(prop.flags & ts.SymbolFlags.Optional);
    });
    if (ok) return proven(`property "${arg0.text}" is required on type ${checker.typeToString(t())}`);
    return null;
  }

  return null;
}

// ------------------------------------------------------------ mock-echo
function detectMockEcho(statements, expectStmts) {
  const stubs = new Map();   // mockName -> stubbed value text (normalized)
  const directCalls = new Map(); // fnName -> args text (normalized)
  const bindings = new Map();    // varName -> {mock, argsText}
  const findings = new Map();    // stmt -> finding

  for (const stmt of statements) {
    let e = ts.isExpressionStatement(stmt) ? stmt.expression : null;
    if (ts.isVariableStatement(stmt)) {
      for (const d of stmt.declarationList.declarations) {
        if (d.initializer && ts.isIdentifier(d.name)) {
          let init = d.initializer;
          if (ts.isAwaitExpression(init)) init = init.expression;
          if (ts.isCallExpression(init) && ts.isIdentifier(init.expression)) {
            bindings.set(d.name.text, { mock: init.expression.text });
          }
        }
      }
      continue;
    }
    if (!e) continue;
    if (ts.isAwaitExpression(e)) e = e.expression;

    // stub: M.mockReturnValue(V) / mockReturnValueOnce / mockResolvedValue(...)
    if (ts.isCallExpression(e) && ts.isPropertyAccessExpression(e.expression) &&
        /^mock(Return|Resolved)Value(Once)?$/.test(e.expression.name.text) &&
        e.arguments.length === 1) {
      const root = rootIdentifier(e.expression.expression);
      if (root) stubs.set(root, canon(e.arguments[0].getText()));
      continue;
    }

    // direct call of a bare identifier: M(...)
    if (ts.isCallExpression(e) && ts.isIdentifier(e.expression)) {
      directCalls.set(e.expression.text, canon(e.arguments.map(a => a.getText()).join(',')));
      continue;
    }

    // expectation?
    const exp = expectStmts.get(stmt);
    if (!exp || exp.unsupported) continue;
    const { subject, matcher, args, negated } = exp;
    if (negated) continue;

    // pattern A: called the mock yourself, then assert it was called
    if (/^toHaveBeenCalled(Times|With)?$/.test(matcher)) {
      const root = rootIdentifier(subject);
      if (root && directCalls.has(root)) {
        let ok = matcher !== 'toHaveBeenCalledWith' ||
                 canon(args.map(a => a.getText()).join(',')) === directCalls.get(root);
        if (matcher === 'toHaveBeenCalledTimes') ok = args[0]?.getText() === '1';
        if (ok) {
          findings.set(stmt, { category: 'mock-echo', level: 'proven', deletable: 'safe',
            reason: `test calls ${root}() itself, then asserts it was called — asserts the test's own action` });
          continue;
        }
      }
    }

    // pattern B: stub M to return V, then assert M() (or const r = M()) returns V
    if (EQ_MATCHERS.has(matcher) && args[0]) {
      let root = null;
      let s = subject;
      if (ts.isAwaitExpression(s)) s = s.expression;
      if (ts.isCallExpression(s) && ts.isIdentifier(s.expression)) root = s.expression.text;
      else if (ts.isIdentifier(s) && bindings.get(s.text)) root = bindings.get(s.text).mock;
      if (root && stubs.has(root) && stubs.get(root) === canon(args[0].getText())) {
        findings.set(stmt, { category: 'mock-echo', level: 'proven', deletable: 'safe',
          reason: `asserts ${root}() returns the exact value it was stubbed with — tests the mocking library` });
      }
    }
  }
  return findings;
}

// ------------------------------------------------------------ per-test analysis
const ASSERTION_ROOTS = new Set(['expect', 'assert', 'chai', 'sinon', 'expectTypeOf', 'assertType']);
const NEUTRAL_EXPECT_STATIC = new Set(['assertions', 'hasAssertions']);
/** custom helpers like expectAllow(...) / assertValidUser(...) count as assertions —
 *  flagging a test as assertion-free when it asserts via a helper is a false positive */
const isAssertionRoot = (root) =>
  ASSERTION_ROOTS.has(root) || /^(expect|assert|verify|check|should)/i.test(root);

/** expect(x).toBe(y) is TWO calls rooted at `expect` — count only the outermost */
function isOutermostAssertCall(n) {
  let p = n.parent;
  while (p && (ts.isPropertyAccessExpression(p) || ts.isCallExpression(p) ||
               ts.isAwaitExpression(p) || ts.isParenthesizedExpression(p) ||
               ts.isNonNullExpression(p))) {
    if (ts.isCallExpression(p)) {
      const r = rootIdentifier(p.expression);
      if (r && isAssertionRoot(r)) return false;
    }
    p = p.parent;
  }
  return true;
}

function isTestBlock(stmt) {
  if (!ts.isExpressionStatement(stmt) || !ts.isCallExpression(stmt.expression)) return null;
  const c = stmt.expression;
  let name = null, modifier = null;
  if (ts.isIdentifier(c.expression)) name = c.expression.text;
  else if (ts.isPropertyAccessExpression(c.expression) && ts.isIdentifier(c.expression.expression) &&
           ['only', 'skip', 'fails', 'concurrent', 'sequential'].includes(c.expression.name.text)) {
    name = c.expression.expression.text;
    modifier = c.expression.name.text;
  }
  if (!name || !['it', 'test', 'xit', 'fit'].includes(name)) return null;
  const fn = c.arguments.find(a => ts.isArrowFunction(a) || ts.isFunctionExpression(a));
  if (!fn) return null;
  const title = c.arguments[0] && ts.isStringLiteralLike(c.arguments[0]) ? c.arguments[0].text : '<dynamic>';
  const skipped = name === 'xit' || modifier === 'skip';
  return { stmt, title, fn, skipped };
}

function enclosingDescribeKey(node, sf) {
  let n = node.parent;
  while (n) {
    if (ts.isCallExpression(n) && rootIdentifier(n.expression) === 'describe') return `${sf.fileName}#${n.pos}`;
    n = n.parent;
  }
  return `${sf.fileName}#top`;
}

const allFindings = [];
const testRecords = [];

function analyzeTest(sf, { stmt, title, fn, skipped }) {
  const body = fn.body;
  const statements = ts.isBlock(body) ? [...body.statements] : [];
  const line = sf.getLineAndCharacterOfPosition(stmt.getStart(sf)).line + 1;
  const rec = { sf, stmt, title, fn, line, findings: [], expectCount: 0,
                nonRedundantExpects: 0, nestedAssertions: 0, hasAssertionCtl: false };

  // unconditionally-skipped test: it never runs, so it can never fail.
  // Advisory (not proven-deleted) because skips sometimes document future work.
  if (skipped) {
    rec.findings.push({ category: 'skipped-test', level: 'advisory', deletable: 'aggressive',
      reason: 'test is unconditionally skipped (xit / .skip) — it never runs and can never fail', stmtRef: null });
    allFindings.push(toReportFinding(rec, rec.findings[0]));
    testRecords.push(rec);
    return;
  }

  // collect all assertion-ish calls anywhere in the fn (incl. custom helpers)
  const allAssertCalls = [];
  walk(fn, n => {
    if (ts.isCallExpression(n)) {
      const root = rootIdentifier(n.expression);
      if (root && isAssertionRoot(root) && isOutermostAssertCall(n)) allAssertCalls.push(n);
    }
  });

  // reachability: top-level statements after an unconditional return/throw never run
  const unreachableStmts = new Set();
  {
    let dead = false;
    for (const s of statements) {
      if (dead) unreachableStmts.add(s);
      if (ts.isReturnStatement(s) || ts.isThrowStatement(s)) dead = true;
    }
  }
  const topStatementOf = (node) => {
    let n = node;
    while (n && n.parent !== body) n = n.parent;
    return n;
  };

  // swallowed: assertions inside try{} whose catch silently absorbs failures
  const swallowed = new Set();
  walk(fn, n => {
    if (ts.isTryStatement(n) && n.catchClause && isSilentCatch(n.catchClause)) {
      walk(n.tryBlock, m => {
        if (ts.isCallExpression(m)) {
          const root = rootIdentifier(m.expression);
          if (root && isAssertionRoot(root) && isOutermostAssertCall(m)) swallowed.add(m);
        }
      });
    }
  });

  // expect.assertions / expect.hasAssertions control statements
  for (const s of statements) {
    if (ts.isExpressionStatement(s) && ts.isCallExpression(s.expression) &&
        ts.isPropertyAccessExpression(s.expression.expression) &&
        ts.isIdentifier(s.expression.expression.expression) &&
        s.expression.expression.expression.text === 'expect' &&
        NEUTRAL_EXPECT_STATIC.has(s.expression.expression.name.text)) {
      rec.hasAssertionCtl = true;
    }
  }

  // no-assert / never-asserts
  const realAsserts = allAssertCalls.filter(c => {
    if (ts.isPropertyAccessExpression(c.expression) &&
        ts.isIdentifier(c.expression.expression) && c.expression.expression.text === 'expect' &&
        NEUTRAL_EXPECT_STATIC.has(c.expression.name.text)) return false;
    return true;
  });
  const liveAsserts = realAsserts.filter(c =>
    !swallowed.has(c) && !unreachableStmts.has(topStatementOf(c)));

  for (const c of swallowed) {
    if (!realAsserts.includes(c)) continue;
    const cline = sf.getLineAndCharacterOfPosition(c.getStart(sf)).line + 1;
    rec.findings.push({ category: 'swallowed-assert', level: 'proven', deletable: 'report-only',
      reason: `assertion at line ${cline} sits in a try{} with a silent catch — a failure is swallowed, it can never fail the test`, stmtRef: null });
  }

  if (realAsserts.length === 0) {
    rec.findings.push({ category: 'no-assert', level: 'advisory', deletable: 'aggressive',
      reason: 'test contains no assertion — it can only fail if something throws', stmtRef: null });
    for (const f of rec.findings) allFindings.push(toReportFinding(rec, f));
    testRecords.push(rec);
    return;
  }
  if (liveAsserts.length === 0) {
    rec.findings.push({ category: 'never-asserts', level: 'proven', deletable: 'safe',
      reason: 'every assertion in this test is unreachable or swallowed — the test can never fail', stmtRef: null });
    for (const f of rec.findings) allFindings.push(toReportFinding(rec, f));
    testRecords.push(rec);
    return;
  }

  // const LIT bindings for local-const-echo
  const constMap = new Map();
  for (const s of statements) {
    if (ts.isVariableStatement(s) && (s.declarationList.flags & ts.NodeFlags.Const)) {
      for (const d of s.declarationList.declarations) {
        if (ts.isIdentifier(d.name) && d.initializer && isLiteralish(d.initializer)) {
          constMap.set(d.name.text, literalValue(d.initializer));
        }
      }
    }
  }

  // top-level expect statements
  const expectStmts = new Map();
  const topLevelExpectCalls = new Set();
  for (const s of statements) {
    if (!ts.isExpressionStatement(s)) continue;
    // floating-async-assert: expect(p).resolves/.rejects... with no await
    if (!ts.isAwaitExpression(s.expression)) {
      let probe = s.expression;
      let sawResolves = false;
      while (ts.isCallExpression(probe) || ts.isPropertyAccessExpression(probe)) {
        if (ts.isPropertyAccessExpression(probe) &&
            ['resolves', 'rejects'].includes(probe.name.text)) sawResolves = true;
        probe = probe.expression;
      }
      if (sawResolves && ts.isIdentifier(probe) && probe.text === 'expect') {
        const cline = sf.getLineAndCharacterOfPosition(s.getStart(sf)).line + 1;
        rec.findings.push({ category: 'floating-async-assert', level: 'advisory', deletable: 'report-only',
          reason: `async assertion at line ${cline} is not awaited — under Jest it silently never fails the test; ` +
                  `bun:test and modern Vitest do catch it at settle time. Add await either way`, stmtRef: null });
        rec.nonRedundantExpects++;
        continue;
      }
    }
    const exp = parseExpectation(s.expression);
    if (!exp) continue;
    if (unreachableStmts.has(s)) {
      rec.findings.push({ category: 'dead-assert', level: 'proven', deletable: 'safe', stmtRef: s,
        reason: 'sits after an unconditional return/throw — this assertion never executes' });
      continue;
    }
    expectStmts.set(s, exp);
    let e = s.expression;
    if (ts.isAwaitExpression(e)) e = e.expression;
    walk(e, n => {
      if (ts.isCallExpression(n) && rootIdentifier(n.expression) === 'expect') topLevelExpectCalls.add(n);
    });
  }

  // nested/conditional assertions
  for (const c of liveAsserts) {
    if (topLevelExpectCalls.has(c)) continue;
    // is it a direct child chain of a top-level statement? treat any assert call whose
    // statement-ancestor chain crosses if/loop/try/callback as nested
    // if-gated only: loop/callback-wrapped asserts are ordinary test style and
    // flagging them buries the signal; they still block whole-test removal
    let n = c.parent, crossed = null;
    while (n && n !== fn) {
      if (ts.isIfStatement(n) || ts.isIterationStatement?.(n, false) ||
          ts.isTryStatement(n) || ts.isCatchClause(n) ||
          ts.isArrowFunction(n) || ts.isFunctionExpression(n)) { crossed = n; break; }
      n = n.parent;
    }
    if (crossed) {
      rec.nestedAssertions++;
      if (ts.isIfStatement(crossed)) {
        const cline = sf.getLineAndCharacterOfPosition(c.getStart(sf)).line + 1;
        rec.findings.push({ category: 'conditional-assert', level: 'advisory', deletable: 'report-only',
          reason: `assertion at line ${cline} is gated behind an if — it may never execute (rotten green)`,
          stmtRef: null });
      }
    }
  }

  // mock-echo pass (may claim some expect statements)
  const mockFindings = detectMockEcho(statements, expectStmts);

  // classify each top-level expect statement
  for (const [s, exp] of expectStmts) {
    rec.expectCount++;
    if (mockFindings.has(s)) {
      const f = { ...mockFindings.get(s), stmtRef: s };
      rec.findings.push(f);
      continue;
    }
    if (exp.unsupported) { rec.nonRedundantExpects++; continue; }
    exp.testTitle = rec.title;
    let verdict = classifyExpect(exp, constMap);
    if (verdict && verdict.category === 'type-guaranteed' &&
        subjectLaunders(exp.subject, statements)) {
      verdict = null;  // annotation isn't runtime-enforced — the assertion is real coverage
    }
    if (verdict) rec.findings.push({ ...verdict, stmtRef: s });
    else rec.nonRedundantExpects++;
  }

  for (const f of rec.findings) allFindings.push(toReportFinding(rec, f));
  testRecords.push(rec);
}

function toReportFinding(rec, f) {
  return {
    file: path.relative(projectDir, rec.sf.fileName),
    line: f.stmtRef ? rec.sf.getLineAndCharacterOfPosition(f.stmtRef.getStart(rec.sf)).line + 1 : rec.line,
    test: rec.title,
    category: f.category, level: f.level, deletable: f.deletable,
    snippet: f.stmtRef ? f.stmtRef.getText(rec.sf).slice(0, 160) : undefined,
    reason: f.reason,
  };
}

// ------------------------------------------------------------ run analysis
for (const file of testFiles) {
  const sf = program.getSourceFile(path.resolve(file));
  if (!sf) continue;
  walk(sf, n => {
    const t = ts.isExpressionStatement(n) ? isTestBlock(n) : null;
    if (t) analyzeTest(sf, t);
  });
}

// duplicate-test pass (same describe scope, ignore snapshot tests)
{
  const seen = new Map();
  const seenGlobal = new Map();
  for (const rec of testRecords) {
    const bodyText = ts.isBlock(rec.fn.body)
      ? printer.printNode(ts.EmitHint.Unspecified, rec.fn.body, rec.sf)
      : rec.fn.body.getText(rec.sf);
    if (/MatchSnapshot|MatchInlineSnapshot/.test(bodyText)) continue;
    const bodyKey = canon(bodyText);
    if (bodyKey.length < 8) continue;
    const key = enclosingDescribeKey(rec.stmt, rec.sf) + '::' + bodyKey;
    if (seen.has(key)) {
      const first = seen.get(key);
      let reason = `body is identical to "${first.title}" (line ${first.line}) in the same suite`;
      if (namesDiverge(rec.title, first.title)) {
        reason += " — names differ, so this is likely a copy-paste that leaves this test's " +
          'named behaviour untested; deleting is coverage-safe but consider fixing the body instead';
      }
      const f = { category: 'duplicate-test', level: 'proven', deletable: 'safe',
        reason, stmtRef: null };
      rec.findings.push(f);
      rec.isDuplicate = true;
      allFindings.push(toReportFinding(rec, f));
    } else {
      seen.set(key, rec);
    }

    // cross-scope duplicate (different file or describe): surface but never
    // auto-delete — a shared body can behave differently under different
    // beforeEach/setup, so a human must pick which to keep.
    const gkey = bodyKey;
    if (seenGlobal.has(gkey)) {
      const first = seenGlobal.get(gkey);
      const sameScope = first.sf.fileName === rec.sf.fileName &&
        enclosingDescribeKey(first.stmt, first.sf) === enclosingDescribeKey(rec.stmt, rec.sf);
      if (!rec.isDuplicate && !sameScope) {
        const where = `${first.sf.fileName.split('/').pop()}:${first.line}`;
        const f = { category: 'duplicate-test', level: 'advisory', deletable: 'report-only',
          reason: `body is identical to "${first.title}" (${where}) in a different suite — ` +
            'likely redundant, but beforeEach/setup may differ, so review before removing', stmtRef: null };
        rec.findings.push(f);
        allFindings.push(toReportFinding(rec, f));
      }
    } else {
      seenGlobal.set(gkey, rec);
    }
  }
}

// ------------------------------------------------------------ decide removals
const removableTests = [];
const removableStmts = [];

for (const rec of testRecords) {
  const wants = (f) => f.deletable === 'safe' || (aggressive && f.deletable === 'aggressive');

  if (rec.isDuplicate && wants(rec.findings.find(f => f.category === 'duplicate-test'))) {
    removableTests.push(rec);
    continue;
  }
  const never = rec.findings.find(f => f.category === 'never-asserts');
  if (never) {
    if (wants(never)) removableTests.push(rec);
    continue;
  }
  const wholeTestCategory = rec.findings.find(f => ['no-assert', 'skipped-test'].includes(f.category));
  if (wholeTestCategory) {
    if (wants(wholeTestCategory)) removableTests.push(rec);
    continue;
  }

  const stmtFindings = rec.findings.filter(f => f.stmtRef);
  const deadStmts = stmtFindings.filter(f => f.category === 'dead-assert');
  const liveDeletable = stmtFindings.filter(f => f.category !== 'dead-assert').filter(wants);
  const allRedundant =
    rec.expectCount > 0 &&
    liveDeletable.length === rec.expectCount &&
    rec.nonRedundantExpects === 0 &&
    rec.nestedAssertions === 0;

  // Even when every assertion is redundant, the test may still EXERCISE code
  // under test outside the assertions (a call that could throw = smoke coverage).
  // Only remove the whole test if nothing executable remains once the redundant
  // expect statements are gone; otherwise leave it intact.
  let remainingCall = false;
  if (allRedundant && ts.isBlock(rec.fn.body)) {
    const del = new Set(liveDeletable.map(f => f.stmtRef));
    for (const st of rec.fn.body.statements) {
      if (del.has(st)) continue;
      if (ts.isExpressionStatement(st) && ts.isStringLiteral(st.expression)) continue;
      walk(st, n => {
        if (ts.isCallExpression(n) || ts.isNewExpression(n)) remainingCall = true;
      });
      if (remainingCall) break;
    }
  }
  const wholeTest = allRedundant && !remainingCall;

  if (wholeTest) removableTests.push(rec);
  else {
    // partial line-deletion is safe as long as the test keeps at least one
    // assertion afterwards — non-redundant, nested, or report-only ones all count
    const remainingAsserts = rec.nonRedundantExpects + rec.nestedAssertions +
      stmtFindings.filter(f => f.deletable === 'report-only').length;
    if (liveDeletable.length > 0 && remainingAsserts > 0 && !rec.hasAssertionCtl) {
      removableStmts.push(...liveDeletable.map(f => ({ rec, stmt: f.stmtRef })));
    }
    // unreachable code is always safe to drop, regardless of what else the test does
    removableStmts.push(...deadStmts.map(f => ({ rec, stmt: f.stmtRef })));
  }
}

// ------------------------------------------------------------ report
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
  plan: {
    testsToRemove: removableTests.map(r => ({ file: path.relative(projectDir, r.sf.fileName), line: r.line, test: r.title })),
    assertionsToRemove: removableStmts.length,
    aggressive,
  },
  fixed: null,
};

// ------------------------------------------------------------ fix
if (doFix) {
  const editsByFile = new Map();
  const removedTestPos = new Set();
  for (const rec of removableTests) {
    const arr = editsByFile.get(rec.sf.fileName) ?? [];
    arr.push({ start: rec.stmt.getFullStart(), end: rec.stmt.getEnd() });
    removedTestPos.add(rec.stmt);
    editsByFile.set(rec.sf.fileName, arr);
  }
  for (const { rec, stmt } of removableStmts) {
    if (removedTestPos.has(rec.stmt)) continue;
    const arr = editsByFile.get(rec.sf.fileName) ?? [];
    arr.push({ start: stmt.getFullStart(), end: stmt.getEnd() });
    editsByFile.set(rec.sf.fileName, arr);
  }
  let filesChanged = 0;
  for (const [file, edits] of editsByFile) {
    let text = fs.readFileSync(file, 'utf8');
    edits.sort((a, b) => b.start - a.start);
    for (const e of edits) text = text.slice(0, e.start) + text.slice(e.end);
    fs.writeFileSync(file, text);
    filesChanged++;
  }
  report.fixed = { testsRemoved: removableTests.length, assertionsRemoved: removableStmts.length, filesChanged };
}

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
console.log(`\n  tests fully removable${aggressive ? ' (aggressive)' : ''}: ${removableTests.length}`);
console.log(`  individual assertions removable: ${removableStmts.length}`);
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
