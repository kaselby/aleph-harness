"""Permission system — mode management, tool classification, diff generation, and PreToolUse hook.

Includes guardrails for dangerous commands that fire regardless of permission mode.
"""

import asyncio
import difflib
import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from claude_agent_sdk import HookContext, HookInput, HookJSONOutput


# ---------------------------------------------------------------------------
# Guardrails — dangerous command detection
# ---------------------------------------------------------------------------

# Each entry: (compiled regex, tier, human description)
# "block"   = always denied, no override from the agent
# "confirm" = requires explicit user approval (even in YOLO mode)
#
# Block patterns are checked first and take priority over confirm patterns.

_GUARDRAIL_PATTERNS: list[tuple[re.Pattern, str, str]] = []


def _compile_guardrails():
    raw = [
        # --- Block: catastrophic, almost never intentional from an agent ---
        (r"\brm\s+-\S*r\S*\s+/\s*$", "block", "recursive delete from filesystem root"),
        (r"\brm\s+-\S*r\S*\s+/\*", "block", "recursive delete from filesystem root"),
        (r"\brm\s+-\S*r\S*\s+~/?\s*$", "block", "recursive delete of home directory"),
        (r"\bmkfs\b", "block", "format filesystem"),
        (r"\bdd\b.*\bof\s*=\s*/dev/", "block", "write directly to raw device"),

        # --- Confirm: destructive but sometimes legitimate ---
        (r"\bgit\s+push\b", "confirm", "git push"),
        (r"\bgit\s+reset\s+--hard\b", "confirm", "git reset --hard (discards changes)"),
        (r"\bgit\s+clean\b.*-\w*f", "confirm", "git clean (deletes untracked files)"),
        (r"\btmux\s+kill-(session|server)\b", "confirm", "kill tmux session/server"),
        (r"\bkillall\s", "confirm", "kill processes by name (killall)"),
        (r"\bpkill\s", "confirm", "kill processes by pattern (pkill)"),
    ]
    for pattern_str, tier, desc in raw:
        _GUARDRAIL_PATTERNS.append((re.compile(pattern_str), tier, desc))


_compile_guardrails()


def _has_rm_rf(command: str) -> bool:
    """Check if a command contains rm with both -r and -f flags in any form."""
    # Quick exit
    if not re.search(r"\brm\s", command):
        return False
    # Single flag group: rm -rf, rm -fr, rm -rfi, etc.
    if re.search(r"\brm\s+.*-\w*(?:r\w*f|f\w*r)", command):
        return True
    # Separate flags: rm -r -f, rm -r somepath -f, etc.
    # Collect all short flags after rm
    match = re.search(r"\brm\s(.*)", command)
    if match:
        after_rm = match.group(1)
        flags = re.findall(r"-(\w+)", after_rm)
        all_flags = "".join(flags)
        if "r" in all_flags and "f" in all_flags:
            return True
    return False


def classify_danger(command: str) -> tuple[str, str] | None:
    """Classify a bash command's danger level.

    Returns (tier, description) where tier is "block" or "confirm",
    or None if the command is not flagged as dangerous.
    """
    # Block patterns first
    for pattern, tier, desc in _GUARDRAIL_PATTERNS:
        if tier == "block" and pattern.search(command):
            return ("block", desc)

    # rm -rf detection (confirm tier) — uses helper for flag combinations
    if _has_rm_rf(command):
        return ("confirm", "recursive force delete (rm -rf)")

    # Other confirm patterns
    for pattern, tier, desc in _GUARDRAIL_PATTERNS:
        if tier == "confirm" and pattern.search(command):
            return ("confirm", desc)

    return None


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


def _notify(title: str, message: str) -> None:
    """Send a macOS notification. Best-effort, never raises.

    Tries terminal-notifier first (reliable from tmux), falls back to osascript.
    """
    try:
        subprocess.Popen(
            ["terminal-notifier", "-title", title, "-message", message,
             "-sound", "default"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return
    except FileNotFoundError:
        pass
    try:
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def create_permission_hook(
    get_mode: Callable[[], PermissionMode],
    request_permission: PermissionHandler,
):
    """Factory: create a PreToolUse hook that checks permissions.

    Guardrails for dangerous commands fire regardless of permission mode.

    Args:
        get_mode: Returns the current permission mode (reads TUI state).
        request_permission: Async callback that shows the diff/prompt in the TUI
                          and returns True (allow) or False (deny).
    """
    agent_id = os.environ.get("ALEPH_AGENT_ID", "aleph")

    async def permission_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        mode = get_mode()

        # --- Guardrails: check dangerous commands BEFORE mode check ---
        if tool_name in ("Bash", "mcp__aleph__Bash"):
            command = tool_input.get("command", "")
            danger = classify_danger(command)

            if danger:
                tier, reason = danger

                if tier == "block":
                    _notify(f"Aleph: {agent_id}", f"BLOCKED: {reason}")
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason":
                                f"Blocked by guardrail: {reason}. "
                                f"This command is never allowed.",
                        }
                    }

                # Confirm tier — always prompt, even in YOLO mode
                _notify(
                    f"Aleph: {agent_id}",
                    f"Dangerous command needs approval: {reason}",
                )
                diff_text = f"DANGEROUS: {reason}\n\n$ {command}"
                req = PermissionRequest(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    diff_text=diff_text,
                )
                allowed = await request_permission(req)
                decision = "allow" if allowed else "deny"
                result: HookJSONOutput = {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision,
                    }
                }
                if not allowed:
                    result["hookSpecificOutput"]["permissionDecisionReason"] = (
                        f"User rejected dangerous command: {reason}"
                    )
                return result

        # --- Normal permission check (respects mode) ---
        if not needs_permission(mode, tool_name):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }

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
