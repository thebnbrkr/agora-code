# Future Hook Opportunities

Hooks we can wire up later once the core flow is stable. Not critical for launch — nice-to-haves that increase token savings and session continuity.

## Per-Turn Context Injection

| Hook | IDE | What it does | Why it's useful |
|---|---|---|---|
| `BeforeAgent` | Gemini CLI | Fires before every turn, can inject `additionalContext` | Inject fresh session state every turn, not just on start. Keeps agent aware of session even after compaction. |
| `UserPromptSubmit` | Claude Code | Fires when user submits a prompt | Enrich prompts with relevant learnings before the model sees them. Could do BM25 search against user's query and append matching learnings. |

## Subagent Awareness

| Hook | IDE | What it does | Why it's useful |
|---|---|---|---|
| `SubagentStart` | Claude Code | Fires when a subagent spawns | Inject session context into Claude subagents / Cursor Task agents so they don't start blind. Currently subagents have zero session awareness. |

## Response Validation

| Hook | IDE | What it does | Why it's useful |
|---|---|---|---|
| `AfterAgent` | Gemini CLI | Fires after model responds, can `deny` to force retry | Auto-retry if the model ignored session context or made a decision that contradicts stored learnings. |

## Tool Filtering

| Hook | IDE | What it does | Why it's useful |
|---|---|---|---|
| `BeforeToolSelection` | Gemini CLI | Filters available tools before LLM picks | Hide tools the model doesn't need to reduce tool-choice noise and prevent unnecessary token burn on tool descriptions. |

## Model-Level Control

| Hook | IDE | What it does | Why it's useful |
|---|---|---|---|
| `BeforeModel` | Gemini CLI | Modify the actual LLM request before sending | Could inject a synthetic response to skip the LLM call entirely for cached/known answers. Also: adjust temperature, switch models mid-session. |

## Shell Output Summarization

| Hook | IDE | What it does | Why it's useful |
|---|---|---|---|
| `afterShellExecution` | Cursor | Fires after shell commands complete | Summarize large shell output (git log, npm install, test runs) the same way we summarize file reads. Prevents 50KB of test output from flooding context. |

## Error Tracking

| Hook | IDE | What it does | Why it's useful |
|---|---|---|---|
| `PostToolUseFailure` | Claude Code | Fires after a tool call fails | Track errors in session memory for debugging continuity. If the same error recurs, surface the prior resolution from learnings. |

## Priority Order

1. `afterShellExecution` (Cursor) — easy win, reuse existing summarizer
2. `BeforeAgent` (Gemini) / `UserPromptSubmit` (Claude Code) — per-turn context is the biggest continuity improvement
3. `SubagentStart` (Claude Code) — subagent blindness is a real pain point
4. `PostToolUseFailure` (Claude Code) — error memory prevents repeated failures
5. `BeforeToolSelection` (Gemini) — nice optimization, lower priority
6. `AfterAgent` (Gemini) — response validation is powerful but complex to get right
7. `BeforeModel` (Gemini) — synthetic responses require careful design
