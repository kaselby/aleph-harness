---
name: collaboration
description: >
  Manages multi-agent collaboration — spawning peer Aleph instances, dividing
  work, and coordinating via messages and the task board. Activate when launching
  other agents, delegating tasks in parallel, or working alongside other running
  Aleph instances.
---

# Collaboration

## Launching Peers

```bash
aleph --detach --ephemeral [--id <name>] [--model <model>] [--project <path>]
```

All aleph instances run inside tmux. `--detach` prevents auto-attaching to the new session — the command prints the agent ID and returns immediately. Always launch with `--ephemeral` — spawned agents should not write session summaries or update persistent memory. (Omit `--ephemeral` only if you specifically need the peer to persist its own session history.)

Use `--id` to give the agent a descriptive name that reflects its task — this is what shows up in `tmux list-sessions`.

After launching, send a message with instructions and your callback ID. Messages can be sent immediately — they're written to the inbox directory and will be picked up when the agent starts, no need to wait.

```bash
aleph --detach --ephemeral --id auth-worker
```
```
send_message(to="auth-worker", from="<your-id>", summary="Task assignment", body="...", priority="normal")
```

This pattern accomplishes two things: the peer knows who launched it and where to send results.

### tmux Management

```bash
tmux list-sessions                      # list running agents
tmux capture-pane -t <agent-id> -p      # peek at output without attaching
tmux kill-session -t <agent-id>         # kill a specific agent
tmux kill-server                        # kill all agents
```

Treat other alephs as collaborators, not disposable processes. Don't kill sessions preemptively — let peers finish their work and exit naturally. If cleanup is needed, check `tmux list-sessions` and `capture-pane` first.

## Protocol

1. **Use messages to coordinate.** Send task assignments, status updates, and results via `send_message`. Include your agent ID so the recipient can reply.

2. **Use TODO.yml to claim tasks.** Set your agent ID as assignee and status to `claimed` before starting work. This prevents two agents from working on the same task.

3. **Continuously update TODO.yml with progress.** Update task status as you work (`in_progress`, `blocked`, `done`). Other agents and the user rely on this to understand the state of work.
