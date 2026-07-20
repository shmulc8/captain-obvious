from __future__ import annotations
import ast

from .models import TestRecord, Finding

def _dangling_edits(rec, removed_nodes, lines):
    removed_ids = {id(n) for n in removed_nodes}
    used_in_removed, used_elsewhere = set(), set()
    for stmt in rec.node.body:
        names = {n.id for n in ast.walk(stmt)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        (used_in_removed if id(stmt) in removed_ids else used_elsewhere).update(names)
    newly_unused = used_in_removed - used_elsewhere
    edits = []
    for stmt in rec.node.body:
        if id(stmt) in removed_ids:
            continue
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            v = stmt.targets[0].id
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            v = stmt.target.id
        else:
            continue
        if v not in newly_unused:
            continue
        rhs = stmt.value
        indent = " " * (len(lines[stmt.lineno - 1]) - len(lines[stmt.lineno - 1].lstrip()))
        if rhs is not None and any(isinstance(n, ast.Call) for n in ast.walk(rhs)):
            try:
                text = indent + " ".join(ast.unparse(rhs).split()) + "\n"
            except Exception:
                continue
            edits.append((stmt.lineno, stmt.end_lineno, text))
        else:
            edits.append((stmt.lineno, stmt.end_lineno, None))
    return edits


def apply_fix(records: list[TestRecord], root: str):
    edits_by_file: dict[str, list[tuple[int, int, str | None]]] = {}
    file_lines: dict[str, list[str]] = {}
    tests_removed, asserts_removed = 0, 0

    def lines_of(f):
        if f not in file_lines:
            file_lines[f] = open(f, encoding="utf-8").read().splitlines(keepends=True)
        return file_lines[f]

    def want(f: Finding) -> bool:
        return f.deletable == "safe"

    for rec in records:
        whole = False
        if rec.is_duplicate and any(f.category == "duplicate-test" and want(f) for f in rec.findings):
            whole = True
        elif any(f.category == "never-asserts" and want(f) for f in rec.findings):
            whole = True
        elif any(f.category in ("no-assert", "skipped-test") and want(f) for f in rec.findings):
            whole = True
        else:
            deletable = [f for f in rec.findings if f.node is not None and want(f)
                         and f.category != "dead-assert"]
            if (rec.live_assert_count > 0 and len(deletable) == rec.live_assert_count
                    and rec.nonredundant == 0 and rec.conditional == 0 and rec.helper_asserts == 0):
                del_nodes = {id(f.node) for f in deletable}

                def _trivial(stmt):
                    if isinstance(stmt, ast.Pass):
                        return True
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                        return True  # docstring / bare literal
                    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                        val = stmt.value
                        return val is None or not any(
                            isinstance(n, ast.Call) for n in ast.walk(val))
                    return False

                whole = all(id(s) in del_nodes or _trivial(s) for s in rec.node.body)

        spans = edits_by_file.setdefault(rec.file, [])
        if whole:
            start = min([d.lineno for d in rec.node.decorator_list] + [rec.node.lineno])
            spans.append((start, rec.node.end_lineno, None))
            tests_removed += 1
        else:
            removed_nodes = []
            for f in rec.findings:
                if f.node is not None and want(f):
                    report_only_asserts = sum(1 for x in rec.findings
                                              if x.node is not None and x.deletable == "report-only")
                    ok_partial = (f.category == "dead-assert" or
                                  rec.nonredundant + rec.helper_asserts + rec.conditional
                                  + report_only_asserts > 0)
                    if ok_partial:
                        spans.append((f.node.lineno, f.node.end_lineno, None))
                        removed_nodes.append(f.node)
                        asserts_removed += 1
            if removed_nodes:
                spans.extend(_dangling_edits(rec, removed_nodes, lines_of(rec.file)))

    files_changed = 0
    for file, spans in edits_by_file.items():
        if not spans:
            continue
        lines = lines_of(file)
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
        files_changed += 1
    return {"testsRemoved": tests_removed, "assertionsRemoved": asserts_removed,
            "filesChanged": files_changed}
