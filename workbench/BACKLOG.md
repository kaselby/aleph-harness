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

### ~~Session handoff protocol~~ RESOLVED
Implemented. Agent writes to `~/.aleph/memory/handoff.md`, harness injects it into the system prompt on next startup and deletes the file. Documentation in ALEPH.md. Session recaps are also generated automatically from today's session files via Haiku.

### Message cleanup / archiving
Read messages get marked (.read file) but never deleted. Eventually inboxes will accumulate. Need a cleanup strategy — time-based, count-based, or explicit agent-managed archival.

**Revisit when:** Inbox directories get unwieldy.

### Concurrent file access
Multiple agents writing to shared files (todo.yaml, memory.md) will eventually conflict. File locking, atomic writes, or a lightweight coordination layer may be needed.

**Revisit when:** Running multi-agent swarms against shared state.

### ~~Persistent Bash tool~~ RESOLVED
Built as custom MCP tool (`mcp__aleph__Bash`). Persistent shell subprocess with sentinel-based output capture. Replaces built-in Bash via `disallowed_tools`. See `src/aleph/shell.py`, `tools.py`.

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

### `claude config` subcommands not usable from inside a session
Running `claude config list` or `claude config get model` from within an Aleph session (via Bash tool) doesn't return CLI config output — it returns what looks like a model response, as if the input is being interpreted as a prompt rather than dispatched to the config subcommand. Setting `CLAUDECODE=` (empty) doesn't help. This matters because it would let us auto-discover the default model (and other settings) without hardcoding. The `CLAUDECODE` env var nesting check might not be the only guard, or the `config` subcommand may have its own issues when invoked in this context.

**Revisit when:** We need runtime introspection of Claude Code settings, or when debugging CLI integration issues.

### Periodic maintenance / git push
The `~/.aleph` repo auto-commits locally at session end but doesn't push to the remote (`kaselby-aleph/aleph`). A periodic maintenance step should handle: git push, inbox cleanup (prune stale .read files and dead agent inboxes), and session history compaction. Could be a cron job, a harness startup hook, or part of the future manager daemon.

Also deferred: **per-agent git worktrees** — each running agent gets its own worktree/branch, merges to main at session end. Eliminates index lock contention entirely but requires significant plumbing (redirect `config.home`, handle merge conflicts, maintain inter-agent visibility). Worth revisiting if concurrent write contention becomes a real problem.

**Revisit when:** Multiple agents are regularly causing git lock contention, or when building the manager daemon.

### `run_in_background` for MCP Bash tool
The custom MCP Bash tool doesn't support background execution. The built-in Bash tool had a `run_in_background` parameter; our MCP equivalent should support something similar. Implementation: spawn the command in a background subshell within the persistent shell, return immediately with a job ID, provide a way to check on / retrieve output later.

**Revisit when:** A real task needs long-running background commands (builds, servers, watchers).

## Future Ideas

- ~~Persistent daemon component for the harness~~ → promoted to deferred decision below
- GitHub integration
- Rate limiting / cost guardrails for swarm mode
- Agent failure recovery
- Zettelkasten-style knowledge store for memory overflow
- Vector DB / embedding-based retrieval
- TUI: message timestamps / elapsed time between messages (subtle header or separator showing when each message was sent and how long the gap was)
- TUI: diff view, syntax highlighting, rewind/fork
- TUI: live markdown rendering during streaming (study `markdown-it-py` as parser — it's what Textual uses under the hood. Feed token stream into a FormattedText builder. Current approach: plain streaming → markdown-rendered scrollback at commit time.)
- TUI: keep context usage visible in status bar while tool calls are running (currently only updates between turns)
- Tool framework: per-tool budget limits (some APIs have free monthly credits — e.g. Exa, Tavily — so a blanket spend cap doesn't reflect actual cost structure. Allow setting per-tool thresholds or marking tools as "free tier" up to N calls)
- `/command` shortcuts in TUI for skill activation
- Stop hook for catching unread messages at turn end
- PreCompact hook for persisting critical context before compaction
- Unify Aleph messaging with comm-channel (`~/Git/claude-tools/comm-channel`). Currently two separate systems: Aleph uses `~/.aleph/inbox/` with markdown frontmatter, comm-channel uses `~/.claude-comm/` with JSON. Comm-channel has nicer primitives (atomic writes, PID-based liveness, GC, name resolution). Long-term, one messaging layer for both Aleph agents and Claude sessions.

### Exit-step permission granularity
Currently we force YOLO mode for the entire exit sequence (summary, archival, git commit). This is fine for now since the exit step only writes to `~/.aleph/`, but it's a blunt instrument. If we later add exit-step behaviors that touch project files or run Bash commands, we'll want finer-grained control — e.g. allow writes only to `~/.aleph/memory/` and `~/.aleph/logs/`, or a dedicated "exit" permission mode that's more permissive than default but less than YOLO.

**Revisit when:** Exit steps expand beyond memory/summary writes, or if we add project-level cleanup to the exit sequence.

### Session summary robustness
The exit summary fires as a final turn after the user quits, but if the session is already at or near the context limit, the summary turn itself may fail (context overflow, truncated output, or the SDK refusing to accept another message). The `except Exception: pass` in the TUI finally block means this fails silently — no summary gets written and there's no indication it was lost. Options: detect remaining context headroom before attempting the summary and skip/warn if too low; write a partial summary from harness-side metadata (agent ID, timestamp, duration) even if the model turn fails; trigger the summary earlier (e.g. via reminder hook when context crosses a threshold). May also just be an acceptable loss — sessions near context limit have presumably already been persisting state incrementally.

**Revisit when:** Observing lost session summaries in practice, or when building the PreCompact hook.

### Skill-aware hooks / session state
Hooks currently have no awareness of what skills are active. The motivating case: when the programming skill is active and a TODO.yml exists in the project, the reminder hook should nudge the agent to update it. But skills follow a standardized protocol and shouldn't carry hook definitions — that's harness-specific behavior.

Proposed approach: the harness tracks which skills have been activated during the session (it already processes `activate_skill` calls via the PostToolUse hook). Expose this as session state that hooks can query. Hooks then make their own decisions based on what's active — e.g. the reminder hook checks for `programming` in the active set and adjusts its message. Skills stay clean; hooks are skill-aware without skills being hook-aware.

Open questions: how to define "project directory" for TODO.yml detection when the agent isn't bound to a single working directory; whether this state-tracking belongs in the harness, the hook system, or a shared session context object.

**Revisit when:** Building context-aware reminders, or when the hook system needs more sophistication generally.

### Runtime agent renaming
Allow agents to rename themselves at runtime to reflect what they're working on (visible in `tmux list-sessions`). Challenge: agent ID is used for inbox routing, tmux session name, session summaries, and message `from` fields — all need to stay in sync. Would require an MCP tool that atomically updates harness internal state, renames the inbox directory, and renames the tmux session. For now, use meaningful `--id` values at launch.

**Revisit when:** Launch-time naming proves insufficient for multi-agent discoverability.

### Aleph manager daemon
A persistent process that serves as the central authority for agent lifecycle. Currently there's no way to know which agents are alive — inbox directories persist after sessions end, tmux detection only works for tmux-spawned agents, and there's no cleanup of stale state.

A manager daemon would handle:
- **Agent registry.** Agents register on startup, deregister on shutdown. The manager is the source of truth for who's running. Solves `list-agents` (task 13.1) cleanly.
- **Inbox cleanup.** Periodically garbage-collect read messages and inboxes for dead agents. See existing backlog item on message cleanup/archiving.
- **Health monitoring.** Detect crashed agents (registered but no heartbeat). Notify parent agents or the user.
- **Cost tracking.** Aggregate token usage across agents for budget enforcement. See existing backlog item on rate limiting.
- **Process management.** Could subsume the tmux spawn logic — the manager launches agents and tracks their PIDs/tmux windows.

Implementation options: a simple Unix socket server in Python, a lightweight HTTP API, or even just a well-structured PID/lock file system. The socket server is probably the right level — agents connect on startup, the manager tracks them, agents query it for discovery.

This is a significant piece of infrastructure and shouldn't be built until multi-agent workflows are actually running and we've felt the pain of not having it.

**Revisit when:** Multi-agent spawning is working (task 20) and agent discovery / cleanup becomes a real friction point.
