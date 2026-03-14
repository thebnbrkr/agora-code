"""
summarizer.py — File content summarization for token-efficient context injection.

The main in-session token reducer: instead of dumping a raw 500+ line file
into the context window, extract structural info (classes, functions,
signatures) and return a compressed summary.

Pipeline:
  Python → stdlib AST (classes, functions, docstrings, signatures)
  JS/TS  → regex (exports, classes, function signatures)
  Ruby   → regex (classes, modules, methods, blocks)
  Java   → regex (classes, interfaces, methods, annotations)
  Go     → regex (types, funcs, interfaces, structs)
  PHP    → regex (classes, functions, namespaces)
  JSON   → top-level keys + structure overview
  YAML   → top-level keys + structure overview
  Markdown/text → headings + opening paragraph
  Other  → head + tail + line count

Token estimation:
  Uses tiktoken (BPE) if installed for accurate counts,
  falls back to a word-based heuristic (~1.3 tokens/word).
"""

from __future__ import annotations

import json
import re
from typing import Optional


# --------------------------------------------------------------------------- #
#  Token estimation                                                            #
# --------------------------------------------------------------------------- #

_tiktoken_enc = None  # lazy singleton


def _get_tiktoken_encoder():
    global _tiktoken_enc
    if _tiktoken_enc is None:
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        except (ImportError, Exception):
            _tiktoken_enc = False  # sentinel: tried and failed
    return _tiktoken_enc if _tiktoken_enc is not False else None


def estimate_tokens(text: str) -> int:
    """
    Estimate token count. Uses tiktoken (BPE, cl100k_base) if installed —
    accurate for GPT-4/Claude class models. Falls back to a word-based
    heuristic (~1.3 tokens per word) which is better than chars//4.
    """
    if not text:
        return 1  # preserve backwards compat: min 1

    enc = _get_tiktoken_encoder()
    if enc is not None:
        return len(enc.encode(text))

    # Fallback: word-based heuristic — English BPE averages ~1.3 tokens/word
    words = len(text.split())
    return max(1, int(words * 1.3))


# --------------------------------------------------------------------------- #
#  Configuration                                                               #
# --------------------------------------------------------------------------- #

FILE_SUMMARY_TOKEN_BUDGET = 500
FILE_SIZE_THRESHOLD = 500  # lines — files smaller than this pass through


# --------------------------------------------------------------------------- #
#  Public API                                                                  #
# --------------------------------------------------------------------------- #

def summarize_file(
    file_path: str,
    content: str,
    max_tokens: int = FILE_SUMMARY_TOKEN_BUDGET,
) -> Optional[str]:
    """
    Summarize file content for context injection instead of raw content.

    Returns None if the file is small enough to pass through uncompressed.
    Returns a compressed structural summary otherwise.
    """
    lines = content.splitlines()
    line_count = len(lines)

    if line_count <= FILE_SIZE_THRESHOLD:
        return None

    ext = _file_ext(file_path)

    if ext == ".py":
        summary = _summarize_python(content, file_path)
    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs"):
        summary = _summarize_js_ts(content, file_path)
    elif ext in (".rb", ".rake"):
        summary = _summarize_ruby(content, file_path)
    elif ext == ".java":
        summary = _summarize_java(content, file_path)
    elif ext == ".go":
        summary = _summarize_go(content, file_path)
    elif ext == ".php":
        summary = _summarize_php(content, file_path)
    elif ext == ".json":
        summary = _summarize_json(content, file_path)
    elif ext in (".yaml", ".yml"):
        summary = _summarize_yaml(content, file_path)
    elif ext in (".md", ".rst", ".txt"):
        summary = _summarize_text(content, file_path)
    else:
        summary = _summarize_generic(content, file_path)

    header = f"[agora-code summary of {file_path} — {line_count} lines]\n"
    result = header + summary

    if estimate_tokens(result) > max_tokens:
        budget_chars = max_tokens * 4
        result = result[:budget_chars] + "\n... (truncated to token budget)"

    return result


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _file_ext(path: str) -> str:
    import os
    return os.path.splitext(path)[1].lower()


# --------------------------------------------------------------------------- #
#  Python (AST-based)                                                          #
# --------------------------------------------------------------------------- #

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
                    parts.append(f"  {sig}")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = _func_signature(node)
            doc = ast.get_docstring(node)
            doc_str = f" — {doc.split(chr(10))[0]}" if doc else ""
            parts.append(f"\n{sig}{doc_str}")

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


# --------------------------------------------------------------------------- #
#  JS/TS (regex-based)                                                         #
# --------------------------------------------------------------------------- #

def _summarize_js_ts(content: str, file_path: str) -> str:
    """Regex-based: extract exports, classes, function signatures."""
    parts: list[str] = []

    import_re = re.compile(
        r"^(?:import\s+.+?from\s+['\"](.+?)['\"]"
        r"|const\s+.+?=\s*require\(['\"](.+?)['\"]\))",
        re.MULTILINE,
    )
    imports = [m.group(1) or m.group(2) for m in import_re.finditer(content)][:8]
    if imports:
        parts.append(f"Imports: {', '.join(imports)}")

    class_re = re.compile(r"(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?")
    for m in class_re.finditer(content):
        ext = f"({m.group(2)})" if m.group(2) else ""
        parts.append(f"\nclass {m.group(1)}{ext}")

    fn_re = re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)"
        r"|(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\(?([^)]*)\)?\s*=>"
    )
    for m in fn_re.finditer(content):
        name = m.group(1) or m.group(3)
        args = m.group(2) or m.group(4) or ""
        args_short = args[:60] + "..." if len(args) > 60 else args
        parts.append(f"function {name}({args_short})")

    component_re = re.compile(r"(?:export\s+default\s+)?(?:const|function)\s+([A-Z]\w+)")
    seen = set()
    for m in component_re.finditer(content):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            parts.append(f"component {name}")

    return "\n".join(parts) if parts else _summarize_generic(content, file_path)


# --------------------------------------------------------------------------- #
#  Ruby (regex-based)                                                          #
# --------------------------------------------------------------------------- #

def _summarize_ruby(content: str, file_path: str) -> str:
    parts: list[str] = []

    require_re = re.compile(r"^require\s+['\"](.+?)['\"]", re.MULTILINE)
    requires = [m.group(1) for m in require_re.finditer(content)][:8]
    if requires:
        parts.append(f"Requires: {', '.join(requires)}")

    module_re = re.compile(r"^\s*module\s+(\w+(?:::\w+)*)", re.MULTILINE)
    for m in module_re.finditer(content):
        parts.append(f"\nmodule {m.group(1)}")

    class_re = re.compile(r"^\s*class\s+(\w+)(?:\s*<\s*(\S+))?", re.MULTILINE)
    for m in class_re.finditer(content):
        parent = f"({m.group(2)})" if m.group(2) else ""
        parts.append(f"\nclass {m.group(1)}{parent}")

    def_re = re.compile(r"^\s*def\s+(self\.)?(\w+[?!=]?)\s*(?:\(([^)]*)\))?", re.MULTILINE)
    for m in def_re.finditer(content):
        prefix = "self." if m.group(1) else ""
        args = m.group(3) or ""
        parts.append(f"  def {prefix}{m.group(2)}({args})")

    return "\n".join(parts) if parts else _summarize_generic(content, file_path)


# --------------------------------------------------------------------------- #
#  Java (regex-based)                                                          #
# --------------------------------------------------------------------------- #

def _summarize_java(content: str, file_path: str) -> str:
    parts: list[str] = []

    pkg_re = re.compile(r"^package\s+([\w.]+);", re.MULTILINE)
    m = pkg_re.search(content)
    if m:
        parts.append(f"Package: {m.group(1)}")

    import_re = re.compile(r"^import\s+([\w.*]+);", re.MULTILINE)
    imports = [m.group(1) for m in import_re.finditer(content)][:10]
    if imports:
        parts.append(f"Imports: {', '.join(imports)}")

    class_re = re.compile(
        r"(?:public|private|protected)?\s*(?:abstract|final)?\s*"
        r"(?:class|interface|enum)\s+(\w+)(?:\s+extends\s+(\w+))?"
        r"(?:\s+implements\s+([\w,\s]+))?",
    )
    for m in class_re.finditer(content):
        ext = f" extends {m.group(2)}" if m.group(2) else ""
        impl = f" implements {m.group(3).strip()}" if m.group(3) else ""
        parts.append(f"\nclass {m.group(1)}{ext}{impl}")

    method_re = re.compile(
        r"(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?"
        r"(\w+(?:<[\w<>,\s]+>)?)\s+(\w+)\s*\(([^)]*)\)",
    )
    for m in method_re.finditer(content):
        ret, name, args = m.group(1), m.group(2), m.group(3)
        args_short = args[:60] + "..." if len(args) > 60 else args
        parts.append(f"  {ret} {name}({args_short})")

    return "\n".join(parts) if parts else _summarize_generic(content, file_path)


# --------------------------------------------------------------------------- #
#  Go (regex-based)                                                            #
# --------------------------------------------------------------------------- #

def _summarize_go(content: str, file_path: str) -> str:
    parts: list[str] = []

    pkg_re = re.compile(r"^package\s+(\w+)", re.MULTILINE)
    m = pkg_re.search(content)
    if m:
        parts.append(f"Package: {m.group(1)}")

    type_re = re.compile(r"^type\s+(\w+)\s+(struct|interface)\b", re.MULTILINE)
    for m in type_re.finditer(content):
        parts.append(f"\ntype {m.group(1)} {m.group(2)}")

    func_re = re.compile(
        r"^func\s+(?:\((\w+)\s+\*?(\w+)\)\s+)?(\w+)\s*\(([^)]*)\)\s*([\w()*,\s]*)?",
        re.MULTILINE,
    )
    for m in func_re.finditer(content):
        receiver = f"({m.group(2)}) " if m.group(2) else ""
        name = m.group(3)
        args = m.group(4) or ""
        ret = m.group(5).strip() if m.group(5) else ""
        args_short = args[:60] + "..." if len(args) > 60 else args
        ret_str = f" {ret}" if ret else ""
        parts.append(f"func {receiver}{name}({args_short}){ret_str}")

    return "\n".join(parts) if parts else _summarize_generic(content, file_path)


# --------------------------------------------------------------------------- #
#  PHP (regex-based)                                                           #
# --------------------------------------------------------------------------- #

def _summarize_php(content: str, file_path: str) -> str:
    parts: list[str] = []

    ns_re = re.compile(r"^namespace\s+([\w\\]+);", re.MULTILINE)
    m = ns_re.search(content)
    if m:
        parts.append(f"Namespace: {m.group(1)}")

    use_re = re.compile(r"^use\s+([\w\\]+)(?:\s+as\s+(\w+))?;", re.MULTILINE)
    uses = [m.group(2) or m.group(1).split("\\")[-1] for m in use_re.finditer(content)][:8]
    if uses:
        parts.append(f"Uses: {', '.join(uses)}")

    class_re = re.compile(
        r"(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?"
        r"(?:\s+implements\s+([\w,\s\\]+))?",
    )
    for m in class_re.finditer(content):
        ext = f"({m.group(2)})" if m.group(2) else ""
        parts.append(f"\nclass {m.group(1)}{ext}")

    fn_re = re.compile(
        r"(?:public|private|protected|static)\s+function\s+(\w+)\s*\(([^)]*)\)"
        r"|^function\s+(\w+)\s*\(([^)]*)\)",
        re.MULTILINE,
    )
    for m in fn_re.finditer(content):
        name = m.group(1) or m.group(3)
        args = m.group(2) or m.group(4) or ""
        args_short = args[:60] + "..." if len(args) > 60 else args
        parts.append(f"  function {name}({args_short})")

    return "\n".join(parts) if parts else _summarize_generic(content, file_path)


# --------------------------------------------------------------------------- #
#  JSON                                                                        #
# --------------------------------------------------------------------------- #

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
        sample_type = type(data[0]).__name__
        return f"Array[{len(data)}] of {sample_type}"
    else:
        return f"{type(data).__name__}: {str(data)[:50]}"


# --------------------------------------------------------------------------- #
#  YAML                                                                        #
# --------------------------------------------------------------------------- #

def _summarize_yaml(content: str, file_path: str) -> str:
    try:
        import yaml
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return _describe_json(data, depth=0, max_depth=2)
        return f"YAML value: {type(data).__name__}"
    except Exception:
        return _summarize_generic(content, file_path)


# --------------------------------------------------------------------------- #
#  Markdown / text                                                             #
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
#  Generic fallback                                                            #
# --------------------------------------------------------------------------- #

def measure_quality(content: str, file_path: str, summary: str) -> dict:
    """
    Measure summary quality by checking how many code symbols survive.

    Returns dict with total_symbols, preserved, quality_pct, missing.
    Works for any language via regex symbol extraction.
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
    """Language-agnostic fallback: regex for common patterns across all languages."""
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
        target = m.group(2) or m.group(1)
        trait_name = m.group(1) if m.group(2) else None
        desc = f"impl {m.group(1)} for {m.group(2)}" if trait_name else f"impl {m.group(1)}"
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
