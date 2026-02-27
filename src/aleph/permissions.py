"""Permission system — mode management, tool classification, diff generation, and PreToolUse hook."""

import asyncio
import difflib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from claude_agent_sdk import HookContext, HookInput, HookJSONOutput


class PermissionMode(Enum):
    SAFE = "safe"
    DEFAULT = "default"
    YOLO = "yolo"

    def next(self) -> "PermissionMode":
        cycle = [PermissionMode.SAFE, PermissionMode.DEFAULT, PermissionMode.YOLO]
        idx = cycle.index(self)
        return cycle[(idx + 1) % len(cycle)]


def needs_permission(mode: PermissionMode, tool_name: str) -> bool:
    """Whether this tool requires user permission in the given mode."""
    if mode == PermissionMode.YOLO:
        return False
    if tool_name in ("Edit", "Write"):
        return True  # Edit/Write gated in both safe and default
    if tool_name in ("Bash", "mcp__aleph__Bash") and mode == PermissionMode.SAFE:
        return True
    return False


@dataclass
class PermissionRequest:
    """Data passed to the TUI when permission is needed."""

    tool_name: str
    tool_input: dict[str, Any]
    diff_text: str
    result: bool = False
    event: asyncio.Event = field(default_factory=asyncio.Event)

    def decide(self, allowed: bool) -> None:
        self.result = allowed
        self.event.set()


def generate_diff(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Generate a human-readable diff or preview for a tool call."""
    if tool_name == "Edit":
        return _diff_edit(tool_input)
    elif tool_name == "Write":
        return _diff_write(tool_input)
    elif tool_name in ("Bash", "mcp__aleph__Bash"):
        return _preview_bash(tool_input)
    return ""


def _diff_edit(tool_input: dict[str, Any]) -> str:
    path = tool_input.get("file_path", "unknown")
    old = tool_input.get("old_string", "")
    new = tool_input.get("new_string", "")

    old_lines = old.splitlines()
    new_lines = new.splitlines()

    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=path, tofile=path,
        lineterm="",
    ))
    return "\n".join(diff_lines)


def _diff_write(tool_input: dict[str, Any]) -> str:
    path_str = tool_input.get("file_path", "unknown")
    content = tool_input.get("content", "")
    path = Path(path_str)

    if path.exists():
        try:
            existing = path.read_text()
            old_lines = existing.splitlines()
            new_lines = content.splitlines()
            diff_lines = list(difflib.unified_diff(
                old_lines, new_lines,
                fromfile=path_str, tofile=path_str,
                lineterm="",
            ))
            return "\n".join(diff_lines)
        except (OSError, UnicodeDecodeError):
            pass

    # New file — show preview
    lines = content.splitlines()
    n = len(lines)
    preview = lines[:15]
    parts = [f"new file ({n} lines)"]
    for line in preview:
        parts.append(f"+{line}")
    if n > 15:
        parts.append(f"... ({n - 15} more lines)")
    return "\n".join(parts)


def _preview_bash(tool_input: dict[str, Any]) -> str:
    cmd = tool_input.get("command", "")
    desc = tool_input.get("description", "")
    parts = []
    if desc:
        parts.append(desc)
    parts.append(f"$ {cmd}")
    return "\n".join(parts)


# Type alias for the permission handler callback the TUI registers
PermissionHandler = Callable[["PermissionRequest"], Awaitable[bool]]


def create_permission_hook(
    get_mode: Callable[[], PermissionMode],
    request_permission: PermissionHandler,
):
    """Factory: create a PreToolUse hook that checks permissions.

    Args:
        get_mode: Returns the current permission mode (reads TUI state).
        request_permission: Async callback that shows the diff/prompt in the TUI
                          and returns True (allow) or False (deny).
    """

    async def permission_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        mode = get_mode()

        if not needs_permission(mode, tool_name):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }

        # Permission required — generate diff and ask the TUI
        diff_text = generate_diff(tool_name, tool_input)
        req = PermissionRequest(
            tool_name=tool_name,
            tool_input=tool_input,
            diff_text=diff_text,
        )

        allowed = await request_permission(req)

        if allowed:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }
        else:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "User rejected tool call",
                }
            }

    return permission_hook
