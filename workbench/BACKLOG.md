# Aleph — Backlog

Deferred decisions, open questions, and things to revisit.

## Deferred Decisions

### Permissions model
`bypassPermissions` is the end goal for autonomous operation, but we need a path to get there safely. The SDK exposes `can_use_tool` as a programmatic callback for permission decisions — this is the right mechanism for us, not Claude Code's interactive permission modes (which need a terminal we don't have). Need to decide: what's the default policy? Always allow? Log and allow? Prompt for Bash? Per-project trust levels?

Now understood: `bypassPermissions` and `allowed_tools` are independent — bypass removes permission prompts, allowed_tools gates execution. The `tools` parameter controls schema visibility (what the model sees). We use `tools=BASE_TOOLS` for visibility, `allowed_tools` for execution gating when needed, and `bypassPermissions` for autonomous operation.

**Revisit when:** Implementing the `can_use_tool` callback (task 12).

### Sandboxing
Separate from permissions — even with a good permission callback, Aleph runs as the user's process with full filesystem/network access. Options: Docker containers, macOS sandbox profiles, SDK's built-in `SandboxSettings`, or just careful permission policies.

**Revisit when:** Before giving agents unsupervised write access to anything important.

### setting_sources=["project"]
Now understood: the SDK passes `--setting-sources ""` by default (when `setting_sources=None`), which prevents loading any CLAUDE.md/MEMORY.md files. We confirmed this works — no settings files leak into Aleph's context. We could use `setting_sources=["project"]` to load project CLAUDE.md files if desired.

**Revisit when:** We need project-level context injection beyond what the system prompt provides.

### Headless / autonomous mode
For now all agents are interactive (ClaudeSDKClient). Headless mode (query() for fire-and-forget tasks) is a natural addition but not needed yet.

**Revisit when:** We want to spawn subagents that don't need a TUI tab.

### Session handoff protocol
Philosophy is settled (persist and hand off, don't fight compaction). Protocol is not — what goes in the handoff doc, where it lives, how the new session finds it.

**Revisit when:** Sessions start hitting context limits in practice.

### Message cleanup / archiving
Read messages get marked (.read file) but never deleted. Eventually inboxes will accumulate. Need a cleanup strategy — time-based, count-based, or explicit agent-managed archival.

**Revisit when:** Inbox directories get unwieldy.

### Concurrent file access
Multiple agents writing to shared files (todo.yaml, memory.md) will eventually conflict. File locking, atomic writes, or a lightweight coordination layer may be needed.

**Revisit when:** Running multi-agent swarms against shared state.

### Persistent Bash tool (self-improvement candidate)
Claude Code's built-in Bash tool resets shell state between invocations — env vars, venv activations, aliases all gone. Working directory persists, nothing else. For v1 we pre-set the venv PATH/VIRTUAL_ENV in the SDK env dict, which covers the main case. But since Bash is the foundation of everything Aleph does, a custom MCP Bash tool with a persistent shell (via pty/pexpect) may be needed. Good candidate for Aleph's first self-improvement project — let it discover the friction and build its own fix.

**Revisit when:** Aleph encounters friction from stateless Bash, or as a deliberate self-improvement exercise.

### ~~allowed_tools behavior~~ RESOLVED
`allowed_tools` (--allowedTools) gates execution only, not schema visibility. The `tools` parameter (--tools) controls which tool schemas the model sees. Both are independent of `bypassPermissions`. Fix applied: harness now sets `tools=BASE_TOOLS`. See diagnostic results in `~/.aleph/scratch/context-dump.md`.

### ~~Claude Code context leaking through SDK~~ RESOLVED
`--system-prompt` with a string fully replaces CC's system prompt. CC prepends one line: "You are a Claude agent, built on Anthropic's Claude Agent SDK." The tool schema injection was caused by not setting the `tools` parameter (CC defaults to all 18 tools). Parent session skills leaked via `<system-reminder>` tags — controlled by setting `tools`. `--setting-sources ""` correctly prevents CLAUDE.md loading.

### Glob/Grep as built-in tools
Now that we control tool schemas via `tools=BASE_TOOLS`, we can add Glob/Grep by including them in BASE_TOOLS in config.py. Currently using shell equivalents via Bash. May be worth adding if the model performs significantly better with the dedicated tools.

**Revisit when:** Observing Aleph's file search behavior in practice.

### Programming skill — domain-specific instructions
Several pieces from Claude Code's system prompt were identified as belonging in the programming skill rather than the base prompt: "executing actions with care" (destructive operation awareness), "doing tasks" (understand code before modifying, don't over-engineer). The programming skill has the core pieces but could be expanded with more of this guidance.

**Revisit when:** After observing Aleph's coding behavior in practice — add what's missing, skip what's not needed.

### Skill activation — deny message UX
The PreToolUse hook denies the Read and injects content as system context. The model sees a "denied" tool result. The system prompt explains this is expected, but it's somewhat hacky. May want a cleaner mechanism if the SDK adds native skill support or if we find a way to allow the Read but suppress duplicate content.

**Revisit when:** If the model gets confused by the denied Read, or if the SDK adds better primitives.

### `claude config` subcommands not usable from inside a session
Running `claude config list` or `claude config get model` from within an Aleph session (via Bash tool) doesn't return CLI config output — it returns what looks like a model response, as if the input is being interpreted as a prompt rather than dispatched to the config subcommand. Setting `CLAUDECODE=` (empty) doesn't help. This matters because it would let us auto-discover the default model (and other settings) without hardcoding. The `CLAUDECODE` env var nesting check might not be the only guard, or the `config` subcommand may have its own issues when invoked in this context.

**Revisit when:** We need runtime introspection of Claude Code settings, or when debugging CLI integration issues.

## Future Ideas

- Persistent daemon component for the harness
- GitHub integration
- Rate limiting / cost guardrails for swarm mode
- Agent failure recovery
- Zettelkasten-style knowledge store for memory overflow
- Vector DB / embedding-based retrieval
- TUI: diff view, syntax highlighting, rewind/fork
- `/command` shortcuts in TUI for skill activation
- Stop hook for catching unread messages at turn end
- PreCompact hook for persisting critical context before compaction
