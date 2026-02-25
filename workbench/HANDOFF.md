# Handoff — TUI Development Session (2026-02-25)

## What We Were Working On

Developing the TUI for Aleph (the custom Claude Code harness). The TUI renders the SDK's message stream — streamed text, thinking blocks, tool calls with arguments, tool results.

## Key Decisions Made

### Scrollback mode, not full-screen
We tried Textual first (full-screen TUI). It worked but had a fundamental UX problem: no native text selection, no native scrolling. These are inherent to full-screen alternate-screen-buffer apps. We switched to a **scrollback-mode** approach: Rich for formatted output to stdout, prompt_toolkit for input. Native terminal behavior preserved.

The Textual version is preserved on the `main` branch at commit `3cf739c` (first TUI iteration). The scrollback rewrite was merged into main.

### Build our own, not integrate with existing
Researched OpenCode and other TUI projects. None are viable integration targets — they're all tightly coupled to their own backends. OpenCode was also blocked by Anthropic for OAuth spoofing. Textual's `MarkdownStream` (v4) solves the hardest rendering problem. Research notes in Obsidian at `claude/Projects/agent-framework/opencode-research.md` and `claude/Projects/agent-framework/tui-feasibility.md`.

## Current State

**Branch: `tui-pt-application`** (checked out, not yet merged to main)

We're mid-refactor from PromptSession to prompt_toolkit Application. The motivation:

1. **PromptSession keybindings only work during `prompt_async()`** — when the agent is responding, nobody is processing stdin, so Escape-to-interrupt doesn't work.
2. We tried a **cbreak/termios hack** to detect Escape during responses. It worked but had edge cases (arrow keys trigger false interrupts) and wouldn't scale to future keybindings (agent switching, etc.).
3. **prompt_toolkit `Application(full_screen=False)`** runs persistently, always processing stdin. Keybindings use `@Condition` filters to be state-aware (Escape only during response, Enter only when idle). This is the current approach on this branch.

### The immediate problem (unresolved)

**Rich output through `patch_stdout` is garbled.** ANSI escape codes show as `?[1;36m` instead of rendering as colors. The ESC byte (0x1b) is being replaced with `?` by patch_stdout's proxy. The Application renders fine, keybindings work, but all Rich output is unreadable.

Theories on the cause:
- `patch_stdout` replaces `sys.stdout` with a `StdoutProxy` that coordinates output with prompt_toolkit's rendering. Something in this proxy corrupts the ESC bytes.
- Rich's Console resolves `sys.stdout` lazily at write time, so it writes through the proxy.

Approaches not yet tried:
- `Console(file=sys.__stdout__, force_terminal=True)` — bypass the proxy entirely
- Remove `patch_stdout` and use `app.run_in_terminal()` for output coordination
- Remove `patch_stdout` and just print directly (accept occasional prompt area flickering)

### Code review findings (already fixed on this branch)

A subagent reviewed the Application refactor and found issues. All actionable ones were fixed:
1. **Race condition** — double Enter could launch concurrent responses. Fixed: set `_receiving = True` synchronously in the keybinding handler before `ensure_future`.
2. **Ctrl+C cleanup** — exit happened before interrupt completed. Fixed: `handle_quit` is now async, awaits `_do_interrupt`.
3. **Initial prompt timing** — `send_initial` was scheduled outside `patch_stdout` context with a fragile `sleep(0.1)`. Fixed: moved inside, uses `sleep(0)`.
4. **Multiple Escape** — spam could schedule multiple interrupts. Fixed: `_interrupt_in_flight` guard.
5. **Tool name misattribution** — parallel tool calls got wrong names. Fixed: FIFO queue instead of single variable.
6. **Dead code** — removed `messages.py` (Textual leftover) and unused `os` import.

## Files

- `src/aleph/tui/app.py` — the TUI (only file that changed in this refactor)
- `src/aleph/tui/messages.py` — deleted (was Textual-specific)
- `src/aleph/tui/__init__.py` — exports `AlephApp`, unchanged

## What the TUI Renders (when output isn't garbled)

- **Streamed text** — token-by-token via `sys.stdout.write()`, committed to scrollback when done
- **Thinking blocks** — streamed via `thinking_delta` events, rendered dimmed/italic
- **Tool calls** — name + smart-formatted input (Bash→command, Read→file_path, Edit→file+preview, etc.)
- **Tool results** — summary line + truncated output (~10 lines), errors in red
- **Turn stats** — turns, duration, token counts
- **Status toolbar** — Ready/Working, agent ID, total tokens, "Esc to interrupt" hint

## Other Changes Made This Session

- **`src/aleph/cli.py`** — Aleph (the agent) added `--prompt` flag for initial prompts on subagent launch
- **`src/aleph/tui/app.py`** — Aleph added auto-send of initial prompt on session start
- **`workbench/todo.yaml`** — marked task 9 (duplicate text output) as done (was already fixed in code)
