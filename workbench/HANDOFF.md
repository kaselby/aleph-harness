# Handoff — TUI Development (2026-02-25)

## Current State

**Branch: `main`** — all TUI work merged.

The TUI is functional. It uses prompt_toolkit `Application(full_screen=False)` for
persistent keybinding handling (Escape to interrupt, Ctrl+C to quit, Enter to submit).
All styled output uses `print_formatted_text(HTML(...))` with a markdown-it-py renderer.
Rich was dropped entirely due to ESC byte corruption through `patch_stdout`.

**Live streaming was removed.** Responses accumulate silently and print in full at
commit time (like Claude Code). This was a deliberate decision after three rounds
of optimization failed to fix choppy rendering — the root cause was prompt_toolkit's
layout rendering overhead (per-token `invalidate()` triggering full layout passes on
the HSplit containing the streaming Window). See `~/.aleph/scratch/tui-streaming-fix-handoff.md`
for the full profiling story.

## Known Issue

**The `> ` input prompt occasionally disappears and doesn't come back.** This is a
race condition between `print_formatted_text` and the Application's layout renderer.
`patch_stdout` is the coordination mechanism (wraps `run_in_terminal` to erase layout,
print, redraw), but it's not 100% reliable. Output calls have been batched to reduce
the frequency of `run_in_terminal` cycles. The issue is intermittent and may need
deeper investigation into prompt_toolkit internals.

## Architecture

- `src/aleph/tui/app.py` — the entire TUI in one file
- `src/aleph/tui/__init__.py` — exports `AlephApp`
- Layout: just input line + toolbar (2 lines at bottom of terminal)
- Output: `_tprint()` helper wraps `print_formatted_text(HTML(...))` with auto-escaping
- Markdown: `_markdown_to_ft()` converts markdown to FormattedText via markdown-it-py
- Message routing: `_handle_sdk_message()` dispatches StreamEvent, AssistantMessage, etc.
- Token accumulation: `_stream_chunks` list, joined and markdown-rendered at commit time

## What the TUI Renders

- **Response text** — markdown-rendered (bold, italic, code, headings, lists, tables, code blocks)
- **Thinking blocks** — "Thinking..." label prints immediately, full content prints before response
- **Tool calls** — name + smart-formatted input (Bash→command, Read→path, etc.)
- **Tool results** — summary + truncated output (~10 lines), errors in red
- **Turn stats** — turns, duration, token counts
- **Toolbar** — Ready/Working, agent ID, context usage (Xk/200k), "Esc to interrupt"

## Recent Changes (this session)

- Removed live streaming and all associated machinery (streaming Window, GC disable,
  render throttle, list-based buffers, tail display)
- Added context usage display in toolbar (latest turn's input+output tokens / 200k)
- Thinking blocks commit as soon as text starts (not at response end)
- Model verification via `check_model()` on first AssistantMessage
- Renamed "Assistant:" label to "Aleph:"
- Restored `patch_stdout` import (was accidentally dropped)
- Added TextBlock fallback in AssistantMessage handler

## Task Board

See `workbench/todo.yaml` — tasks 14-15, 21-23 done this session. Open items
include conversation resume/fork/rewind, multi-agent wiring, and the `>` prompt
disappearing issue.
