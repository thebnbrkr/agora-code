# Roadmap

What's planned. Core hook coverage for Claude Code, Cursor, and Gemini CLI is already shipped. This is what comes next.

---

## Distribution

**Expand IDE and agent support**
Add hook support for Cline, Amazon Kiro, and other agents/IDEs as their hook systems mature. The pattern is consistent across editors — each needs a `.{editor}/hooks/` directory and config file.

**Automate editor setup**
`install-hooks --cursor` and `install-hooks --gemini` to copy hook files automatically instead of requiring manual directory copying.

---

## Infrastructure

**Cloud-synced DB**
Host the SQLite database (or a sync layer on top of it) so that session context, learnings, and AST summaries follow you across machines. Same checkpoint and context injection behaviour whether you're on your laptop or a remote dev box.

---

## UI

**Local memory viewer**
A localhost UI for exploring what's stored — sessions, learnings, file change history, symbol index. Visualize code changes over time, browse stored learnings, and do everything the CLI listing commands do but interactively. The worker API is already at localhost so the surface area is small.

---

## Code intelligence

**Symbol usage explorer**
Something like Cmd+B / Go to usages in IntelliJ — given a function or class, show everywhere it's called across the indexed codebase. The symbol index already stores line numbers and code blocks; this would be a query layer on top.

---

## Advanced hooks (Gemini CLI)

| Hook | What it does |
|---|---|
| `BeforeToolSelection` | Filter available tools before the model picks — reduces tool-choice noise |
| `AfterAgent` | Fire after model responds, can `deny` to force a retry if the model ignored context |
| `BeforeModel` | Modify the LLM request before sending — temperature, model switching, synthetic responses for cached answers |
