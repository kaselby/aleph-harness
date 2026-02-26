# Handoff — Permission System & VSCode Extension Planning (2026-02-26)

## What Was Done This Session

### 1. VSCode Extension Feasibility Assessment
Researched and assessed building a VSCode extension for Aleph. Key findings:
- **Fully feasible**, no blockers for personal use
- No marketplace publishing needed — sideload via `.vsix` or symlink
- Claude Code's terminal mode embeds xterm.js in a webview sidebar, spawning the CLI as a subprocess
- Diffs and permissions are **CLI features, not extension features** — CC renders them in the terminal, not through VSCode APIs
- This means the extension is trivially thin: just xterm.js + `aleph` subprocess
- The real work is building diffs/permissions into Aleph's TUI (which we started)
- Detailed research notes saved to Obsidian at `claude/Research/VSCode Extensions/`

### 2. Permission System — Implemented and Merged to Main
Built a three-mode permission system with inline diffs:

| Mode | Edit/Write | Bash | Read/Web |
|------|-----------|------|----------|
| **Safe** (red) | Ask | Ask | Allow |
| **Default** (yellow) | Ask | Allow | Allow |
| **Yolo** (green) | Allow | Allow | Allow |

**Files created/modified:**
- `src/aleph/permissions.py` (NEW) — PermissionMode enum, needs_permission(), generate_diff() via difflib, PermissionRequest dataclass with asyncio.Event, create_permission_hook() factory
- `src/aleph/harness.py` — set_permission_hook() method, PreToolUse hook registration
- `src/aleph/tui/app.py` — Tab cycles modes, y/n keybindings for accept/reject, colored toolbar mode indicator, diff rendering, permission prompt

**How it works:** PreToolUse hook fires before each tool call. The hook checks the current mode and tool classification. If permission needed, it generates a diff, renders it in the TUI, and awaits an asyncio.Event. The y/n keybindings resolve the Event. The hook returns `permissionDecision: "allow"` or `"deny"` to the SDK. This works because the SDK dispatches hooks via `await callback()` inside anyio tasks (query.py:293), so prompt_toolkit's event loop keeps processing keystrokes while the hook blocks.

**Status:** Functional and merged to main. User tested it and confirmed the core functionality works.

## What Needs Doing Next

### Immediate: Permission prompt should be ephemeral (in-progress)
The "Allow Edit? [y] accept [n] reject" text currently prints to scrollback and persists after answering. It should disappear after the user responds.

**Approach:** Use `ConditionalContainer` in the prompt_toolkit layout to make the permission prompt a dynamic layout element (like the toolbar) instead of scrollback text. The diff stays in scrollback (that's fine), but the action prompt appears/disappears with the permission state.

Specifically:
- Import `ConditionalContainer` from `prompt_toolkit.layout.containers`
- Add a `_permission_bar` method (like `_toolbar`) that returns the prompt HTML
- Add a `ConditionalContainer` to the HSplit in `__init__`, filtered by `_pending_permission is not None`
- Remove the `_tprint` call for the prompt from `_render_permission_prompt`
- The accepted/rejected feedback can still print to scrollback (green checkmark / red X)

**Work is in the `worktree-permissions` worktree** at `/Users/kaselby/Git/aleph/.claude/worktrees/permissions`. The worktree has some additional changes on top of main (cli.py, config.py additions for ephemeral mode). The main branch also has changes from a parallel Aleph session (usage logging hook, conversation archival, ephemeral mode, improved interrupt handling) — the worktree is behind main on those.

### Later: VSCode Extension
Once the TUI permission UX is polished, building the extension is straightforward:
1. Scaffold with `yo code`
2. Register a sidebar webview with xterm.js
3. Spawn `aleph` process, connect stdin/stdout to xterm.js pty
4. That's it — diffs, permissions, markdown all render in the terminal

The extension needs no structured IPC, no diff editor integration, no permission UI — it's all in the TUI.

## Key Technical Details

- `permission_mode="bypassPermissions"` stays on ClaudeAgentOptions — our PreToolUse hook handles permissions independently
- PreToolUse hooks fire regardless of SDK permission_mode setting
- asyncio.Event works across the anyio/asyncio boundary because anyio runs on the asyncio backend
- The `_on_tool_call_start` method suppresses abbreviated display when `needs_permission()` returns True, since the hook will render the full diff
- Tab cycling works during both idle and receiving states (but not during permission prompts)
- Escape auto-denies pending permissions before interrupting

## Files to Read
- `src/aleph/permissions.py` — all permission logic
- `src/aleph/tui/app.py` — TUI integration (search for "Permission" section)
- `/Users/kaselby/.claude/plans/twinkly-squishing-crystal.md` — original implementation plan
- `claude/Research/VSCode Extensions/` (Obsidian) — extension feasibility research
