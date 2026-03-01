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
aleph --detach --ephemeral [--id <name>] [--mode <mode>] [--model <model>] [--project <path>]
```

All aleph instances run inside tmux. `--detach` prevents auto-attaching to the new session — the command prints the agent ID and returns immediately. Always launch with `--ephemeral` — spawned agents should not write session summaries or update persistent memory. (Omit `--ephemeral` only if you specifically need the peer to persist its own session history.)

Use `--mode yolo` for agents that should run fully autonomously without permission prompts. Use `--mode safe` for agents that need human approval on every tool call. Default mode (`--mode default`) uses the normal permission rules. Most spawned workers should use `--mode yolo` — they'll get stuck on permission prompts otherwise since nobody is watching their tmux session.

Use `--id` to give the agent a descriptive name that reflects its task — this is what shows up in `tmux list-sessions`.

After launching, send a message with instructions and your callback ID. Messages can be sent immediately — they're written to the inbox directory and will be picked up when the agent starts, no need to wait.

```bash
aleph --detach --ephemeral --mode yolo --id auth-worker
```
```
message(action="send", to="auth-worker", summary="Task assignment", body="...", priority="normal")
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

## Channels

Channels let multiple agents share findings in a broadcast fashion. Every subscriber receives all messages sent to the channel.

```
message(action="subscribe", channel="exploration")       # join a channel
message(action="send", channel="exploration",            # broadcast to all subscribers
        summary="Found the entry point", body="...")
message(action="unsubscribe", channel="exploration")     # leave when done
```

Use channels when you're collaborating with other agents on the same problem. Subscribe at the start of your task, share findings as you discover them, and build on what others report. Channel messages arrive in your inbox just like direct messages — the `channel` field in the frontmatter tells you where it came from.

## Task Coordination

Use the `task` tool for all TODO.yml operations — it handles file locking to prevent concurrent edit conflicts between agents.

```bash
task list                          # show all tasks with status
task claim 2.1                     # claim a task (sets assignee + in-progress)
task status 2.1 done               # update status (open/in-progress/done/blocked)
task release 2.1                   # release a claimed task
```

The tool uses `ALEPH_AGENT_ID` (set automatically by the harness) as the assignee. If another agent has already claimed a task, the claim will fail with an error — no silent overwrites.

If the project has no TODO.yml, use messages for task assignment instead.

## Protocol

1. **Use messages to coordinate.** Send task assignments, status updates, and results via the `message` tool. For group work, use channels so everyone stays informed without explicit routing.

2. **Use the `task` tool to claim tasks.** Run `task claim <id>` before starting work. This atomically sets your agent ID as assignee and prevents two agents from working on the same task.

3. **Update task status as you work.** Run `task status <id> done` (or `blocked`, etc.) so other agents and the user can see progress.
