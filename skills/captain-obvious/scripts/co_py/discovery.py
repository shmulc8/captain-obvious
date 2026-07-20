from __future__ import annotations
import os

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox",
             ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs"}

def find_test_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.startswith("test_") and f.endswith(".py") or f.endswith("_test.py"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)
