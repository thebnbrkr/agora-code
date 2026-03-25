---
name: agora-code
description: Persistent memory + AST summarization — injects session context, runs tree-sitter AST analysis on large files, and enforces token-efficient reading
keep-coding-instructions: true
---

You have persistent memory and AST-based file summarization via agora-code. Follow these rules every session:

1. **At session start** — run `agora-code inject` to load prior context before doing anything else.
2. **Before reading any file over ~50 lines** — run `agora-code summarize <file>` first. This runs tree-sitter AST analysis (170+ languages) and returns a structured outline of all functions, classes, and symbols with line numbers — a 75%+ token reduction vs reading the full file. Never use the Read tool on a large file without summarizing first.
3. **When done with a task** — run `agora-code complete --summary "..."` to archive the session.

`agora-code summarize` uses tree-sitter to parse the file and extract real AST structure — it is not a cache or a grep. It gives you the actual shape of the code with precise line numbers so you can then read only the specific functions you need.

The hooks handle indexing, recall, and checkpointing automatically. Your only job is inject at start, summarize before large reads, and complete when done.
