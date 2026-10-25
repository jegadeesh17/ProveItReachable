"""The `ast` tool (ARCHITECTURE sections 7.6 and 9.1): read-only queries over the AnalysisIndex.

Every result is a pure, deterministic function of the index: sorted, compact JSON of at most
`MAX_RESULT_BYTES`, paginated with `limit` / `offset` when a list is long. The tool never reads
a file; the model sees the repository only through these results. Every `file:line` that a result
returns is added to `seen_locations`, which the verdict gate uses to refuse locations the model
made up (rule 2). Errors are results too (`ok=False`); `call` never raises.
"""

import json
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import islice
from typing import Any

from pir.llm import ToolSpec
from pir.models.index import AnalysisIndex, DefRecord, Edge, TargetSite

MAX_RESULT_BYTES = 8 * 1024
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_PATHS = 5
MAX_DEPTH = 8
_MAX_ARG_CHARS = 300
_MAX_ERROR_CHARS = 200

TOOL_NAMES: tuple[str, ...] = (
    "find_imports",
    "find_call_sites",
    "find_callers",
    "get_definition",
    "list_entry_points",
    "find_paths",
    "submit_verdict",
)

_STR = {"type": "string"}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "description": "Page size."}
_OFFSET = {"type": "integer", "minimum": 0, "description": "Items to skip (use next_offset)."}


def _enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_VERDICT_SCHEMA = _schema(
    {
        "verdict": _enum("likely reachable", "no reachable path detected", "uncertain"),
        "confidence": _enum("high", "medium", "low"),
        "call_path": {
            "type": ["array", "null"],
            "description": "Steps copied from a find_paths result; null unless likely reachable.",
            "items": _schema(
                {
                    "file": _STR,
                    "line": {"type": "integer", "minimum": 1},
                    "edge": _enum("entry", "call", "heuristic_call", "vulnerable_call"),
                    "symbol": _STR,
                },
                ["file", "line", "edge", "symbol"],
            ),
        },
        "source_locations": {
            "type": "array",
            "description": "file:line locations returned by the tools that support the verdict.",
            "items": _schema(
                {
                    "file": _STR,
                    "line": {"type": "integer", "minimum": 1},
                    "kind": _enum("import", "call", "reference", "definition"),
                    "snippet": {"type": ["string", "null"], "maxLength": 200},
                },
                ["file", "line", "kind", "snippet"],
            ),
        },
        "explanation": {"type": "string", "maxLength": 2000},
        "limitations": {"type": "array", "minItems": 1, "items": _STR},
    },
    ["verdict", "confidence", "call_path", "source_locations", "explanation", "limitations"],
)

_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="find_imports",
        description=(
            "Import statements of a module and its submodules in repository code, with the local "
            "name each binds (bound_as) and file:line."
        ),
        parameters=_schema({"module": _STR, "limit": _LIMIT, "offset": _OFFSET}, ["module"]),
    ),
    ToolSpec(
        name="find_call_sites",
        description=(
            "Uses (calls or references) of a dotted symbol such as yaml.load in repository code, "
            "with file:line, the enclosing function and whether the file is a test."
        ),
        parameters=_schema({"symbol": _STR, "limit": _LIMIT, "offset": _OFFSET}, ["symbol"]),
    ),
    ToolSpec(
        name="find_callers",
        description=(
            "Calls into a repository function: caller function id, file:line and whether the "
            "edge is a heuristic (name-based) match."
        ),
        parameters=_schema(
            {"function_id": _STR, "limit": _LIMIT, "offset": _OFFSET}, ["function_id"]
        ),
    ),
    ToolSpec(
        name="get_definition",
        description="A repository function: file, first and last line, decorators, entry kind.",
        parameters=_schema({"function_id": _STR}, ["function_id"]),
    ),
    ToolSpec(
        name="list_entry_points",
        description=(
            "Functions and modules that start execution: __main__ blocks, web routes, CLI "
            "commands, scripts and module-level code."
        ),
        parameters=_schema({"limit": _LIMIT, "offset": _OFFSET}, []),
    ),
    ToolSpec(
        name="find_paths",
        description=(
            "Up to 5 shortest call paths from entry points to uses of a symbol. Each path is a "
            "list of steps ready to copy into submit_verdict.call_path."
        ),
        parameters=_schema(
            {
                "symbol": _STR,
                "max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_DEPTH,
                    "description": f"Most calls from the entry point (default {MAX_DEPTH}).",
                },
            },
            ["symbol"],
        ),
    ),
    ToolSpec(
        name="submit_verdict",
        description=(
            "Submit the final reachability verdict. Call it once, when finished. A likely "
            "reachable verdict needs a call_path from find_paths."
        ),
        parameters=_VERDICT_SCHEMA,
    ),
)

_ALLOWED_ARGS: dict[str, frozenset[str]] = {
    spec.name: frozenset(spec.parameters["properties"]) for spec in _SPECS
}


@dataclass(frozen=True)
class ToolResult:
    """`content` is compact JSON (at most MAX_RESULT_BYTES). `ok` is false for an error result."""

    ok: bool
    content: str

    @property
    def result_bytes(self) -> int:
        return len(self.content.encode("utf-8"))


class _ArgError(Exception):
    pass


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _size(payload: dict[str, Any]) -> int:
    return len(_dump(payload))  # ASCII only, so characters equal bytes


def _error(message: str) -> ToolResult:
    return ToolResult(False, _dump({"error": message[:_MAX_ERROR_CHARS]}))


def _text_arg(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _ArgError(f"{key} must be a non-empty string")
    if len(value) > _MAX_ARG_CHARS:
        raise _ArgError(f"{key} is longer than {_MAX_ARG_CHARS} characters")
    return value.strip()


def _int_arg(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _ArgError(f"{key} must be an integer")
    if not low <= value <= high:
        raise _ArgError(f"{key} must be {low} to {high}")
    return value


def _module_matches(module: str, wanted: str) -> bool:
    return module == wanted or module.startswith(wanted + ".")


class AstTool:
    """Deterministic, read-only. `seen_locations` collects every file:line a result returned."""

    def __init__(self, index: AnalysisIndex) -> None:
        self._index = index
        self.seen_locations: set[tuple[str, int]] = set()
        self._tests = {f.path for f in index.files if f.is_test}
        self._defs: dict[str, DefRecord] = {}
        for definition in index.defs:
            self._defs.setdefault(definition.id, definition)
        self._handlers: dict[str, Callable[[dict[str, Any]], str]] = {
            "find_imports": self._find_imports,
            "find_call_sites": self._find_call_sites,
            "find_callers": self._find_callers,
            "get_definition": self._get_definition,
            "list_entry_points": self._list_entry_points,
            "find_paths": self._find_paths,
        }

    def specs(self) -> list[ToolSpec]:
        return list(_SPECS)

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run one tool. Unknown names, bad arguments and `submit_verdict` give an error result."""
        if name == "submit_verdict":
            return _error("submit_verdict is the final answer and is not an index query")
        handler = self._handlers.get(name)
        if handler is None:
            return _error(f"unknown tool: {name[:60]}")
        if not isinstance(arguments, dict):
            return _error("arguments must be a JSON object")
        unexpected = sorted(set(arguments) - _ALLOWED_ARGS[name])
        if unexpected:
            return _error(f"unexpected argument: {unexpected[0][:60]}")
        try:
            return ToolResult(True, handler(arguments))
        except _ArgError as exc:
            return _error(str(exc))

    # ---- helpers ----------------------------------------------------------------------

    def _page(
        self,
        key: str,
        items: list[dict[str, Any]],
        offset: int,
        limit: int,
        locations: Callable[[dict[str, Any]], list[tuple[str, int]]],
        extra: dict[str, Any] | None = None,
    ) -> str:
        """The longest prefix of the requested page whose JSON fits, and its locations."""
        total = len(items)
        window = items[offset : offset + limit]
        included: list[dict[str, Any]] = []

        def payload(chosen: list[dict[str, Any]]) -> dict[str, Any]:
            end = offset + len(chosen)
            body: dict[str, Any] = {
                key: chosen,
                "total": total,
                "offset": offset,
                "next_offset": end if end < total else None,
            }
            body.update(extra or {})
            return body

        for item in window:
            if _size(payload([*included, item])) > MAX_RESULT_BYTES:
                break
            included.append(item)
        if window and not included:
            raise _ArgError("a single result is larger than the size limit")
        for item in included:
            self.seen_locations.update(locations(item))
        return _dump(payload(included))

    @staticmethod
    def _file_line(item: dict[str, Any]) -> list[tuple[str, int]]:
        return [(item["file"], item["line"])]

    def _limit_offset(self, args: dict[str, Any]) -> tuple[int, int]:
        limit = _int_arg(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        offset = _int_arg(args, "offset", 0, 0, 1_000_000)
        return limit, offset

    @staticmethod
    def _site_item(site: TargetSite, in_test: bool) -> dict[str, Any]:
        return {
            "file": site.file,
            "line": site.line,
            "col": site.col,
            "caller": site.caller,
            "kind": site.kind,
            "snippet": site.snippet,
            "target": site.target,
            "in_test": in_test,
        }

    # ---- tools ------------------------------------------------------------------------

    def _find_imports(self, args: dict[str, Any]) -> str:
        module = _text_arg(args, "module")
        limit, offset = self._limit_offset(args)
        records = sorted(
            (r for r in self._index.imports if _module_matches(r.module, module)),
            key=lambda r: (r.file, r.line, r.module, r.name or "", r.asname or "", r.bound_as),
        )
        items = [
            {
                "file": r.file,
                "line": r.line,
                "module": r.module,
                "name": r.name,
                "asname": r.asname,
                "bound_as": r.bound_as,
            }
            for r in records
        ]
        return self._page("imports", items, offset, limit, self._file_line)

    def _find_call_sites(self, args: dict[str, Any]) -> str:
        symbol = _text_arg(args, "symbol")
        limit, offset = self._limit_offset(args)
        sites = sorted(
            (s for s in self._index.target_sites if _module_matches(s.target, symbol)),
            key=lambda s: (s.file, s.line, s.col, s.target, s.kind),
        )
        items = [self._site_item(s, s.file in self._tests) for s in sites]
        return self._page("sites", items, offset, limit, self._file_line)

    def _find_callers(self, args: dict[str, Any]) -> str:
        function_id = _text_arg(args, "function_id")
        limit, offset = self._limit_offset(args)
        edges = sorted(
            (e for e in self._index.edges if e.callee == function_id),
            key=lambda e: (e.file, e.line, e.caller, e.heuristic),
        )
        items = [
            {"caller": e.caller, "file": e.file, "line": e.line, "heuristic": e.heuristic}
            for e in edges
        ]
        return self._page("callers", items, offset, limit, self._file_line)

    def _get_definition(self, args: dict[str, Any]) -> str:
        function_id = _text_arg(args, "function_id")
        definition = self._defs.get(function_id)
        if definition is None:
            raise _ArgError("no such function id in the index")
        self.seen_locations.add((definition.file, definition.line))
        return _dump(
            {
                "id": definition.id,
                "file": definition.file,
                "line": definition.line,
                "end_line": definition.end_line,
                "decorators": definition.decorators,
                "entry_kind": definition.entry_kind,
            }
        )

    def _list_entry_points(self, args: dict[str, Any]) -> str:
        limit, offset = self._limit_offset(args)
        entries = sorted(
            (d for d in self._index.defs if d.entry_kind is not None),
            key=lambda d: (d.file, d.line, d.id),
        )
        items = [
            {"id": d.id, "file": d.file, "line": d.line, "entry_kind": d.entry_kind}
            for d in entries
        ]
        return self._page("entry_points", items, offset, limit, self._file_line)

    def _find_paths(self, args: dict[str, Any]) -> str:
        symbol = _text_arg(args, "symbol")
        max_depth = _int_arg(args, "max_depth", MAX_DEPTH, 0, MAX_DEPTH)
        paths = self._shortest_paths(symbol, max_depth)
        extra: dict[str, Any] = {"symbol": symbol, "max_depth": max_depth}
        if not paths:
            extra["note"] = "no path from an entry point within max_depth"
        return self._page(
            "paths",
            paths,
            0,
            MAX_PATHS,
            lambda p: [(s["file"], s["line"]) for s in p["steps"]],
            extra,
        )

    # ---- find_paths -------------------------------------------------------------------

    def _best_edges(self) -> dict[str, dict[str, Edge]]:
        """caller -> callee -> the edge to report (non-heuristic first, then file, line)."""
        best: dict[str, dict[str, Edge]] = {}
        for edge in self._index.edges:
            current = best.setdefault(edge.caller, {}).get(edge.callee)
            if current is None or _edge_key(edge) < _edge_key(current):
                best[edge.caller][edge.callee] = edge
        return best

    def _shortest_paths(self, symbol: str, max_depth: int) -> list[dict[str, Any]]:
        sites = [s for s in self._index.target_sites if _module_matches(s.target, symbol)]
        entries = sorted({d.id for d in self._index.defs if d.entry_kind is not None})
        if not sites or not entries:
            return []
        edges = self._best_edges()
        dist: dict[str, int] = dict.fromkeys(entries, 0)
        preds: dict[str, list[str]] = {}
        queue: deque[str] = deque(entries)
        while queue:
            node = queue.popleft()
            if dist[node] >= max_depth:
                continue
            for callee in sorted(edges.get(node, {})):
                if callee not in dist:
                    dist[callee] = dist[node] + 1
                    queue.append(callee)
                if dist[callee] == dist[node] + 1:
                    preds.setdefault(callee, []).append(node)

        reachable = sorted(
            (s for s in sites if s.caller in dist),
            key=lambda s: (dist[s.caller], s.file, s.line, s.col, s.target),
        )
        found: list[dict[str, Any]] = []
        for site in reachable:
            for nodes in islice(_node_paths(site.caller, preds), MAX_PATHS - len(found)):
                found.append(self._path_item(nodes, site, edges))
            if len(found) >= MAX_PATHS:
                break
        return found

    def _path_item(
        self, nodes: list[str], site: TargetSite, edges: dict[str, dict[str, Edge]]
    ) -> dict[str, Any]:
        entry = self._defs[nodes[0]]
        steps: list[dict[str, Any]] = [
            {"file": entry.file, "line": entry.line, "edge": "entry", "symbol": entry.id}
        ]
        heuristic = False
        for caller, callee in zip(nodes, nodes[1:], strict=False):
            edge = edges[caller][callee]
            heuristic = heuristic or edge.heuristic
            kind = "heuristic_call" if edge.heuristic else "call"
            steps.append({"file": edge.file, "line": edge.line, "edge": kind, "symbol": callee})
        steps.append(
            {"file": site.file, "line": site.line, "edge": "vulnerable_call", "symbol": site.target}
        )
        return {"depth": len(nodes) - 1, "heuristic": heuristic, "steps": steps}


def _edge_key(edge: Edge) -> tuple[bool, str, int]:
    return (edge.heuristic, edge.file, edge.line)


def _node_paths(node: str, preds: dict[str, list[str]]) -> Iterator[list[str]]:
    """Every shortest node path from an entry point to `node`, lazily, in sorted order."""
    parents = sorted(preds.get(node, []))
    if not parents:
        yield [node]
        return
    for parent in parents:
        for prefix in _node_paths(parent, preds):
            yield [*prefix, node]
