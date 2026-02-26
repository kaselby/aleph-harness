# Subagent Spawning — Design Doc

Task 20: Multi-agent wiring.

## Goal

Aleph can spawn subagents from within a session to parallelize work. Subagents are full Aleph instances running in tmux, visible to the user, and communicating with the parent via the existing messaging system.

## What Exists

- **CLI flags:** `--id`, `--prompt`, `--parent`, `--depth`, `--project` all accepted and wired through to config.
- **Initial prompt:** TUI auto-sends `--prompt` on session start via `send_initial()`.
- **Messaging:** `send_message` MCP tool writes to recipient's inbox. `inbox_check` PostToolUse hook surfaces unread messages as notifications after every tool call.
- **System prompt awareness:** When `--parent` is set, the session context includes parent ID and depth.

## What's Missing

### 1. Spawn mechanism

The parent agent needs a way to launch a subagent in a tmux window. Options:

- **Shell script in `tools/`** (recommended). `spawn-agent` handles tmux window creation, argument escaping, ID generation. Called via Bash. Simple, inspectable, easy to iterate on.
- **MCP tool.** More integrated but adds complexity to `tools.py` and needs async process management.
- **Raw Bash.** The parent constructs the `tmux new-window` command manually. Fragile, error-prone with quoting.

**Recommendation:** `tools/spawn-agent` shell script. It's the right level of abstraction — encapsulates the tmux mechanics while keeping the parent in control of the prompt and parameters.

Sketch:
```bash
spawn-agent --id worker-1 \
  --prompt "Implement the auth module. See task 2 in TODO.yml." \
  --project /path/to/project \
  --parent aleph-52951c52
```

The script would:
- Generate an ID if not provided
- Create a tmux window named after the agent ID
- Launch `aleph` with all the flags
- Print the agent ID back to the parent

### 2. Tmux topology

**Prerequisite:** tmux must be installed. Add to setup/bootstrap checklist.

**Options:**
- **New window in existing session** — simplest if the user is already in tmux. `tmux new-window -n <agent-id> 'aleph ...'`
- **New named session** — works if the user isn't in tmux. `tmux new-session -d -s <agent-id> 'aleph ...'`

**Recommendation:** Try new window first, fall back to new session if not inside tmux. The script can detect this with `$TMUX`.

### 3. Completion signaling

After the initial prompt's turn completes, the subagent drops into the interactive input loop. Neither the parent nor the user knows it's "done."

**Options:**
- **Prompt convention.** The parent's prompt includes "When you've completed this task, send a message to [parent-id] with a summary of what you did." This is the simplest approach and requires zero harness changes. The subagent stays alive for the user to interact with via tmux.
- **`--oneshot` flag.** The TUI exits after the initial prompt completes (skip the input loop). Clean, but the user can't interact with the subagent after. Could still fire the session summary in the finally block.
- **Both.** `--oneshot` for fire-and-forget tasks, persistent for tasks the user might want to supervise.

**Recommendation:** Start with prompt convention only. Add `--oneshot` later if needed. Persistent sessions are more flexible and let the user course-correct via tmux.

### 4. `send_message` missing `from` field

The message metadata has `summary`, `priority`, and `timestamp` but no sender. When a subagent messages the parent, the parent can't tell who sent it without reading the body.

**Fix:** Add `from` to the `send_message` tool schema and write it into the frontmatter. The harness knows its own agent ID — could auto-populate, but the MCP tool doesn't currently have access to session state. Simplest fix: add `from` as an explicit parameter to the tool (the agent knows its own ID from the system prompt).

### 5. Depth limit enforcement

`--depth` is tracked but not enforced. A runaway agent could spawn indefinitely.

**Fix:** Add a `max_depth` to config (default 3?). The harness refuses to start if depth exceeds it. Or: the `spawn-agent` script increments depth and refuses to spawn beyond the limit. Harness-level enforcement is safer since it can't be bypassed.

### 6. Prompt composition

What should the parent include in the subagent's `--prompt`? The subagent gets the same system prompt (ALEPH.md) and memory context, so it has general orientation. But it needs task-specific context.

**Convention options:**
- **Task board reference.** "Claim and complete task 2.2 in /path/to/project/TODO.yml. Send a completion message to [parent-id] when done."
- **Full briefing.** The parent composes a detailed prompt with all relevant context. More tokens but self-contained.
- **Hybrid.** Brief description + pointer to task board for details.

**Recommendation:** Hybrid. The prompt should be self-contained enough that the subagent can start working without multiple rounds of file reading, but reference the task board for detailed spec. Convention to document in ALEPH.md or docs/.

## Implementation Plan

Ordered by dependency:

1. **Install tmux** — prerequisite.
2. **Fix `send_message` `from` field** — 5 min. Add parameter to tool, write to frontmatter.
3. **Build `tools/spawn-agent`** — shell script, ~30 min. Handles tmux, ID generation, argument passing.
4. **Add depth enforcement** — small change in harness `start()` or config validation.
5. **Document conventions** — prompt composition, completion signaling, in docs/ or ALEPH.md.
6. **Test end-to-end** — spawn a subagent, have it do a small task, message back.

Steps 2-4 are probably an hour of implementation. Step 5 is iterative — conventions will evolve with use. Step 6 is where we'll discover what we got wrong.

## Open Questions

- **Shared file access.** Multiple agents writing to TODO.yml or memory files will eventually conflict. Not a blocker for v1 (sequential tasks, low contention) but needs addressing for parallel work. See backlog item on concurrent file access.
- **Error handling.** What happens if the subagent crashes? The tmux window closes and the parent never gets a message. Could monitor with `tmux list-windows` or add a heartbeat mechanism. Overkill for v1.
- **Cost visibility.** Subagents consume API tokens independently. No aggregation or budget enforcement. See backlog item on rate limiting.
- **Agent discovery.** The parent currently needs to know the subagent's ID to message it. A `list-agents` tool (task 13.1) that scans active tmux windows or inbox directories would help.
