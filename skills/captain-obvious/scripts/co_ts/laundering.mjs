import { walk, hasUnsafeCast } from './ast_utils.mjs';
import { resolveSymbol } from './type_predicates.mjs';

export function callLaunders(ts, checker, callExpr) {
  let sym = checker.getSymbolAtLocation(callExpr.expression);
  sym = resolveSymbol(ts, checker, sym);
  const decl = (sym?.declarations ?? []).find(d =>
    (ts.isFunctionDeclaration(d) || ts.isMethodDeclaration(d) || ts.isArrowFunction(d) ||
     ts.isFunctionExpression(d)) && d.body) ??
    (sym?.valueDeclaration && ts.isVariableDeclaration(sym.valueDeclaration) &&
     sym.valueDeclaration.initializer &&
     (ts.isArrowFunction(sym.valueDeclaration.initializer) ||
      ts.isFunctionExpression(sym.valueDeclaration.initializer))
      ? sym.valueDeclaration.initializer : null);
  if (!decl || !decl.body || !decl.type) return false;
  let launders = false;
  walk(ts, decl.body, n => {
    if (launders) return;
    if (ts.isReturnStatement(n) && n.expression) {
      const t = checker.getTypeAtLocation(n.expression);
      if ((t.flags & ts.TypeFlags.Any) || hasUnsafeCast(ts, n.expression)) launders = true;
    }
  });
  return launders;
}

export function subjectLaunders(ts, checker, subject, statements) {
  const calls = [];
  walk(ts, subject, n => { if (ts.isCallExpression(n)) calls.push(n); });
  const names = new Set();
  walk(ts, subject, n => { if (ts.isIdentifier(n)) names.add(n.text); });
  for (const s of statements) {
    if (!ts.isVariableStatement(s)) continue;
    for (const d of s.declarationList.declarations) {
      if (ts.isIdentifier(d.name) && names.has(d.name.text) && d.initializer) {
        walk(ts, d.initializer, n => { if (ts.isCallExpression(n)) calls.push(n); });
      }
    }
  }
  return calls.some(c => callLaunders(ts, checker, c));
}
