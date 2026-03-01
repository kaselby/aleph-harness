# Channel Dashboard — Design Notes

## Decision

Proposal A (dashboard launcher) with proper TUI matching the main aleph interface. Option to add auto-spawning (Proposal C) later as a separable feature.

Command: `aleph dashboard` (or `aleph channels`).

## What It Does

Creates a tmux session with one window per active channel, each running a channel viewer TUI. User can Ctrl-B between windows to follow different channels. Optionally includes windows for agent session views.

## Channel Viewer TUI

Each window runs a standalone channel viewer process. This is the core component.

**Display:**
- Message history scrolling up (tail mode)
- Each message: timestamp, sender (colored by role), summary or body
- Summaries by default, some way to expand to full body
- Styling should match the main aleph TUI (see style map below)

**Input:**
- Input line at bottom for sending messages to the channel
- Messages attributed to user identity (--as flag or ALEPH_USER env var, default "kira")
- Sends via the same channel broadcast mechanism agents use

**Framework:** prompt_toolkit (same as main TUI). NOT Textual. The main TUI uses prompt_toolkit with full_screen=False, printing styled output to scrollback. The channel viewer could follow the same pattern, or go full_screen=True for a more traditional chat UI — depends on whether scrollback matters.

**Style map** (from main TUI, extend as needed):
```python
TUI_STYLE = Style.from_dict({
    "user": "ansicyan bold",
    "assistant": "ansigreen bold",
    "tool": "ansiyellow bold",
    "dim": "#888888",
    "dim-i": "#888888 italic",
    "err": "ansired",
    "text": "#cccccc",
    "text-heading": "#e0e0e0 bold",
    "agent-msg": "ansimagenta",
    "agent-msg-b": "ansimagenta bold",
})
```

For channel messages, suggest:
- Agent messages: `ansimagenta` (matches existing agent-msg)
- User messages: `ansicyan` (matches existing user)
- System/channel messages: `dim`
- Timestamps: `dim`

## Dashboard Launcher

`aleph dashboard [--as kira] [channels...]`

- If channels specified, open those. Otherwise, scan `~/.aleph/channels/` for any with recent history.
- Create tmux session `aleph-dashboard`
- One window per channel, each running the channel viewer
- Optionally add windows for running agent sessions (just tmux links to existing sessions)

## Data Source

Channel history lives at `~/.aleph/channels/<name>/history.jsonl`. One JSON line per message:
```json
{"ts": "...", "from": "agent-id", "summary": "...", "body": "...", "priority": "normal"}
```

To send a message, use the existing message tool infrastructure — need to figure out whether the viewer calls the MCP tool or writes directly to the JSONL + delivers to subscribers.

## Open Questions

- **Full-screen vs scrollback?** Main TUI uses scrollback mode. Chat UI might work better full-screen with a message pane and input pane. But then you lose native scrollback/selection.
- **How does the viewer send messages?** It's not an agent, so it can't use the MCP message tool. Options: (a) write directly to JSONL and handle delivery, (b) shell out to a CLI command, (c) import the messaging module directly.
- **Agent session views** — just `tmux link-window` to existing agent sessions? Or something fancier?
- **Auto-spawning (Proposal C)** — deferred. If we want it later, add a filesystem watcher that creates new tmux windows when new channel dirs appear.

## Implementation Order

1. Channel viewer script (the TUI component) — this is the bulk of the work
2. Dashboard launcher (tmux orchestration) — relatively simple wrapper
3. CLI integration (`aleph dashboard` subcommand)
4. Auto-spawning (later, if needed)
