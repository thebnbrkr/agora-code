"""
summarizer.py — File content summarization for token-efficient context injection.

Pipeline:
  Python             → stdlib AST (classes, functions, docstrings, signatures)
  JS/TS/Ruby/Java/
  Go/Rust/C#/PHP/
  Swift/Kotlin/etc.  → tree-sitter-language-pack (real AST, exact line numbers)
  JSON               → top-level keys + structure overview
  YAML               → top-level keys + structure overview
  Markdown/text      → headings + opening paragraph
  Unsupported ext    → generic regex fallback

Token estimation: tiktoken if installed, else word-count heuristic.
"""
from __future__ import annotations

import json
import re
from typing import Optional


# ── Token estimation ──────────────────────────────────────────────────────────

_tiktoken_enc = None


def _get_tiktoken_encoder():
    global _tiktoken_enc
    if _tiktoken_enc is None:
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        except (ImportError, Exception):
            _tiktoken_enc = False
    return _tiktoken_enc if _tiktoken_enc is not False else None


def estimate_tokens(text: str) -> int:
    """
    Estimate token count. Uses tiktoken (BPE, cl100k_base) if installed —
    accurate for GPT-4/Claude class models. Falls back to a word-based
    heuristic (~1.3 tokens per word) which is better than chars//4.
    """
    if not text:
        return 1
    enc = _get_tiktoken_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, int(len(text.split()) * 1.3))


# ── Configuration ─────────────────────────────────────────────────────────────

FILE_SUMMARY_TOKEN_BUDGET = 1000
FILE_SIZE_THRESHOLD = 500  # lines — files smaller than this pass through


# ── Extension → tree-sitter language name ────────────────────────────────────

_EXT_TO_LANG: dict[str, str] = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".rb": "ruby", ".rake": "ruby",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
    ".sh": "bash", ".bash": "bash",
    ".zig": "zig",
    ".ex": "elixir", ".exs": "elixir",
    ".sql": "sql",
    ".toml": "toml",
    ".tf": "hcl", ".hcl": "hcl",
    ".proto": "proto",
}

# ── Tree-sitter queries — one per language family ────────────────────────────
# Captures: @import, @class.name, @func.name, @func.params

_TS_QUERIES: dict[str, str] = {
    "javascript": """
        (import_statement) @import
        (class_declaration name: (identifier) @class.name) @class
        (function_declaration
            name: (identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
        (method_definition
            name: (property_identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
        (lexical_declaration
            (variable_declarator
                name: (identifier) @func.name
                value: (arrow_function
                    parameters: (formal_parameters) @func.params))) @func
        (lexical_declaration
            (variable_declarator
                name: (identifier) @func.name
                value: (function_expression
                    parameters: (formal_parameters) @func.params))) @func
    """,
    "typescript": """
        (import_statement) @import
        (class_declaration name: (type_identifier) @class.name) @class
        (interface_declaration name: (type_identifier) @class.name) @class
        (type_alias_declaration name: (type_identifier) @class.name) @class
        (function_declaration
            name: (identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
        (method_definition
            name: (property_identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
        (abstract_method_definition
            name: (property_identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
    """,
    "tsx": """
        (import_statement) @import
        (class_declaration name: (type_identifier) @class.name) @class
        (function_declaration
            name: (identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
        (method_definition
            name: (property_identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
    """,
    "ruby": """
        (class name: [(constant)(scope_resolution)] @class.name) @class
        (module name: [(constant)(scope_resolution)] @class.name) @class
        (method name: (identifier) @func.name
                parameters: (method_parameters)? @func.params) @func
        (singleton_method name: (identifier) @func.name
                          parameters: (method_parameters)? @func.params) @func
    """,
    "java": """
        (import_declaration) @import
        (class_declaration name: (identifier) @class.name) @class
        (interface_declaration name: (identifier) @class.name) @class
        (enum_declaration name: (identifier) @class.name) @class
        (method_declaration
            name: (identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
        (constructor_declaration
            name: (identifier) @func.name
            parameters: (formal_parameters) @func.params) @func
    """,
    "go": """
        (import_declaration) @import
        (type_declaration
            (type_spec name: (type_identifier) @class.name)) @class
        (function_declaration
            name: (identifier) @func.name
            parameters: (parameter_list) @func.params) @func
        (method_declaration
            name: (field_identifier) @func.name
            parameters: (parameter_list) @func.params) @func
    """,
    "rust": """
        (use_declaration) @import
        (struct_item name: (type_identifier) @class.name) @class
        (enum_item name: (type_identifier) @class.name) @class
        (trait_item name: (type_identifier) @class.name) @class
        (impl_item type: (type_identifier) @class.name) @class
        (function_item
            name: (identifier) @func.name
            parameters: (parameters) @func.params) @func
    """,
    "c_sharp": """
        (using_directive) @import
        (class_declaration name: (identifier) @class.name) @class
        (interface_declaration name: (identifier) @class.name) @class
        (struct_declaration name: (identifier) @class.name) @class
        (record_declaration name: (identifier) @class.name) @class
        (method_declaration
            name: (identifier) @func.name
            parameters: (parameter_list) @func.params) @func
        (constructor_declaration
            name: (identifier) @func.name
            parameters: (parameter_list) @func.params) @func
    """,
    "php": """
        (namespace_definition name: (namespace_name) @class.name) @class
        (class_declaration name: (name) @class.name) @class
        (interface_declaration name: (name) @class.name) @class
        (function_definition
            name: (name) @func.name
            parameters: (formal_parameters) @func.params) @func
        (method_declaration
            name: (name) @func.name
            parameters: (formal_parameters) @func.params) @func
    """,
    "swift": """
        (import_declaration) @import
        (class_declaration name: (type_identifier) @class.name) @class
        (struct_declaration name: (type_identifier) @class.name) @class
        (protocol_declaration name: (type_identifier) @class.name) @class
        (function_declaration
            name: (simple_identifier) @func.name
            function_value_parameters: (function_value_parameters) @func.params) @func
    """,
    "kotlin": """
        (import_header) @import
        (class_declaration (type_identifier) @class.name) @class
        (object_declaration (type_identifier) @class.name) @class
        (function_declaration
            (simple_identifier) @func.name
            (function_value_parameters) @func.params) @func
    """,
    "scala": """
        (import_declaration) @import
        (class_definition name: (identifier) @class.name) @class
        (object_definition name: (identifier) @class.name) @class
        (trait_definition name: (identifier) @class.name) @class
        (function_definition
            name: (identifier) @func.name
            parameters: (parameters) @func.params) @func
    """,
    "lua": """
        (function_declaration
            name: (identifier) @func.name
            parameters: (parameters) @func.params) @func
        (local_function
            name: (identifier) @func.name
            parameters: (parameters) @func.params) @func
    """,
    "bash": """
        (function_definition
            name: (word) @func.name) @func
    """,
    "c": """
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @func.name
                parameters: (parameter_list) @func.params)) @func
        (struct_specifier name: (type_identifier) @class.name) @class
        (type_definition declarator: (type_identifier) @class.name) @class
    """,
    "cpp": """
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @func.name
                parameters: (parameter_list) @func.params)) @func
        (class_specifier name: (type_identifier) @class.name) @class
        (struct_specifier name: (type_identifier) @class.name) @class
    """,
    "elixir": """
        (call
            target: (identifier) @_def
            arguments: (arguments (alias) @class.name)
            (#match? @_def "^(defmodule)$")) @class
        (call
            target: (identifier) @_def
            arguments: (arguments (identifier) @func.name)
            (#match? @_def "^(def|defp|defmacro)$")) @func
    """,
    "zig": """
        (ContainerDecl) @class
        (FnProto
            (IDENTIFIER) @func.name
            (ParamDeclList) @func.params) @func
    """,
    "sql": """
        (create_table (object_reference) @class.name) @class
        (create_view (object_reference) @class.name) @class
        (create_index (identifier) @func.name) @func
    """,
    "toml": """
        (table (bare_key) @class.name) @class
        (table_array_element (bare_key) @class.name) @class
    """,
    "hcl": """
        (block (identifier) @class.name) @class
    """,
    "proto": """
        (message (message_name) @class.name) @class
        (enum (enum_name) @class.name) @class
        (service (service_name) @class.name) @class
        (rpc (rpc_name) @func.name) @func
    """,
}


# ── Public API ────────────────────────────────────────────────────────────────

def summarize_file(
    file_path: str,
    content: str,
    max_tokens: int = FILE_SUMMARY_TOKEN_BUDGET,
) -> Optional[str]:
    """
    Summarize file content for context injection instead of raw content.

    Returns None if the file is small enough to pass through uncompressed.
    Returns a structural summary with line numbers otherwise.

    The returned string has a trailing marker:
      [parser=ast]      — Python stdlib AST
      [parser=treesitter] — tree-sitter-language-pack
      [parser=generic]  — regex fallback (unsupported extension or parse failure)
                          Hook should treat this as "pass full file + prompt"
    """
    lines = content.splitlines()
    line_count = len(lines)

    if line_count <= FILE_SIZE_THRESHOLD:
        return None

    ext = _file_ext(file_path)

    if ext == ".py":
        summary = _summarize_python(content, file_path)
        parser = "ast"
    elif ext in (".json",):
        summary = _summarize_json(content, file_path)
        parser = "ast"
    elif ext in (".yaml", ".yml"):
        summary = _summarize_yaml(content, file_path)
        parser = "ast"
    elif ext in (".md", ".rst", ".txt"):
        summary = _summarize_text(content, file_path)
        parser = "ast"
    elif ext in _EXT_TO_LANG:
        summary, parser = _summarize_with_treesitter(content, file_path, _EXT_TO_LANG[ext])
    else:
        summary = _summarize_generic(content, file_path)
        parser = "generic"

    header = f"[agora-code summary of {file_path} — {line_count} lines]\n"
    footer = (
        f"\n[File has {line_count} lines. To read specific sections, use offset+limit.]"
        f"\n[parser={parser}]"
    )
    result = header + summary + footer

    return result


# ── Tree-sitter summarizer ────────────────────────────────────────────────────

def _preceding_comment(source_lines: list[str], decl_line: int) -> str:
    """
    Return the best comment text preceding decl_line (1-indexed).

    Handles:
      - Single-line: // text | /// text | # text | -- text
      - Multi-line JSDoc/block: /** ... */ or /* ... */ — walks back to opening /*
      - Rust/Go doc blocks: consecutive /// or // lines above the declaration
    Returns '' if no meaningful comment found.
    """
    idx = decl_line - 2   # 0-indexed line immediately before declaration
    if idx < 0:
        return ""

    raw = source_lines[idx].strip()
    if not raw:
        return ""

    comment_prefixes = ("///", "//", "##", "#", "--")

    # Case 1: closing line of a block comment (ends with */)
    if raw.endswith("*/"):
        # Walk back to find the opening /*
        lines_collected: list[str] = []
        i = idx
        while i >= 0:
            line = source_lines[i].strip()
            # Strip leading * or /** markers
            cleaned = line.lstrip("/*").rstrip("*/").strip()
            if cleaned and not cleaned.startswith("@"):
                lines_collected.insert(0, cleaned)
            if line.startswith("/*"):
                break
            i -= 1
        text = " ".join(lines_collected).strip()
        return text[:120] if text else ""

    # Case 2: consecutive single-line comment block (walk back while lines are comments)
    collected: list[str] = []
    i = idx
    while i >= 0:
        line = source_lines[i].strip()
        matched = False
        for prefix in comment_prefixes:
            if line.startswith(prefix):
                text = line[len(prefix):].strip()
                if text and not text.startswith("@"):
                    collected.insert(0, text)
                matched = True
                break
        if not matched:
            break
        i -= 1

    if collected:
        return " ".join(collected)[:120]

    return ""


def _summarize_with_treesitter(content: str, file_path: str, lang: str) -> tuple[str, str]:
    """
    Parse with tree-sitter and extract classes, functions, imports with
    exact line numbers from the AST.

    Returns (summary, parser_tag) where parser_tag is "treesitter" on success
    or "generic" when we fell back to regex (so the hook can send the full file
    to the LLM instead of serving a useless regex output).
    """
    from tree_sitter_language_pack import get_language, get_parser
    from tree_sitter import Query, QueryCursor

    try:
        parser = get_parser(lang)
        language = get_language(lang)
    except Exception:
        return _summarize_generic(content, file_path), "generic"

    source_bytes = content.encode("utf-8", errors="replace")
    tree = parser.parse(source_bytes)

    query_src = _TS_QUERIES.get(lang)
    if not query_src:
        return _summarize_generic(content, file_path), "generic"

    try:
        query = Query(language, query_src)
    except Exception:
        return _summarize_generic(content, file_path), "generic"

    cursor = QueryCursor(query)
    captures = cursor.captures(tree.root_node)

    # --- collect imports ---
    import_nodes = captures.get("import", [])
    imports: list[str] = []
    for node in import_nodes[:8]:
        text = node.text.decode("utf-8", errors="replace").strip()
        # keep first line only to avoid multiline import noise
        first_line = text.splitlines()[0].strip()
        if first_line not in imports:
            imports.append(first_line)

    # --- collect classes with real spans from @class nodes ---
    class_nodes = captures.get("class", [])
    class_name_nodes = captures.get("class.name", [])
    # Map: name_node start_line → class end_line (use outer class node span)
    class_end_by_name_line: dict[int, int] = {}
    for cls_node in class_nodes:
        cls_start = cls_node.start_point[0] + 1
        cls_end = cls_node.end_point[0] + 1
        class_end_by_name_line[cls_start] = cls_end

    classes: list[tuple[str, int, int]] = []  # (name, start_line, end_line)
    seen_classes: set[str] = set()
    for node in class_name_nodes:
        name = node.text.decode("utf-8", errors="replace").strip()
        line = node.start_point[0] + 1
        if name not in seen_classes:
            seen_classes.add(name)
            # find the class node that starts at or just before this name line
            end = line + 200  # fallback
            for cls_start, cls_end in class_end_by_name_line.items():
                if cls_start <= line <= cls_end:
                    end = cls_end
                    break
            classes.append((name, line, end))

    # Build a line-indexed source for comment lookup
    source_lines = content.splitlines()

    # --- collect functions ---
    func_nodes_raw = captures.get("func", [])
    func_name_nodes = captures.get("func.name", [])
    func_param_nodes = captures.get("func.params", [])

    # build line→params map for lookup
    param_by_line: dict[int, str] = {}
    for node in func_param_nodes:
        line = node.start_point[0] + 1
        text = node.text.decode("utf-8", errors="replace").strip()
        param_by_line[line] = text

    # build outer-func-start-line → description from preceding comment
    func_desc_by_line: dict[int, str] = {}
    for node in func_nodes_raw:
        fstart = node.start_point[0] + 1
        desc = _preceding_comment(source_lines, fstart)
        if desc:
            func_desc_by_line[fstart] = desc

    funcs: list[tuple[str, str, int, str]] = []  # (name, params, line, desc)
    seen_funcs: set[str] = set()
    for node in func_name_nodes:
        name = node.text.decode("utf-8", errors="replace").strip()
        line = node.start_point[0] + 1
        params = param_by_line.get(line, "()")
        if len(params) > 60:
            params = params[:57] + "..."
        key = f"{name}:{line}"
        if key not in seen_funcs:
            seen_funcs.add(key)
            # the outer func node starts at line; look it up
            desc = func_desc_by_line.get(line, "")
            funcs.append((name, params, line, desc))

    # --- class descriptions from preceding comment ---
    class_desc_by_line: dict[int, str] = {}
    # Full first-line declaration (e.g. HCL: "resource "aws_s3_bucket" "assets"",
    # Go: "type Router struct", JS: "class Router extends EventEmitter").
    # Used as display name when richer than just the name node text.
    class_decl_by_line: dict[int, str] = {}
    for cls_node in class_nodes:
        cstart = cls_node.start_point[0] + 1
        desc = _preceding_comment(source_lines, cstart)
        if desc:
            class_desc_by_line[cstart] = desc
        first_line = cls_node.text.decode("utf-8", errors="replace").splitlines()[0]
        first_line = first_line.rstrip("{").strip()
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        class_decl_by_line[cstart] = first_line

    # --- format output ---
    parts: list[str] = []

    if imports:
        extra = f" +{len(import_nodes) - 8} more" if len(import_nodes) > 8 else ""
        parts.append(f"Imports: {', '.join(imports)}{extra}")

    for name, start, end in classes:
        desc = class_desc_by_line.get(start, "")
        desc_str = f" — {desc}" if desc else ""
        display = class_decl_by_line.get(start, f"class {name}")
        parts.append(f"\n{display}{desc_str} [line {start}]")
        for fname, fparams, fline, fdesc in funcs:
            if start <= fline <= end:
                fdesc_str = f" — {fdesc}" if fdesc else ""
                parts.append(f"  {fname}{fparams}{fdesc_str} [line {fline}]")

    # top-level functions (not inside any class range)
    class_ranges = [(start, end) for _, start, end in classes]
    top_funcs = [
        (fname, fparams, fline, fdesc) for fname, fparams, fline, fdesc in funcs
        if not any(start <= fline <= end for start, end in class_ranges)
    ]
    if top_funcs:
        if classes:
            parts.append("")
        for fname, fparams, fline, fdesc in top_funcs:
            fdesc_str = f" — {fdesc}" if fdesc else ""
            parts.append(f"{fname}{fparams}{fdesc_str} [line {fline}]")

    if parts:
        return "\n".join(parts), "treesitter"
    # Tree-sitter parsed OK but captured nothing (empty file, unsupported pattern)
    return _summarize_generic(content, file_path), "generic"


# ── Python (stdlib AST) ───────────────────────────────────────────────────────

def _file_ext(path: str) -> str:
    import os
    return os.path.splitext(path)[1].lower()


def _summarize_python(content: str, file_path: str) -> str:
    """AST-based: extract classes, functions, docstrings, signatures."""
    import ast

    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return _summarize_generic(content, file_path)

    parts: list[str] = []
    module_doc = ast.get_docstring(tree)
    if module_doc:
        first_line = module_doc.split("\n")[0].strip()
        parts.append(f"Module: {first_line}")

    imports: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append(f"{mod}.{alias.name}")

    if imports:
        shown = imports[:8]
        extra = f" +{len(imports)-8} more" if len(imports) > 8 else ""
        parts.append(f"Imports: {', '.join(shown)}{extra}")

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = [_ast_name(b) for b in node.bases]
            base_str = f"({', '.join(bases)})" if bases else ""
            doc = ast.get_docstring(node)
            doc_str = f" — {doc.split(chr(10))[0]}" if doc else ""
            parts.append(f"\nclass {node.name}{base_str}{doc_str}")

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig = _func_signature(item)
                    doc = ast.get_docstring(item)
                    doc_str = f" — {doc.split(chr(10))[0]}" if doc else ""
                    parts.append(f"  {sig}{doc_str} [line {item.lineno}]")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = _func_signature(node)
            doc = ast.get_docstring(node)
            doc_str = f" — {doc.split(chr(10))[0]}" if doc else ""
            parts.append(f"\n{sig}{doc_str} [line {node.lineno}]")

    top_assigns = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    top_assigns.append(target.id)
    if top_assigns:
        parts.append(f"\nConstants: {', '.join(top_assigns[:10])}")

    return "\n".join(parts) if parts else _summarize_generic(content, file_path)


def _func_signature(node) -> str:
    """Build 'def name(arg: type, ...) -> ret' from AST."""
    import ast
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args_parts = []
    for arg in node.args.args:
        ann = ""
        if arg.annotation:
            ann = f": {_ast_name(arg.annotation)}"
        args_parts.append(f"{arg.arg}{ann}")
    args_str = ", ".join(args_parts)
    ret = ""
    if node.returns:
        ret = f" -> {_ast_name(node.returns)}"
    return f"{prefix} {node.name}({args_str}){ret}"


def _ast_name(node) -> str:
    """Best-effort name from an AST node."""
    import ast
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_ast_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Subscript):
        return f"{_ast_name(node.value)}[{_ast_name(node.slice)}]"
    if isinstance(node, ast.Tuple):
        return ", ".join(_ast_name(e) for e in node.elts)
    return "..."


# ── JSON ──────────────────────────────────────────────────────────────────────

def _summarize_json(content: str, file_path: str) -> str:
    try:
        data = json.loads(content)
    except Exception:
        return _summarize_generic(content, file_path)
    return _describe_json(data, depth=0, max_depth=2)


def _describe_json(data, depth: int = 0, max_depth: int = 2) -> str:
    indent = "  " * depth
    if isinstance(data, dict):
        parts = [f"{indent}Object with {len(data)} keys:"]
        for key in list(data.keys())[:15]:
            val = data[key]
            if depth < max_depth and isinstance(val, (dict, list)):
                parts.append(f"{indent}  {key}: {_describe_json(val, depth+1, max_depth)}")
            else:
                parts.append(f"{indent}  {key}: {type(val).__name__}")
        if len(data) > 15:
            parts.append(f"{indent}  ... +{len(data)-15} more keys")
        return "\n".join(parts)
    elif isinstance(data, list):
        if not data:
            return "[]"
        return f"Array[{len(data)}] of {type(data[0]).__name__}"
    else:
        return f"{type(data).__name__}: {str(data)[:50]}"


# ── YAML ──────────────────────────────────────────────────────────────────────

def _summarize_yaml(content: str, file_path: str) -> str:
    try:
        import yaml
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return _describe_json(data, depth=0, max_depth=2)
        return f"YAML value: {type(data).__name__}"
    except Exception:
        return _summarize_generic(content, file_path)


# ── Markdown / text ───────────────────────────────────────────────────────────

def _summarize_text(content: str, file_path: str) -> str:
    lines = content.splitlines()
    parts: list[str] = []

    headings = [l for l in lines if l.startswith("#")]
    if headings:
        parts.append("Headings:")
        for h in headings[:15]:
            parts.append(f"  {h.strip()}")
        if len(headings) > 15:
            parts.append(f"  ... +{len(headings)-15} more sections")

    first_para = []
    for line in lines:
        if line.strip() and not line.startswith("#"):
            first_para.append(line.strip())
            if len(first_para) >= 3:
                break
    if first_para:
        parts.append("\nOpening: " + " ".join(first_para))

    return "\n".join(parts) if parts else _summarize_generic(content, file_path)


# ── Generic regex fallback ────────────────────────────────────────────────────

def measure_quality(content: str, file_path: str, summary: str) -> dict:
    """
    Measure summary quality by checking how many code symbols survive.
    Returns dict with total_symbols, preserved, quality_pct, missing.
    """
    symbol_patterns = [
        re.compile(r"(?:def|func|fn|function)\s+(\w+)\s*\(", re.MULTILINE),
        re.compile(r"(?:class|struct|interface|trait|enum)\s+(\w+)", re.MULTILINE),
        re.compile(r"(?:pub|export|public|private|protected)\s+(?:static\s+)?(?:\w+\s+)?(\w+)\s*\(", re.MULTILINE),
    ]

    symbols: set[str] = set()
    for pat in symbol_patterns:
        for m in pat.finditer(content):
            name = m.group(1)
            if len(name) > 1 and not name.isupper():
                symbols.add(name)

    if not symbols:
        return {"total_symbols": 0, "preserved": 0, "quality_pct": 100.0, "missing": []}

    summary_lower = summary.lower()
    preserved = [s for s in symbols if s.lower() in summary_lower]
    missing = [s for s in symbols if s.lower() not in summary_lower]
    pct = round(len(preserved) / len(symbols) * 100, 1) if symbols else 100.0

    return {
        "total_symbols": len(symbols),
        "preserved": len(preserved),
        "quality_pct": pct,
        "missing": sorted(missing),
    }


def _summarize_generic(content: str, file_path: str) -> str:
    """Language-agnostic regex fallback for unsupported file types."""
    parts: list[str] = []

    func_patterns = [
        re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|func|def)\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE),
        re.compile(r"^\s*(?:public|private|protected|internal)\s+(?:static\s+)?(?:async\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(?([^)]*)\)?\s*=>", re.MULTILINE),
    ]

    functions: list[str] = []
    for pat in func_patterns:
        for m in pat.finditer(content):
            name = m.group(1)
            args = m.group(2).strip() if m.group(2) else ""
            args_short = args[:60] + "..." if len(args) > 60 else args
            sig = f"{name}({args_short})"
            if sig not in functions:
                functions.append(sig)

    class_patterns = [
        re.compile(r"^\s*(?:pub\s+)?(?:abstract\s+)?(?:class|struct|interface|trait|enum|type)\s+(\w+)", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface|type|enum)\s+(\w+)", re.MULTILINE),
        re.compile(r"^\s*(?:public|private|protected|internal)\s+(?:abstract\s+)?(?:partial\s+)?(?:class|struct|interface|enum|record)\s+(\w+)", re.MULTILINE),
    ]

    classes: list[str] = []
    for pat in class_patterns:
        for m in pat.finditer(content):
            name = m.group(1)
            if name not in classes:
                classes.append(name)

    impl_re = re.compile(r"^\s*impl(?:<[^>]+>)?\s+(\w+)(?:\s+for\s+(\w+))?", re.MULTILINE)
    impls: list[str] = []
    for m in impl_re.finditer(content):
        desc = f"impl {m.group(1)} for {m.group(2)}" if m.group(2) else f"impl {m.group(1)}"
        if desc not in impls:
            impls.append(desc)

    import_patterns = [
        re.compile(r"^\s*(?:use|import|require|include|using)\s+(.+?)(?:;|\s*$)", re.MULTILINE),
        re.compile(r"^\s*from\s+(\S+)\s+import", re.MULTILINE),
    ]
    imports: list[str] = []
    for pat in import_patterns:
        for m in pat.finditer(content):
            val = m.group(1).strip()
            if val not in imports:
                imports.append(val)

    if imports:
        shown = imports[:8]
        extra = f" +{len(imports)-8} more" if len(imports) > 8 else ""
        parts.append(f"Imports: {', '.join(shown)}{extra}")

    if classes:
        for c in classes[:15]:
            parts.append(f"\nclass/struct/type {c}")

    if impls:
        for i in impls[:10]:
            parts.append(f"  {i}")

    if functions:
        if not classes:
            parts.append("")
        for f in functions[:25]:
            parts.append(f"  {f}")

    if not parts:
        lines = content.splitlines()
        n = len(lines)
        head = "\n".join(lines[:10])
        tail = "\n".join(lines[-5:]) if n > 15 else ""
        middle = f"\n... ({n - 15} lines omitted) ...\n" if n > 15 else ""
        return f"{head}{middle}{tail}"

    return "\n".join(parts)
