# Agent Framework — Implementation Notes

Working document for brainstorming and nailing down implementation details.

**Legend:**
- ✅ **Settled** — agreed upon, not up for debate
- 💡 **Proposed** — suggestion from Claude, not yet agreed
- ❓ **Open** — genuinely undecided, needs discussion

---

## Settled Decisions


### Runtime
- Python harness built on the **Claude Agent SDK**
- SDK wraps Claude Code CLI as subprocess — handles auth, tool loop, context management
- Runs on **Max subscription**, no API keys needed
- Each agent is one harness process managing one Claude Code subprocess

### Agent Identity
- Wrapper process launches the agent with an **identifier** — user-supplied or auto-generated
- ID is used for message routing, filesystem paths, and session management
- Agent ID is the *session* identity — the agent's persistent identity lives in memory (see philosophy.md)

### SDK + Custom TUI (not CLI tmux wrapper)

**Decision:** We use the SDK for full programmatic control and build our own TUI, rather than wrapping Claude Code's interactive CLI in tmux.

**Why:** There's no way to get both full system prompt replacement AND Claude Code's interactive TUI — they're mutually exclusive. `--system-prompt` only works in `-p` (non-interactive) mode. In interactive mode, we can only append via CLAUDE.md. Since a custom system prompt is central to Aleph's identity and in-process Python hooks are our messaging backbone, the CLI wrapper approach compromises too many core requirements.

**What we give up:** Claude Code's polished interactive TUI. We have to build our own.

**What we get:**
- Full system prompt replacement (not appending to Claude Code's opaque default)
- In-process Python hooks (not shell scripts) — faster, more capable, direct access to harness state
- Programmatic tool restriction via `allowed_tools`
- Programmatic session management (resume, fork)
- Complete control over the agent lifecycle

**TUI approach:** prompt_toolkit Application in scrollback mode (`full_screen=False`). The SDK provides structured typed messages (AssistantMessage, ToolUseBlock, ToolResultMessage, etc.) — we render them. MVP needs: chat display, streaming responses, tool call display, user input, status toolbar.

**Output architecture:**
- **Styled output** (tool calls, results, headers, status): `print_formatted_text(HTML(...))` — prints to scrollback above the Application layout, handles its own `run_in_terminal` coordination.
- **Streaming text** (token-by-token model output): Rendered in a layout Window (`FormattedTextControl`), committed to scrollback via `print_formatted_text` when streaming completes.
- **`patch_stdout()`** kept as safety net for stray `print()` calls from libraries, but not used for our output.

**Multi-agent composition:** tmux panes, not in-TUI tabs. Each Aleph agent is a separate process in its own terminal. tmux handles side-by-side layout and switching. The TUI is a single-agent interface; composition is tmux's job. This aligns with the "everything is files and bash" philosophy.

**Frameworks considered and rejected:**

*Textual:* Tried first (commit `3cf739c`). Full-screen alternate-screen-buffer app — no native text selection or scrolling, which are fundamental UX properties of a terminal tool. Textual's widget framework isn't needed when tmux handles multi-agent composition.

*Rich + prompt_toolkit:* Rich for styled output, prompt_toolkit Application for input/keybindings. These two libraries conflict over terminal control. `patch_stdout`'s `StdoutProxy` bridges them, but it garbles Rich's ANSI output: `Vt100Output.write()` literally does `data.replace("\x1b", "?")`, stripping all color codes. The `raw=True` mode passes ANSI through but `run_in_terminal` redraws the layout on top of partial-line output, causing text to disappear. Two UI libraries fighting over the terminal is architecturally unsound — dropped Rich in favor of prompt_toolkit's native `HTML` formatter.

**Reference implementations (MIT-licensed, studied for patterns):**
- OpenCode — most complete open-source alternative, client/server architecture. Blocked by Anthropic for OAuth spoofing. Not viable as integration target — tightly coupled to its own backend.
- Crystal — Electron-based, embeds Claude Code TUI

**OAuth safety:** The SDK spawns the actual `claude` CLI binary as a subprocess, so requests come from Claude Code itself from Anthropic's perspective. No risk of the OAuth blocks that hit tools like OpenCode.

### Tools
- **Bash** is the central tool — primary interface for everything
- **Read, Write, Edit** retained as built-ins (genuinely better than shell equivalents for the model)
- A small number of additional tools kept for convenience where they're clearly better than shell scripts (e.g. **WebSearch, WebFetch**)
- A small number of framework-specific tools may be needed (e.g. messaging-related)
- Everything else is shell scripts invoked via Bash
- `allowed_tools` on the SDK enforces this restriction

### Tool and Skill Discovery
- `tools/` directory for shell-script tools, `skills/` directory for skills following the standard agent skills protocol
- `tools/REGISTRY.md` — agent-maintained index of all tools with one-line descriptions
- System prompt tells the agent where to find the registry; contents are NOT injected at boot (read on demand)
- Skills use the standard SKILL.md protocol for progressive disclosure — read the SKILL.md to understand the skill before using it
- When the agent builds or modifies a tool, it updates the registry

### Inter-Agent Communication
- Filesystem-based inboxes: agents have inbox directories in the shared filesystem
- Messages consist of **summary**, **metadata/header**, and **body** (exact header schema TBD)
- **PostToolUse hooks** check for unread messages after every tool call and inject summaries as `additionalContext`
- Agent reads full message content from filesystem when it wants the details
- Some form of automatic cleanup or archiving on old messages
- Sending messages handled via a tool (built-in or shell script — TBD)

### Subagent Spawning
- Agents spawn subagents by **invoking the harness command through Bash** — it's just a process invocation, nothing special
- User connects to spawned agents via the TUI (multi-agent tab switching)

### Build Our Own Infrastructure
We're building custom implementations for messaging, task tracking, and memory rather than integrating existing community projects (MCP Agent Mail, Beads/Gastown, claude-mem). Rationale:
- Our push-based messaging via PostToolUse hooks is fundamentally different from the pull models used by existing tools
- Existing projects are built for general-purpose Claude Code usage, not a custom harness with our specific architecture
- They bring heavy dependencies and opinionated designs that would fight our "everything is files and bash" philosophy
- We want self-modifiable infrastructure — the agent should be able to improve its own tools, which is harder when they're external projects

**Key patterns we're borrowing:**
- From **claude-mem**: Progressive disclosure for memory (lightweight index at boot, full retrieval on demand). Structured observation types. Hybrid search concepts.
- From **Beads**: Task tracking as orientation ("what should I do next?" not just "what do I know?"). Dependency DAGs with a `ready` query. Priority levels. The philosophy that agents should orient themselves from the work graph at session start.
- From **MCP Agent Mail**: Message acknowledgment (sender knows recipient processed the message). Thread IDs linking tasks to conversations. Advisory file reservations for conflict prevention in swarm scenarios. Message schema elements (subject, importance, cc).

We should design our systems with schemas that don't diverge wildly from these projects, so we could integrate or migrate later if our homebrew versions hit walls.

### Memory Architecture (v1)

**Two-file hierarchy at both global and project levels, replacing Claude Code's built-in systems:**

**System prompt / CLAUDE.md equivalent — stable reference:**
- **Global:** Harness instructions, agent identity, operating procedures, messaging protocol, tool conventions. Written initially by the user. The agent can propose modifications over time but this evolves slowly and under more supervision.
- **Project-level:** Factual overview of the project — architecture, key patterns, conventions, structure. The stuff a new session needs to orient itself.

**Memory.md equivalent — living impressions:**
- **Global:** Learned preferences, observations about the user, things figured out over time, notes to future self. Agent-managed — the agent writes, curates, and prunes this.
- **Project-level:** Observations from working on the project — "this API is flaky," "test suite takes 20 minutes," "tried X, didn't work because Y." Experiential knowledge that makes the agent smarter about this project. Works alongside the task tracking layer.

**Key decisions:**
- Disable Claude Code's built-in auto-memory (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`) — we own the entire memory layer
- Use SDK's `setting_sources=["project"]` to load CLAUDE.md files from the workspace, but with our own files, not Claude Code's defaults (*User comment: need to look into this and see if this loads the global claude.md. Also we might use AGENTS.md or some other format rather than CLAUDE.md precisely*)
- Memory is primarily **agent-managed** (the agent decides what to remember and curates over time), not automatic capture
- Automatic backstops at session handoff: forced "persist what matters" step when sessions end
- No conversational memory for v1 — if the agent writes important things to memory and handoff docs, conversation content is already captured where it matters
- Periodic **cleanup/integration sessions** where agents are spawned to consolidate memories, clean up files, and maintain the knowledge base (details TBD)

**Potential future additions (not v1):**
- Larger global knowledge store (zettelkasten-style or similar) for overflow when the boot file outgrows context budget
- Vector DB or embedding-based retrieval for the knowledge store
- More sophisticated automatic capture / observation pipeline (claude-mem-style)
- These are upgrade paths, not requirements


## Deferred

### DSP and Sandboxing
Needs further thought. `--dangerously-skip-permissions` is required for autonomous operation, but the safety implications and sandboxing approach are not yet decided.

---

## Proposed / Under Discussion

Everything below is brainstorming — nothing here is settled.

### ❓ Hooks Architecture

The PostToolUse hook for message notification is settled. The rest of the hooks story is open:

**Stop hook for catching messages at turn end:**
💡 *Claude's suggestion:* When an agent finishes its turn, a Stop hook checks the inbox one last time. If messages are pending, exit code 2 + stderr message forces the agent to continue and process them. This prevents messages from being silently dropped.

**PreToolUse for interrupts and guardrails:**
💡 *Claude's suggestion:* PreToolUse could serve two purposes: (1) high-priority message interrupts — deny the pending tool call and force the agent to process an urgent message first, and (2) safety guardrails — inspect Bash commands, deny dangerous operations, detect subagent spawning and enforce depth limits. These are two very different concerns and would need clean separation in the hook code.

**SessionStart hook:**
💡 *Claude's suggestion:* Could inject boot sequence context (read memory, check task board) or detect post-compaction restarts and re-inject critical context. But the alternative is just putting that in the system prompt or having the agent's first action be reading its boot docs. Not clear a hook is better than a prompt instruction here.

**Periodic reminders via hooks:**
Hooks should periodically nudge the agent to: update memories, review/improve its own tools and prompts, and other self-improvement behaviors. Exact mechanism TBD — could be a counter on PostToolUse that injects a reminder every N tool calls, or time-based.

### ❓ Message Format Details

The existence of summary/metadata/body is settled. The specifics are not.

💡 *Claude's suggestion:* Markdown files with YAML frontmatter:
```
/shared/inbox/agent-1/msg-001.md
```
```yaml
---
from: agent-2
timestamp: 2026-02-24T10:30:00
summary: "Review complete, 3 issues found in auth.py"
priority: normal
---

Full message body here with all the details...
```
The summary field is what the PostToolUse hook injects. Priority could affect delivery mechanism (PostToolUse notification vs PreToolUse interrupt). Markdown body means agents write messages the same way they write everything else.

### ❓ Message Cleanup
When do messages get cleared from an inbox? Options:
- After the agent reads the full message
- After explicit acknowledgment
- Automatic archival after N hours or after being read
- Never (append-only, but move read messages to an archive subfolder)

### ❓ Broadcasting / Group Communication
Point-to-point is straightforward. One-to-many is less clear:
- Write to each recipient's inbox individually
- Shared bulletin board / blackboard that all agents can watch
- Both for different use cases

### ❓ Session Handoffs

Philosophy is settled: don't fight compaction, persist and hand off. Protocol is not.

Questions:
- What does a handoff document contain? (current state, what's done, what's next, critical context)
- Where does it live?
- How does the agent know it's time to hand off? (self-awareness? hook that monitors turns/tokens? hard cutoff?)
- How does the new session find and continue from the handoff?

### ❓ Subagent Lifecycle
- Blocking (parent waits for result) vs async (parent continues working) — probably both should be possible
- How subagents report results back — inbox message? stdout capture? shared results location?

### ❓ Recursion Control
💡 *Claude's suggestion:* `--parent` and `--depth` flags on the harness command. PreToolUse hooks detect harness invocations in Bash commands and enforce depth limits. This gives hierarchical orchestration with built-in depth limiting through existing primitives.

### ❓ Task Coordination
No settled design yet. Starting simple for v1.

For v1: probably something like a `TODO.yml` per project with a defined task schema — status, assignee, priority, description, subtasks. Agents read it to orient ("what should I do next?") and update it as they work. Inspired by Beads' orientation-first philosophy.

Longer term: may need something more sophisticated to handle concurrent access (multiple agents claiming/updating tasks simultaneously). File locking, atomic operations, or a lightweight DB. But start simple.

### ❓ Standardized Project Documentation
The user has been using a **design.md / discussion.md** split for projects:
- **design.md** — settled decisions only
- **discussion.md** — iteration process, tradeoffs, rejected ideas, Claude's opinions

This pattern (or something like it) could become a standardized format that agents automatically maintain. Hooks could remind agents to update project docs as they work — similar to memory reminders. The documentation format itself is something the agent could evolve over time.

Open questions: is the two-doc split the right structure? Should there be more docs (e.g. a changelog, an architecture overview)? How much should be standardized vs project-specific?

### ❓ Filesystem Layout — Agent Home Directory
The agent needs a global home directory. Rough thinking on what lives there:

```
~/.agent/                         # or /shared/, or wherever — location TBD
  system-prompt.md                # Global CLAUDE.md equivalent
  memory.md                       # Global memory (agent-managed)
  config.yaml                     # Harness configuration
  
  harness/                        # Harness source code (agent can modify)
    harness.py
    hooks/
    
  tools/                          # Shell-script tools and skills
    
  projects/                       # Project registry
    registry.yaml                 # Index of known projects
    <project-name>/               # Per-project metadata (OR stored in project dirs — TBD)
      memory.md
      
  venv/                           # Canonical Python virtual environment
                                  # Auto-activated for Python work
                                  # Agents can use local project venvs when needed
  
  inbox/                          # Per-agent message inboxes
    <agent-id>/
    
  scratch/                        # Temporary working space
```

**Open questions:**
- Where exactly does this live on the filesystem?
- Project memory files: stored here in a central registry, or stored in the actual project directories? Tradeoff: central registry is easier for the agent to find everything; project-local is more natural and keeps project dirs self-contained.
- Canonical venv: how does auto-loading work? Probably a shell wrapper or hook that activates it. Need an escape hatch for projects with their own venvs.
- Should the harness source live here (agent-modifiable) or in a separate repo?

### ❓ Observability / User Connection to Agent Sessions
Settled: the user needs to be able to connect to any running agent session, including ones spawned by other agents. Not settled: how.

Options:
- **tmux** — each agent in a tmux window, user switches between them. Simple, proven, but ties us to tmux.
- **Custom solution** — harness maintains a registry of running agents with connection info. More flexible but more to build.

💡 *Claude's suggestion:* tmux is probably right for v1. It's already there, it works, and agents running in tmux windows gives you observation and intervention for free. A registry of active agents (even just a directory of PID files or a simple JSON file) could complement tmux for programmatic discovery.

### ❓ Logging
💡 *Claude's suggestion:* Structured logs beyond tmux output would be valuable for debugging and reviewing what agents did. Could be as simple as teeing output to log files per agent.

### ❓ System Prompt Design
The system prompt is critical — it's where the agent learns its identity, capabilities, and operating procedures. Not designing this until more implementation details are settled, but it will need to cover:
- Agent identity and relationship to persistent memory
- Available tools and conventions
- Messaging protocol
- Memory conventions
- Handoff protocol
- Self-improvement guidelines
- Project documentation conventions

---



## Parking Lot

## V1 MVP

The goal of v1 is to get a working harness that can be used and then improved by Aleph itself. It doesn't need to be complete — it needs to be functional enough to pair with and to start self-improving.

### Technical Reference — SDK Integration

These details are critical for implementing the harness correctly.

**System prompt: two injection channels.**
Aleph's context gets shaped by two separate mechanisms:
1. **SDK `system_prompt` parameter** — this is where our global system prompt goes. The SDK defaults to an empty system prompt (NOT Claude Code's prompt), giving us full control. Load `~/.aleph/ALEPH.md` and pass it here.
2. **`setting_sources=["project"]`** — this tells the SDK to load CLAUDE.md files from the filesystem hierarchy (project root, subdirectories, etc.). This is how project-level context gets injected. When `--project` is specified, the harness should set the working directory to the project root so the SDK picks up any CLAUDE.md there.

These are complementary: the global system prompt defines Aleph's identity and operating procedures, while project CLAUDE.md files provide project-specific context. Both are loaded at session start.

**Disabling Claude Code's built-in memory.**
Set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in the environment before launching the SDK. This prevents Claude Code from writing to `~/.claude/projects/<project>/memory/MEMORY.md` — we manage all memory ourselves via `~/.aleph/`. Without this, Claude Code's auto-memory and Aleph's memory would conflict.

**What `setting_sources=["project"]` does and doesn't load:**
- ✅ Loads CLAUDE.md files from the filesystem hierarchy
- ❓ May also load MEMORY.md automatically (undocumented — needs testing during implementation)
- ❌ Does NOT carry over session memory from previous Claude Code sessions (SDK creates fresh sessions)

If MEMORY.md auto-loading can't be disabled independently, the `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` env var should handle it.

**SDK system prompt options (for reference):**
- `system_prompt=""` — empty (SDK default). We don't want this.
- `system_prompt="<our prompt>"` — custom string. **This is what we use.**
- `system_prompt={"type": "preset", "preset": "claude_code"}` — Claude Code's full built-in prompt. We don't want this either.
- `append_system_prompt="<extra>"` — appends to whatever base prompt is used. Could be useful for injecting agent ID and session-specific context on top of the base prompt.

### Technical Reference — Hook Output Format

PostToolUse hooks return JSON. The `additionalContext` field is what gets injected into Claude's context.

**Message delivery hook — when messages exist:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "[Message from agent-2]: Review complete, 3 issues found. Full details at ~/.aleph/inbox/agent-1/msg-004.md\n[Message from agent-3]: Build passed. Full details at ~/.aleph/inbox/agent-1/msg-005.md"
  }
}
```

**When no messages / no reminder due:**
```json
{}
```
Returning empty object means no context injection — zero overhead.

**Periodic reminder hook — when reminder is due:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "[System reminder]: Consider updating ~/.aleph/memory.md with any important observations from this session."
  }
}
```

**Other hook types (for future reference):**
- **PreToolUse** can return `additionalContext` (same format) plus `decision: "allow" | "deny" | "modify"` to control the tool call
- **Stop hook** uses exit code 2 + stderr message to force the agent to continue. Does NOT use the JSON format — it writes to stderr and the CLI re-injects that as a new prompt.
- **Known bug:** PostToolUse may not fire reliably for MCP tool calls specifically (GitHub issue #24788, Feb 2026). Monitor this.

### Technical Reference — SDK Message Types

The SDK streams structured typed messages that the TUI needs to render. Key types:
- **AssistantMessage** — Claude's text responses (streamed token-by-token)
- **ToolUseBlock** — tool invocations (tool name, parameters)
- **ToolResultMessage** — tool outputs (stdout, stderr, exit codes)

The TUI renders these as they arrive. Full message type documentation should be consulted from the SDK docs during implementation.

### Build List

**1. Directory scaffolding**
Create the `~/.aleph/` structure. At minimum:
```
~/.aleph/
  ALEPH.md                   # System prompt (agent identity + instructions)
  memory.md                  # Global memory (agent-managed)
  config.yaml                # Harness configuration (minimal for v1)
  tools/                     # Shell-script tools
    REGISTRY.md              # Agent-maintained tool index
  skills/                    # Standard skills protocol
  docs/                      # Reference docs (message formats, TODO schema, etc.)
  inbox/                     # Per-agent message inboxes
    <agent-id>/
  scratch/                   # Temporary working space
  venv/                      # Canonical Python virtual environment
  harness/
    harness.py
    hooks/
    tui/                     # TUI source code
```
A setup script that creates this and initializes the venv.

**2. Harness CLI + SDK integration**
The `aleph` command. Python, wraps the Claude Agent SDK. Core responsibilities:
- Accept flags: `--id` (or auto-generate), `--prompt`, `--project`, `--parent`, `--depth`
- Load system prompt from `~/.aleph/ALEPH.md`
- Configure SDK: tool restrictions (`allowed_tools`), hooks, system prompt, disable auto-memory
- Use `setting_sources=["project"]` when a project is specified to load project CLAUDE.md
- Set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in environment
- Auto-activate `~/.aleph/venv/`
- Launch the TUI (interactive) or run headless (autonomous/spawned agents)
- Basic invocations: `aleph` for a general session, `aleph --project ~/myproject` for project-scoped, `aleph --id worker-1 --prompt "do X"` for an autonomous task

**3. TUI (prompt_toolkit, scrollback mode)**
Custom terminal interface since the SDK is headless (no Claude Code TUI). Single-agent interface; multi-agent composition via tmux. MVP needs:
- Chat display: render streamed AssistantMessage, ToolUseBlock, ToolResultMessage
- User input: prompt_toolkit Application with keybindings (Enter to submit, Escape to interrupt)
- Streaming: token-by-token display via layout Window, committed to scrollback on completion
- Status toolbar: agent state, token counts, keybinding hints

Not needed for MVP: diff view, syntax highlighting, rewind/fork, fancy styling. Get the basics working, polish later. The agent can improve its own TUI over time.

**4. PostToolUse hook — message delivery**
In-process Python callback:
- Check `~/.aleph/inbox/<agent-id>/` for unread messages after every tool call
- If messages exist, inject summaries as `additionalContext` (see hook output format above)
- If no messages, return `{}` (no context cost)

**5. PostToolUse hook — periodic reminders**
Counter-based or time-based nudges injected via the same hook:
- Remind agent to update memory when appropriate
- Frequency should be tunable (and eventually self-tunable by the agent)
- Combined with the message delivery hook — one PostToolUse callback handles both

**6. System prompt (first draft)**
Written by the user. Working draft at `system prompt v2.md`. Must cover:
- Agent identity (you are Aleph)
- Personality (agent-editable)
- Where memory lives and how to use it (`~/.aleph/memory.md`, project-level `memory.md`)
- Available tools and the bash-first philosophy
- Tool/skill discovery (REGISTRY.md, SKILL.md protocol)
- How messaging works (inboxes, notifications, sending via tool)
- How to spawn subagents (`aleph` command in bash)
- TODO.yml task board conventions (format, how to claim tasks)
- Self-improvement expectations (update memory, improve tools, propose prompt changes)
- Communication style guidance
- Handoff protocol (brief for v1, formalize later)

**7. TODO.yml task board pattern**
A simple per-project task file with a defined minimal schema. Something like:
```yaml
tasks:
  - id: 1
    description: "Refactor auth module"
    status: open          # open | claimed | done | blocked
    assignee: null        # agent ID when claimed
    priority: high        # high | medium | low
    subtasks:
      - id: 1.1
        description: "Extract token validation"
        status: open
        assignee: null
```
Agents read this to orient, claim tasks by writing their ID to `assignee` and setting status to `claimed`, and update status as they work. System prompt explains the conventions.

**8. Canonical Python venv**
- Created by the scaffolding script
- Lives at `~/.aleph/venv/`
- Auto-activated by the harness at launch
- Agent can use project-local venvs when a project requires one

**9. A few starter tools**
Shell scripts in `~/.aleph/tools/` to demonstrate the pattern and bootstrap useful capabilities. Examples:
- `send-message.sh` — write a message to another agent's inbox with proper format
- `list-agents.sh` — show running agents
- Anything else that emerges during development

### Explicitly Deferred

- Session handoff protocol formalization (system prompt tells agent to write handoff docs — no hook enforcement)
- PreToolUse guardrails / sandboxing
- Stop hook for catching messages at turn end
- Message cleanup / archiving
- Sophisticated task board concurrency handling
- Project documentation conventions (let this emerge from usage)
- Logging infrastructure
- TUI polish: diff view, syntax highlighting, rewind/fork, styling
- Multi-agent TUI tabs (using tmux for composition instead)

---



## Parking Lot


## Smoke Test Findings (2026-02-24)

First successful boot. Key issues discovered:

1. **`allowed_tools` may not work as a whitelist** when `bypassPermissions` is set. Aleph used Bash despite it not being in allowed_tools. Needs SDK source investigation.
2. **Claude Code injects its own context** regardless of custom `system_prompt`. Aleph saw tools (Glob, Grep) and skills (keybindings-help) from the CC runtime that weren't in our config. The SDK's `system_prompt` parameter may not fully replace CC's default prompt — CC may inject additional tool schemas and skill headers separately.
3. **`StreamEvent` + `AssistantMessage` duplication** — with `include_partial_messages=True`, text content arrives twice. Display layer must handle this.
4. **Must unset `CLAUDECODE` env var** to launch from within a CC session. Fixed in harness.py.
5. **`StreamEvent` is not exported** from `claude_agent_sdk` public API — must import from `claude_agent_sdk.types`.



## Parking Lot

Things that will matter eventually but don't need answers now:
- Concurrent write handling for memory and shared files
- Exact sandbox implementation
- Whether the harness needs a persistent daemon component
- GitHub integration
- CLI UX for how the user enters the system
- Rate limiting / cost guardrails for swarm mode
- Agent failure recovery
- Glob/Grep: keep as built-in tools or let agents use shell equivalents?
- Zettelkasten-style knowledge store as overflow for global memory
- Vector DB / embedding-based retrieval for larger knowledge stores

