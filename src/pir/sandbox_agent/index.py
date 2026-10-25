"""`index`: the deterministic static analysis index (ARCHITECTURE section 6.3, ADR-0007).

Standard library only, `ast` only: repository code is parsed, never imported or executed. Every
traversal here is iterative (an explicit stack), so a hostile, deeply nested file that still
parses cannot crash the agent; files `ast.parse` refuses are skipped with a reason.

Output is plain JSON shaped like `pir.models.index.AnalysisIndex`; every list is sorted and no
wall-clock value appears, so the same repo gives byte-identical JSON (AC-14).
"""

import ast
import io
import json
import os
import re
import stat
import tokenize
import warnings
from dataclasses import dataclass, field
from typing import Any

from .fetch import check_sha
from .protocol import PROTOCOL_VERSION, BadInput, log, require_str

REPO_ROOT = "/work/repo"
MAX_PY_FILE_BYTES = 1_000_000
MAX_AST_DEPTH = 1_000  # deeper trees are skipped; the same on every platform
MAX_INDEXED_FILES = 10_000
MAX_IMPORTS = 20_000
MAX_DEFS = 20_000
MAX_SITES = 5_000
MAX_EDGES = 30_000
MAX_NAME_EDGE_CANDIDATES = 3
SNIPPET_CHARS = 200
MAX_REL_PATH_CHARS = 1024

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        ".eggs",
        "__pycache__",
        "node_modules",
        "site-packages",
    }
)
ROUTE_ATTRS = frozenset(
    {"route", "get", "post", "put", "delete", "patch", "head", "options", "websocket", "api_route"}
)
CLI_ATTRS = frozenset({"command", "group"})
TARGET_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NEWLINE_RE = re.compile(r"\r\n|\r|\n")
_MODULE_CALLER = "<module>"


class _Scope:
    """A module, class or function scope: where imports bind and where defs get their names."""

    __slots__ = ("kind", "qualname", "prefix", "parent", "bindings", "method_class")

    def __init__(
        self,
        kind: str,
        qualname: str,
        prefix: str,
        parent: "_Scope | None",
        method_class: str | None,
    ) -> None:
        self.kind = kind
        self.qualname = qualname
        self.prefix = prefix
        self.parent = parent
        self.bindings: dict[str, list[str]] = {}
        self.method_class = method_class


@dataclass
class _Use:
    line: int
    col: int
    root: str
    attrs: tuple[str, ...]
    kind: str
    scope: _Scope


@dataclass
class _Call:
    line: int
    root: str | None
    attrs: tuple[str, ...]
    attr_name: str | None
    scope: _Scope


@dataclass
class _Facts:
    path: str
    module: str
    lines: int
    imports: list[dict[str, Any]] = field(default_factory=list)
    import_callers: list[str] = field(default_factory=list)
    defs: list[dict[str, Any]] = field(default_factory=list)
    classes: set[str] = field(default_factory=set)
    uses: list[_Use] = field(default_factory=list)
    calls: list[_Call] = field(default_factory=list)
    sites: list[dict[str, Any]] = field(default_factory=list)
    has_main_guard: bool = False
    has_module_calls: bool = False


def module_name_for(path: str) -> tuple[str, bool]:
    """Dotted module name and is_package for a repo-relative `.py` path (`src/` prefix stripped)."""
    parts = path[: -len(".py")].split("/") if path.endswith(".py") else path.split("/")
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return (".".join(parts) or "__init__"), is_package


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(part in ("tests", "test", "testing") for part in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def _chain(node: ast.AST) -> tuple[str, tuple[str, ...]] | None:
    """`a.b.c` as ("a", ("b", "c")) when it is rooted at a plain name, else None."""
    attrs: list[str] = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return node.id, tuple(reversed(attrs))
    return None


def _decorator_text(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    chain = _chain(target)
    text = ".".join((chain[0], *chain[1])) if chain else "<expr>"
    return text[:SNIPPET_CHARS]


def _entry_kind_for_function(decorators: list[ast.expr]) -> str | None:
    for decorator in decorators:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        chain = _chain(target)
        if not chain or not chain[1]:
            continue
        last = chain[1][-1]
        if last in CLI_ATTRS:
            return "cli"
        if last in ROUTE_ATTRS:
            has_args = isinstance(decorator, ast.Call) and bool(
                decorator.args or decorator.keywords
            )
            if last == "route" or has_args:
                return "route"
    return None


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq) or len(test.comparators) != 1:
        return False
    sides = (test.left, test.comparators[0])
    has_name = any(isinstance(side, ast.Name) and side.id == "__name__" for side in sides)
    has_main = any(isinstance(side, ast.Constant) and side.value == "__main__" for side in sides)
    return has_name and has_main


def _caller_id(scope: _Scope, module: str) -> str:
    while scope.kind == "class" and scope.parent is not None:
        scope = scope.parent
    if scope.kind == "function":
        return f"{module}:{scope.qualname}"
    return f"{module}:{_MODULE_CALLER}"


def _lookup(scope: _Scope, name: str) -> list[str]:
    """Qualified names `name` may be bound to. Class bodies are invisible to nested functions."""
    current: _Scope | None = scope
    innermost = True
    while current is not None:
        if (current.kind != "class" or innermost) and name in current.bindings:
            return current.bindings[name]
        innermost = False
        current = current.parent
    return []


def _bind(scope: _Scope, name: str, qualified: str) -> None:
    bound = scope.bindings.setdefault(name, [])
    if qualified not in bound:
        bound.append(qualified)


def _resolve_relative(
    module: str, is_package: bool, level: int, name: str | None
) -> tuple[str, bool]:
    """Absolute module for `from <level dots><name> import ...`; False when it leaves the repo."""
    if level == 0:
        return name or "", True
    package = [] if module == "__init__" else module.split(".")
    if not is_package:
        package = package[:-1]
    if level > len(package):
        return "." * level + (name or ""), False
    base = package[: len(package) - (level - 1)]
    if name:
        base = [*base, *name.split(".")]
    return ".".join(base), True


def _decode(data: bytes) -> str:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    except (SyntaxError, LookupError):
        encoding = "utf-8"
    return data.decode(encoding, errors="replace")


def _collect(tree: ast.Module, facts: _Facts, is_package: bool) -> None:
    """Iterative walk: imports, defs, classes, name and call uses. No recursion."""
    module = facts.module
    root_scope = _Scope("module", "", "", None, None)
    facts.has_main_guard = any(_is_main_guard(stmt) for stmt in tree.body)
    stack: list[tuple[ast.AST, _Scope]] = [(stmt, root_scope) for stmt in reversed(tree.body)]
    while stack:
        node, scope = stack.pop()
        kind = type(node)
        if kind is ast.Import:
            caller = _caller_id(scope, module)
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                qualified = alias.name if alias.asname else bound
                _bind(scope, bound, qualified)
                facts.imports.append(
                    {
                        "file": facts.path,
                        "line": node.lineno,
                        "module": alias.name,
                        "name": None,
                        "asname": alias.asname,
                        "bound_as": bound,
                    }
                )
                facts.import_callers.append(caller)
        elif kind is ast.ImportFrom:
            caller = _caller_id(scope, module)
            absolute, resolved = _resolve_relative(module, is_package, node.level or 0, node.module)
            for alias in node.names:
                bound = "*" if alias.name == "*" else (alias.asname or alias.name)
                if alias.name != "*" and resolved:
                    _bind(scope, bound, f"{absolute}.{alias.name}" if absolute else alias.name)
                facts.imports.append(
                    {
                        "file": facts.path,
                        "line": node.lineno,
                        "module": absolute,
                        "name": alias.name,
                        "asname": alias.asname,
                        "bound_as": bound,
                    }
                )
                facts.import_callers.append(caller)
        elif kind is ast.FunctionDef or kind is ast.AsyncFunctionDef:
            _enter_function(node, scope, facts, stack)
        elif kind is ast.ClassDef:
            qualname = scope.prefix + node.name
            facts.classes.add(qualname)
            for expr in (*node.decorator_list, *node.bases, *(k.value for k in node.keywords)):
                stack.append((expr, scope))
            inner = _Scope("class", qualname, qualname + ".", scope, None)
            stack.extend((stmt, inner) for stmt in reversed(node.body))
        elif kind is ast.Call:
            _enter_call(node, scope, facts, stack)
        elif kind is ast.Attribute:
            chain = _chain(node)
            if chain is None:
                stack.append((node.value, scope))
            else:
                facts.uses.append(
                    _Use(node.lineno, node.col_offset, chain[0], chain[1], "reference", scope)
                )
        elif kind is ast.Name:
            if not isinstance(node.ctx, ast.Store):
                facts.uses.append(
                    _Use(node.lineno, node.col_offset, node.id, (), "reference", scope)
                )
        elif kind is not ast.Constant:
            stack.extend((child, scope) for child in ast.iter_child_nodes(node))


def _enter_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    scope: _Scope,
    facts: _Facts,
    stack: list[tuple[ast.AST, _Scope]],
) -> None:
    args = node.args
    all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        all_args.append(args.vararg)
    if args.kwarg:
        all_args.append(args.kwarg)
    outer_exprs: list[ast.AST] = [*node.decorator_list, *args.defaults]
    outer_exprs.extend(default for default in args.kw_defaults if default is not None)
    outer_exprs.extend(arg.annotation for arg in all_args if arg.annotation is not None)
    if node.returns is not None:
        outer_exprs.append(node.returns)
    stack.extend((expr, scope) for expr in outer_exprs)

    qualname = scope.prefix + node.name
    if scope.kind == "class":
        method_class: str | None = scope.qualname
    elif scope.kind == "function":
        method_class = scope.method_class
    else:
        method_class = None
    inner = _Scope("function", qualname, qualname + ".<locals>.", scope, method_class)
    for arg in all_args:
        inner.bindings[arg.arg] = []  # a parameter shadows any import of the same name
    stack.extend((stmt, inner) for stmt in reversed(node.body))
    facts.defs.append(
        {
            "id": f"{facts.module}:{qualname}",
            "file": facts.path,
            "line": node.lineno,
            "end_line": max(node.end_lineno or node.lineno, node.lineno),
            "decorators": [_decorator_text(d) for d in node.decorator_list],
            "entry_kind": _entry_kind_for_function(node.decorator_list),
            "name": node.name,
        }
    )


def _enter_call(node: ast.Call, scope: _Scope, facts: _Facts, stack: list) -> None:
    if _caller_id(scope, facts.module).endswith(f":{_MODULE_CALLER}"):
        facts.has_module_calls = True
    func = node.func
    chain = _chain(func)
    if chain is not None:
        facts.uses.append(_Use(func.lineno, func.col_offset, chain[0], chain[1], "call", scope))
        facts.calls.append(_Call(node.lineno, chain[0], chain[1], None, scope))
    else:
        attr_name = func.attr if isinstance(func, ast.Attribute) else None
        facts.calls.append(_Call(node.lineno, None, (), attr_name, scope))
        stack.append((func, scope))
    stack.extend((arg, scope) for arg in node.args)
    stack.extend((keyword.value, scope) for keyword in node.keywords)


def _ast_too_deep(tree: ast.AST) -> bool:
    """True when the tree nests deeper than `MAX_AST_DEPTH`. Iterative, so it cannot recurse."""
    pending: list[tuple[ast.AST, int]] = [(tree, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_AST_DEPTH:
            return True
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return False


def _analyze_file(path: str, data: bytes, targets: frozenset[str]) -> _Facts:
    """Parse one file and extract its facts. Raises what `ast.parse` raises; the caller skips."""
    module, is_package = module_name_for(path)
    lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = ast.parse(data, filename=path)
    if _ast_too_deep(tree):
        raise RecursionError("ast nesting over MAX_AST_DEPTH")
    facts = _Facts(path=path, module=module, lines=lines)
    _collect(tree, facts, is_package)
    module_def = {
        "id": f"{module}:{_MODULE_CALLER}",
        "file": path,
        "line": 1,
        "end_line": max(lines, 1),
        "decorators": [],
        "entry_kind": (
            "main_block"
            if facts.has_main_guard
            else "script"
            if path.rsplit("/", 1)[-1] == "__main__.py"
            else "module_level"
            if facts.has_module_calls
            else None
        ),
        "name": _MODULE_CALLER,
    }
    facts.defs.append(module_def)
    _emit_sites(facts, targets, data)
    return facts


def _emit_sites(facts: _Facts, targets: frozenset[str], data: bytes) -> None:
    source: list[str] | None = None
    for use in facts.uses:
        seen: set[str] = set()
        for qualified in _lookup(use.scope, use.root):
            full = ".".join((qualified, *use.attrs))
            if full in seen or full.split(".")[0] not in targets:
                continue
            seen.add(full)
            if source is None:
                source = _NEWLINE_RE.split(_decode(data))
            snippet = source[use.line - 1].strip() if 0 < use.line <= len(source) else ""
            facts.sites.append(
                {
                    "file": facts.path,
                    "line": use.line,
                    "col": use.col,
                    "caller": _caller_id(use.scope, facts.module),
                    "kind": use.kind,
                    "snippet": snippet[:SNIPPET_CHARS],
                    "target": full,
                }
            )


class _Graph:
    """Repo-wide tables used to resolve calls to repo definitions."""

    def __init__(self, all_facts: list[_Facts]) -> None:
        self.modules = {f.module for f in all_facts}
        self.def_ids = {d["id"] for f in all_facts for d in f.defs}
        self.classes = {f"{f.module}:{c}" for f in all_facts for c in f.classes}
        by_name: dict[str, set[str]] = {}
        for facts in all_facts:
            for d in facts.defs:
                name = d["name"]
                if name != _MODULE_CALLER and not (name.startswith("__") and name.endswith("__")):
                    by_name.setdefault(name, set()).add(d["id"])
        self.by_name = {name: sorted(ids) for name, ids in by_name.items()}

    def resolve_qualified(self, qualified: str) -> str | None:
        """A repo def id for a fully qualified dotted name, using the longest module prefix."""
        parts = qualified.split(".")
        for cut in range(len(parts), 0, -1):
            module = ".".join(parts[:cut])
            rest = parts[cut:]
            if module not in self.modules or not rest:
                continue
            ident = f"{module}:{'.'.join(rest)}"
            if ident in self.def_ids:
                return ident
            if ident in self.classes and f"{ident}.__init__" in self.def_ids:
                return f"{ident}.__init__"
        return None

    def resolve_local(self, call: _Call, module: str) -> str | None:
        scope: _Scope | None = call.scope
        innermost = True
        suffix = "".join("." + a for a in call.attrs)
        while scope is not None:
            if scope.kind != "class" or innermost:
                base = f"{module}:{scope.prefix}{call.root}"
                if base + suffix in self.def_ids:
                    return base + suffix
                if not suffix and base in self.classes and f"{base}.__init__" in self.def_ids:
                    return f"{base}.__init__"
            innermost = False
            scope = scope.parent
        return None

    def edges_for(self, call: _Call, module: str) -> list[tuple[str, bool]]:
        found: list[tuple[str, bool]] = []
        if call.root is not None:
            local = self.resolve_local(call, module)
            if local:
                return [(local, False)]
            quals = _lookup(call.scope, call.root)
            for qualified in quals:
                callee = self.resolve_qualified(".".join((qualified, *call.attrs)))
                if callee and (callee, False) not in found:
                    found.append((callee, False))
            if found:
                return found
            if call.root in ("self", "cls") and len(call.attrs) == 1 and not quals:
                owner = call.scope.method_class
                if owner:
                    candidate = f"{module}:{owner}.{call.attrs[0]}"
                    if candidate in self.def_ids:
                        return [(candidate, True)]
            if not call.attrs or quals:
                return []  # a plain name, or a call into a library: nothing to guess
            name: str | None = call.attrs[-1]
        else:
            name = call.attr_name
        candidates = self.by_name.get(name or "", [])
        if 1 <= len(candidates) <= MAX_NAME_EDGE_CANDIDATES:
            return [(candidate, True) for candidate in candidates]
        return []


def _build_edges(all_facts: list[_Facts], graph: _Graph) -> list[dict[str, Any]]:
    edges: dict[tuple[str, str, str, int, bool], dict[str, Any]] = {}

    def add(caller: str, callee: str, path: str, line: int, heuristic: bool) -> None:
        edges.setdefault(
            (caller, callee, path, line, heuristic),
            {
                "caller": caller,
                "callee": callee,
                "file": path,
                "line": line,
                "heuristic": heuristic,
            },
        )

    for facts in all_facts:
        for call in facts.calls:
            caller = _caller_id(call.scope, facts.module)
            for callee, heuristic in graph.edges_for(call, facts.module):
                add(caller, callee, facts.path, call.line, heuristic)
        for record, caller in zip(facts.imports, facts.import_callers, strict=True):
            imported = [record["module"]]
            if record["name"] and record["name"] != "*":
                imported.append(f"{record['module']}.{record['name']}" if record["module"] else "")
            for name in imported:
                callee = f"{name}:{_MODULE_CALLER}"
                if name in graph.modules and callee != caller:
                    add(caller, callee, facts.path, record["line"], False)
    return list(edges.values())


def normalize_targets(raw: object) -> list[str]:
    """Top-level import names, validated, de-duplicated and sorted."""
    if not isinstance(raw, list) or not raw or len(raw) > 100:
        raise BadInput("targets must be a non-empty list of top-level import names")
    names: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not TARGET_RE.fullmatch(item):
            raise BadInput("each target must be a top-level import name (letters, digits, _)")
        names.add(item)
    return sorted(names)


def _walk_python_files(root: str, stats: dict[str, int]) -> tuple[list[str], bool]:
    """Sorted repo-relative paths of regular `.py` files. Never follows symlinks."""
    found: list[str] = []
    truncated = False
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        kept = sorted(d for d in dirnames if d not in SKIP_DIRS)
        stats["dirs_skipped"] += len(dirnames) - len(kept)
        dirnames[:] = kept
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            full = os.path.join(current, filename)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if "\\" in rel or "\x00" in rel or len(rel) > MAX_REL_PATH_CHARS:
                stats["names_skipped"] += 1
                continue
            try:
                mode = os.lstat(full).st_mode
            except OSError:
                stats["names_skipped"] += 1
                continue
            if not stat.S_ISREG(mode):
                stats["symlinks_skipped"] += 1
                continue
            if len(found) >= MAX_INDEXED_FILES:
                truncated = True
                break
            found.append(rel)
        if truncated:
            break
    return sorted(found), truncated


def build_index(
    root: str,
    commit_sha: str,
    targets: list[str],
    max_py_file_bytes: int = MAX_PY_FILE_BYTES,
) -> dict[str, Any]:
    """The `AnalysisIndex` as a JSON-ready dict for the repository at `root`."""
    target_set = frozenset(targets)
    stats = {"dirs_skipped": 0, "names_skipped": 0, "symlinks_skipped": 0}
    paths, truncated = _walk_python_files(root, stats)

    files: list[dict[str, Any]] = []
    parsed_facts: list[_Facts] = []
    for rel in paths:
        entry: dict[str, Any] = {
            "path": rel,
            "lines": 0,
            "is_test": is_test_path(rel),
            "parsed": False,
            "skip_reason": None,
        }
        files.append(entry)
        full = os.path.join(root, *rel.split("/"))
        try:
            size = os.lstat(full).st_size
            if size > max_py_file_bytes:
                entry["skip_reason"] = "file over the size limit"
                continue
            with open(full, "rb") as handle:
                data = handle.read(max_py_file_bytes + 1)
        except OSError:
            entry["skip_reason"] = "unreadable"
            continue
        entry["lines"] = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        if b"\x00" in data:
            entry["skip_reason"] = "null bytes"
            continue
        try:
            facts = _analyze_file(rel, data, target_set)
        except SyntaxError:
            entry["skip_reason"] = "syntax error"
        except ValueError:
            entry["skip_reason"] = "invalid source"
        except RecursionError:
            entry["skip_reason"] = "nesting too deep"
        except MemoryError:
            entry["skip_reason"] = "out of memory"
        else:
            entry["parsed"] = True
            parsed_facts.append(facts)

    graph = _Graph(parsed_facts)
    imports = [rec for f in parsed_facts for rec in f.imports]
    defs = [{k: v for k, v in d.items() if k != "name"} for f in parsed_facts for d in f.defs]
    sites = [site for f in parsed_facts for site in f.sites]
    edges = _build_edges(parsed_facts, graph)

    imports.sort(
        key=lambda r: (
            r["file"],
            r["line"],
            r["module"],
            r["name"] or "",
            r["asname"] or "",
            r["bound_as"],
        )
    )
    defs.sort(key=lambda d: (d["file"], d["line"], d["id"]))
    sites.sort(key=lambda s: (s["file"], s["line"], s["col"], s["target"], s["kind"], s["caller"]))
    edges.sort(key=lambda e: (e["file"], e["line"], e["caller"], e["callee"], e["heuristic"]))
    for name, items, cap in (
        ("imports", imports, MAX_IMPORTS),
        ("defs", defs, MAX_DEFS),
        ("target_sites", sites, MAX_SITES),
        ("edges", edges, MAX_EDGES),
    ):
        if len(items) > cap:
            truncated = True
            del items[cap:]
            stats[f"{name}_dropped"] = 1

    stats.update(
        {
            "files_total": len(files),
            "files_parsed": len(parsed_facts),
            "files_skipped": len(files) - len(parsed_facts),
            "lines_total": sum(f["lines"] for f in files),
            "imports": len(imports),
            "defs": len(defs),
            "target_sites": len(sites),
            "edges": len(edges),
        }
    )
    return {
        "agent_protocol": PROTOCOL_VERSION,
        "commit_sha": commit_sha,
        "target_modules": sorted(target_set),
        "files": files,
        "imports": imports,
        "defs": defs,
        "target_sites": sites,
        "edges": edges,
        "truncated": truncated,
        "stats": dict(sorted(stats.items())),
    }


def index_json(index: dict[str, Any]) -> str:
    """Canonical JSON text of an index: what determinism (AC-14) is measured on."""
    return json.dumps(index, sort_keys=True, separators=(",", ":"))


def index_command(args: dict[str, Any], repo_root: str = REPO_ROOT) -> dict[str, Any]:
    """`index` handler. Args: `targets` (top-level import names) and `sha` (the scanned commit)."""
    targets = normalize_targets(args.get("targets"))
    sha = check_sha(require_str(args, "sha"))
    if not os.path.isdir(repo_root):
        raise BadInput("repository directory does not exist")
    result = build_index(repo_root, sha, targets)
    log(f"indexed {result['stats']['files_total']} files, truncated={result['truncated']}")
    return result
