"""
indexer.py — Symbol-level code indexing for agora-code.

Extracts per-symbol (function/class/method) one-liners from a file using
tree-sitter (via the existing summarizer) and writes them to symbol_notes.

Entry points used by hooks:
  index_file(file_path, ...)   — called on PostToolUse(Read) first-time + PostToolUse(Edit)
  tag_commit(sha, files, ...)  — called on PostToolUse(Bash) when git commit detected

Design:
  - One row per symbol per (project_id, file_path, branch)
  - note = first docstring/comment line (zero LLM cost)
  - signature = function def line with params
  - On file edit: delete old symbols for file, re-index
  - On commit: backfill commit_sha on file_changes + symbol_notes
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# Extensions supported by tree-sitter (subset of what summarizer handles)
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".cs", ".rb", ".swift", ".kt", ".php",
    ".sh", ".bash", ".yaml", ".yml", ".toml",
}

# Map extension → tree-sitter language name (mirrors summarizer._EXT_TO_LANG)
_EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".go": "go",
    ".rs": "rust", ".java": "java", ".c": "c", ".cpp": "cpp",
    ".cs": "c_sharp", ".rb": "ruby", ".swift": "swift",
    ".kt": "kotlin", ".php": "php",
}


def extract_symbols(file_path: str, content: Optional[str] = None) -> list[dict]:
    """
    Parse a file with tree-sitter and return a list of symbol dicts:
      {symbol_type, symbol_name, start_line, end_line, signature, note}

    Falls back to regex-based extraction for unsupported extensions.
    Returns [] if file is not a code file or can't be parsed.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext not in _CODE_EXTENSIONS:
        return []

    if content is None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

    # Python: use stdlib ast (more accurate than tree-sitter for .py)
    if ext == ".py":
        try:
            symbols = _extract_python_ast(content, file_path)
            if symbols:
                return symbols
        except Exception:
            pass
        return _extract_python_regex(content)

    lang = _EXT_TO_LANG.get(ext)
    if lang:
        try:
            symbols = _extract_with_treesitter(content, file_path, lang)
            if symbols:
                return symbols
        except Exception:
            pass

    return []


def index_file(
    file_path: str,
    *,
    content: Optional[str] = None,
    project_id: Optional[str] = None,
    branch: Optional[str] = None,
    commit_sha: Optional[str] = None,
    session_id: Optional[str] = None,
) -> int:
    """
    Extract symbols from file and upsert into symbol_notes + file_snapshots.
    Deletes stale symbols for the file before re-indexing.
    Returns number of symbols indexed.
    """
    from agora_code.vector_store import get_store
    from agora_code.summarizer import summarize_file

    path = Path(file_path)
    ext = path.suffix.lower()
    if ext not in _CODE_EXTENSIONS:
        return 0

    if content is None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return 0

    store = get_store()

    # 1. Re-index symbols (delete stale first so removed functions don't linger)
    store.delete_symbols_for_file(file_path, project_id=project_id, branch=branch)

    symbols = extract_symbols(file_path, content=content)
    if not symbols:
        return 0

    rows = [
        {
            "file_path": file_path,
            "symbol_type": s["symbol_type"],
            "symbol_name": s["symbol_name"],
            "start_line": s.get("start_line"),
            "end_line": s.get("end_line"),
            "signature": s.get("signature"),
            "note": s.get("note"),
            "project_id": project_id,
            "branch": branch,
            "commit_sha": commit_sha,
            "session_id": session_id,
        }
        for s in symbols
    ]
    count = store.upsert_symbol_notes_bulk(rows)

    # 2. Also upsert file_snapshots with the full AST summary text
    try:
        summary_text = summarize_file(file_path, content)
        symbols_json = _symbols_to_json(symbols)
        store.upsert_file_snapshot(
            file_path, summary_text,
            symbols=symbols_json,
            project_id=project_id,
            branch=branch,
            commit_sha=commit_sha,
            session_id=session_id,
        )
    except Exception:
        pass

    return count


def tag_commit(
    commit_sha: str,
    file_paths: list[str],
    project_id: Optional[str] = None,
    branch: Optional[str] = None,
) -> int:
    """
    Called when a git commit is detected. Tags file_changes + symbol_notes
    with the commit SHA and marks file_changes as committed.
    Returns number of file_changes rows updated.
    """
    from agora_code.vector_store import get_store
    return get_store().tag_committed_files(
        file_paths, commit_sha,
        project_id=project_id,
        branch=branch,
    )


# ── Python AST extraction (stdlib, most accurate for .py) ────────────────────

def _extract_python_ast(content: str, file_path: str) -> list[dict]:
    """Extract symbols from Python files using stdlib ast — exact line numbers and docstrings."""
    import ast
    from agora_code.summarizer import _func_signature, _ast_name

    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return []

    symbols: list[dict] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = [_ast_name(b) for b in node.bases]
            base_str = f"({', '.join(bases)})" if bases else ""
            sig = f"class {node.name}{base_str}"
            doc = ast.get_docstring(node)
            note = doc.split("\n")[0].strip() if doc else None
            symbols.append({
                "symbol_type": "class",
                "symbol_name": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
                "signature": sig,
                "note": note,
            })
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig = _func_signature(item)
                    doc = ast.get_docstring(item)
                    note = doc.split("\n")[0].strip() if doc else None
                    symbols.append({
                        "symbol_type": "method",
                        "symbol_name": item.name,
                        "start_line": item.lineno,
                        "end_line": item.end_lineno,
                        "signature": sig,
                        "note": note,
                    })

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = _func_signature(node)
            doc = ast.get_docstring(node)
            note = doc.split("\n")[0].strip() if doc else None
            symbols.append({
                "symbol_type": "function",
                "symbol_name": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
                "signature": sig,
                "note": note,
            })

    return symbols


# ── Tree-sitter extraction ────────────────────────────────────────────────────

def _extract_with_treesitter(content: str, file_path: str, lang: str) -> list[dict]:
    """Use tree-sitter to extract symbols with line numbers, signatures, and notes."""
    from tree_sitter_language_pack import get_language, get_parser
    from tree_sitter import Query, QueryCursor
    from agora_code.summarizer import _TS_QUERIES, _preceding_comment

    try:
        parser = get_parser(lang)
        language = get_language(lang)
    except Exception:
        return []

    query_src = _TS_QUERIES.get(lang)
    if not query_src:
        return []

    try:
        query = Query(language, query_src)
    except Exception:
        return []

    source_bytes = content.encode("utf-8", errors="replace")
    tree = parser.parse(source_bytes)
    cursor = QueryCursor(query)
    captures = cursor.captures(tree.root_node)
    source_lines = content.splitlines()

    symbols: list[dict] = []

    # ── Classes ───────────────────────────────────────────────────────────────
    class_nodes = captures.get("class", [])
    class_name_nodes = captures.get("class.name", [])
    class_end_by_start: dict[int, int] = {
        n.start_point[0] + 1: n.end_point[0] + 1 for n in class_nodes
    }
    seen_classes: set[str] = set()
    for node in class_name_nodes:
        name = node.text.decode("utf-8", errors="replace").strip()
        if name in seen_classes:
            continue
        seen_classes.add(name)
        start = node.start_point[0] + 1
        end = class_end_by_start.get(start, start + 50)
        # signature = first line of class declaration
        sig = source_lines[start - 1].strip() if start <= len(source_lines) else f"class {name}"
        if len(sig) > 120:
            sig = sig[:117] + "..."
        note = _preceding_comment(source_lines, start) or ""
        if not note:
            note = _first_docstring(source_lines, start)
        symbols.append({
            "symbol_type": "class",
            "symbol_name": name,
            "start_line": start,
            "end_line": end,
            "signature": sig,
            "note": note or None,
        })

    # ── Functions / methods ───────────────────────────────────────────────────
    func_nodes_raw = captures.get("func", [])
    func_name_nodes = captures.get("func.name", [])
    func_param_nodes = captures.get("func.params", [])

    param_by_line: dict[int, str] = {
        n.start_point[0] + 1: n.text.decode("utf-8", errors="replace").strip()
        for n in func_param_nodes
    }
    func_end_by_start: dict[int, int] = {
        n.start_point[0] + 1: n.end_point[0] + 1 for n in func_nodes_raw
    }
    class_ranges = [(s, e) for _, s, e in [
        (n.text.decode("utf-8", errors="replace").strip(),
         n.start_point[0] + 1,
         class_end_by_start.get(n.start_point[0] + 1, n.start_point[0] + 50))
        for n in class_name_nodes
    ]]

    seen_funcs: set[str] = set()
    for node in func_name_nodes:
        name = node.text.decode("utf-8", errors="replace").strip()
        start = node.start_point[0] + 1
        key = f"{name}:{start}"
        if key in seen_funcs:
            continue
        seen_funcs.add(key)

        params = param_by_line.get(start, "()")
        if len(params) > 80:
            params = params[:77] + "..."
        end = func_end_by_start.get(start, start + 30)

        # signature = "def name(params)" style
        raw_line = source_lines[start - 1].strip() if start <= len(source_lines) else ""
        sig = raw_line if raw_line else f"{name}{params}"
        if len(sig) > 120:
            sig = sig[:117] + "..."

        note = _preceding_comment(source_lines, start) or ""
        if not note:
            note = _first_docstring(source_lines, start)

        sym_type = "method" if any(s <= start <= e for s, e in class_ranges) else "function"
        symbols.append({
            "symbol_type": sym_type,
            "symbol_name": name,
            "start_line": start,
            "end_line": end,
            "signature": sig,
            "note": note or None,
        })

    return symbols


# ── Python regex fallback ─────────────────────────────────────────────────────

_PY_DEF = re.compile(r'^( *)(?:async )?def (\w+)\(([^)]*)\)', re.MULTILINE)
_PY_CLASS = re.compile(r'^class (\w+)', re.MULTILINE)
_PY_DOCSTRING = re.compile(r'^\s+["\']([^\n"\']{5,120})["\']', re.MULTILINE)


def _extract_python_regex(content: str) -> list[dict]:
    lines = content.splitlines()
    symbols: list[dict] = []
    for m in _PY_DEF.finditer(content):
        start = content[:m.start()].count('\n') + 1
        name = m.group(2)
        params = m.group(3)[:80]
        sig = m.group(0).strip()
        note = _first_docstring(lines, start)
        symbols.append({
            "symbol_type": "function",
            "symbol_name": name,
            "start_line": start,
            "end_line": None,
            "signature": sig,
            "note": note or None,
        })
    for m in _PY_CLASS.finditer(content):
        start = content[:m.start()].count('\n') + 1
        name = m.group(1)
        note = _first_docstring(lines, start)
        symbols.append({
            "symbol_type": "class",
            "symbol_name": name,
            "start_line": start,
            "end_line": None,
            "signature": m.group(0).strip(),
            "note": note or None,
        })
    return symbols


# ── Helpers ───────────────────────────────────────────────────────────────────

def _first_docstring(lines: list[str], def_line: int) -> str:
    """Return the first docstring line immediately after a def/class, if any."""
    for i in range(def_line, min(def_line + 4, len(lines))):
        line = lines[i].strip()
        for q in ('"""', "'''", '"', "'"):
            if line.startswith(q):
                text = line.strip(q).strip()
                if len(text) > 4:
                    # strip trailing quote if single-line docstring
                    text = text.split(q)[0].strip()
                    return text[:120] if text else ""
    return ""


def _symbols_to_json(symbols: list[dict]) -> str:
    """Compact JSON list of symbol names for FTS indexing."""
    import json
    return json.dumps([s["symbol_name"] for s in symbols])
