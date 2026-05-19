"""
Layer 2 — AST + Semantic analysis (Python).

For each .py file: parse with stdlib `ast`, then add radon metrics
(cyclomatic complexity + LOC). Across files: build module graph,
identify entry points, surface complexity hotspots, mark functions
that look unused (defined but never referenced elsewhere by name).

Usage:
    from ast_analyzer import run_ast
    report = run_ast("path/to/repo")

CLI:
    python ast_analyzer.py path/to/repo
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from radon.complexity import cc_visit
from radon.raw import analyze as raw_analyze

SKIP_DIRS = {
    ".venv", "venv", "env", "__pycache__", ".git",
    "node_modules", "dist", "build", "out", ".next",
    "target", "vendor", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "skills_cache",
}


def _iter_python_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


# ── Schema ───────────────────────────────────────────────────

@dataclass
class FunctionInfo:
    name: str
    qualname: str            # ClassName.method or module.func
    lineno: int
    end_lineno: int
    args: list[str]
    is_async: bool
    is_method: bool
    has_docstring: bool
    complexity: int          # cyclomatic
    body_lines: int


@dataclass
class ClassInfo:
    name: str
    lineno: int
    bases: list[str]
    methods: list[str]


@dataclass
class FileInfo:
    path: str                # relative
    module: str              # dotted module path
    loc: int                 # total lines
    sloc: int                # source lines (radon)
    comments: int
    imports: list[str]       # module names
    from_imports: list[str]  # "module:symbol"
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    has_main_guard: bool = False
    parse_error: str | None = None


@dataclass
class AstReport:
    root: str
    file_count: int
    files: list[FileInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "file_count": self.file_count,
            "summary": self.summary(),
            "module_graph": self.module_graph(),
            "entry_points": self.entry_points(),
            "complexity_hotspots": [asdict(f) for f in self.complexity_hotspots()],
            "potentially_unused_functions": self.unused_functions(),
            "files": [asdict(f) for f in self.files],
        }

    def summary(self) -> dict:
        total_loc = sum(f.loc for f in self.files)
        total_sloc = sum(f.sloc for f in self.files)
        total_fns = sum(len(f.functions) for f in self.files)
        total_cls = sum(len(f.classes) for f in self.files)
        complexities = [fn.complexity for f in self.files for fn in f.functions]
        avg_cc = round(sum(complexities) / len(complexities), 2) if complexities else 0
        max_cc = max(complexities, default=0)
        return {
            "total_loc": total_loc,
            "total_sloc": total_sloc,
            "total_functions": total_fns,
            "total_classes": total_cls,
            "avg_cyclomatic": avg_cc,
            "max_cyclomatic": max_cc,
            "parse_errors": sum(1 for f in self.files if f.parse_error),
        }

    def module_graph(self) -> dict[str, list[str]]:
        own_modules = {f.module for f in self.files if f.module}
        graph: dict[str, list[str]] = {}
        for f in self.files:
            deps: set[str] = set()
            for imp in f.imports + [x.split(":")[0] for x in f.from_imports]:
                top = imp.split(".")[0]
                if imp in own_modules or top in own_modules:
                    deps.add(imp if imp in own_modules else top)
            graph[f.module] = sorted(deps)
        return graph

    def entry_points(self) -> list[str]:
        eps = []
        for f in self.files:
            if f.has_main_guard:
                eps.append(f.path)
        return sorted(eps)

    def complexity_hotspots(self, min_cc: int = 10) -> list[FunctionInfo]:
        hot = [fn for f in self.files for fn in f.functions if fn.complexity >= min_cc]
        return sorted(hot, key=lambda x: -x.complexity)

    def unused_functions(self) -> list[dict]:
        """Functions defined in the project but whose name never appears
        in any other file's source text. Heuristic, not authoritative —
        misses dynamic dispatch and string-based lookups.
        """
        all_sources: dict[str, str] = {}
        for f in self.files:
            try:
                all_sources[f.path] = Path(self.root, f.path).read_text(
                    encoding="utf-8", errors="ignore"
                )
            except Exception:
                continue
        unused: list[dict] = []
        for f in self.files:
            for fn in f.functions:
                if fn.name.startswith("_") or fn.is_method:
                    continue
                if fn.name in {"main", "__init__"}:
                    continue
                hits = 0
                for path, src in all_sources.items():
                    if path == f.path:
                        # within own file: only count references outside the def line
                        lines = src.splitlines()
                        for i, ln in enumerate(lines, 1):
                            if i == fn.lineno:
                                continue
                            if fn.name in ln:
                                hits += 1
                                break
                    elif fn.name in src:
                        hits += 1
                        break
                if hits == 0:
                    unused.append({
                        "file": f.path,
                        "function": fn.name,
                        "line": fn.lineno,
                    })
        return unused


# ── Per-file analysis ────────────────────────────────────────

def _module_path(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _rel(path: Path, root: Path) -> Path:
    return path.resolve().relative_to(root.resolve())


def _docstring(node) -> bool:
    return bool(ast.get_docstring(node, clean=False))


def _arg_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    out: list[str] = []
    a = func.args
    out.extend(arg.arg for arg in a.posonlyargs)
    out.extend(arg.arg for arg in a.args)
    if a.vararg:
        out.append(f"*{a.vararg.arg}")
    out.extend(arg.arg for arg in a.kwonlyargs)
    if a.kwarg:
        out.append(f"**{a.kwarg.arg}")
    return out


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and any(
                    isinstance(c, ast.Constant) and c.value == "__main__"
                    for c in test.comparators
                )
            ):
                return True
    return False


def analyze_file(path: Path, root: Path) -> FileInfo:
    rel = _rel(path, root)
    rel_str = str(rel).replace("\\", "/")
    module = _module_path(rel)
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return FileInfo(path=rel_str, module=module, loc=0, sloc=0,
                        comments=0, imports=[], from_imports=[],
                        parse_error=f"read: {e}")

    try:
        raw = raw_analyze(src)
        loc, sloc, comments = raw.loc, raw.sloc, raw.comments
    except Exception:
        loc = len(src.splitlines())
        sloc = loc
        comments = 0

    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        return FileInfo(path=rel_str, module=module, loc=loc, sloc=sloc,
                        comments=comments, imports=[], from_imports=[],
                        parse_error=f"syntax: {e.msg} @ line {e.lineno}")

    imports: list[str] = []
    from_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for n in node.names:
                from_imports.append(f"{mod}:{n.name}")

    # Complexity via radon
    try:
        cc_results = {(r.name, r.lineno): r.complexity for r in cc_visit(src)}
    except Exception:
        cc_results = {}

    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []

    def add_function(node, qual_prefix: str, is_method: bool):
        name = node.name
        cc = cc_results.get((name, node.lineno))
        if cc is None and qual_prefix:
            # radon names methods like "Class.method"
            cc = cc_results.get((f"{qual_prefix}.{name}", node.lineno))
        functions.append(FunctionInfo(
            name=name,
            qualname=f"{qual_prefix}.{name}" if qual_prefix else name,
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            args=_arg_names(node),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_method=is_method,
            has_docstring=_docstring(node),
            complexity=cc if cc is not None else 1,
            body_lines=(getattr(node, "end_lineno", node.lineno) or node.lineno)
                        - node.lineno + 1,
        ))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_function(node, "", is_method=False)
        elif isinstance(node, ast.ClassDef):
            method_names: list[str] = []
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add_function(sub, node.name, is_method=True)
                    method_names.append(sub.name)
            classes.append(ClassInfo(
                name=node.name, lineno=node.lineno,
                bases=[ast.unparse(b) for b in node.bases],
                methods=method_names,
            ))

    return FileInfo(
        path=rel_str,
        module=module,
        loc=loc,
        sloc=sloc,
        comments=comments,
        imports=sorted(set(imports)),
        from_imports=sorted(set(from_imports)),
        functions=functions,
        classes=classes,
        has_main_guard=_has_main_guard(tree),
    )


def run_ast(root: str | Path) -> AstReport:
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(root_path)
    py_files = _iter_python_files(root_path)
    report = AstReport(root=str(root_path), file_count=len(py_files))
    for p in py_files:
        report.files.append(analyze_file(p, root_path))
    return report


# ── CLI ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("usage: python ast_analyzer.py <path>")
        sys.exit(2)
    report = run_ast(sys.argv[1])
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
