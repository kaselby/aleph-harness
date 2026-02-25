"""Hook callbacks for message delivery, read tracking, and periodic reminders."""

from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    HookContext,
    HookInput,
    HookJSONOutput,
)


def create_inbox_check_hook(inbox_path: Path):
    """Create a PostToolUse hook that checks for unread messages after every tool call.

    Returns summaries of unread messages as additionalContext.
    """

    async def inbox_check_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if not inbox_path.exists():
            return {}

        summaries = []
        for msg_file in sorted(inbox_path.iterdir()):
            if msg_file.is_file() and msg_file.suffix == ".md":
                # Check for read marker
                read_marker = msg_file.with_suffix(".read")
                if read_marker.exists():
                    continue

                # Extract summary from frontmatter
                summary = _extract_summary(msg_file)
                if summary:
                    summaries.append(f"[Message]: {summary} — Full message at {msg_file}")

        if not summaries:
            return {}

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(summaries),
            }
        }

    return inbox_check_hook


def create_skill_context_hook(skills_path: Path):
    """Create a PostToolUse hook (matcher="mcp__aleph__activate_skill") that injects
    skill content as system-level context.

    When the activate_skill MCP tool runs, this hook replaces its output with a short
    confirmation (via updatedMCPToolOutput) and injects the full skill content as
    additionalContext so it appears as a system message rather than a tool result.
    """

    async def skill_context_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        tool_input = input_data.get("tool_input", {})
        name = tool_input.get("name", "")
        if not name:
            return {}

        skill_md = skills_path / name / "SKILL.md"
        if not skill_md.exists():
            return {}

        content = skill_md.read_text()

        # Strip YAML frontmatter
        if content.startswith("---"):
            end = content.index("---", 3)
            content = content[end + 3:].strip()

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedMCPToolOutput": f"Skill '{name}' activated.",
                "additionalContext": f"[Skill: {name}]\n\n{content}",
            }
        }

    return skill_context_hook


def create_read_tracking_hook(inbox_path: Path):
    """Create a PostToolUse hook (matcher="Read") that marks inbox messages as read.

    When the agent reads a file inside its inbox directory, this hook creates a
    .read marker file so the inbox check hook stops surfacing it.
    """

    async def read_tracking_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        # input_data is a TypedDict — get the tool_input
        tool_input = input_data.get("tool_input", {})
        file_path_str = tool_input.get("file_path", "")
        if not file_path_str:
            return {}

        file_path = Path(file_path_str)

        # Check if this file is inside the inbox directory
        try:
            file_path.relative_to(inbox_path)
        except ValueError:
            return {}

        # It's an inbox file — mark it as read
        if file_path.suffix == ".md" and file_path.exists():
            read_marker = file_path.with_suffix(".read")
            read_marker.touch()

        return {}

    return read_tracking_hook


def create_reminder_hook(interval: int = 50):
    """Create a PostToolUse hook that periodically reminds the agent to update memory.

    Args:
        interval: Number of tool calls between reminders.
    """
    call_count = 0

    async def reminder_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal call_count
        call_count += 1

        if call_count % interval != 0:
            return {}

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "[System reminder]: Consider updating memory with any important "
                    "observations from this session. Review ~/.aleph/memory.md."
                ),
            }
        }

    return reminder_hook


def _extract_summary(msg_file: Path) -> str | None:
    """Extract the summary field from a message file's YAML frontmatter."""
    try:
        text = msg_file.read_text()
    except OSError:
        return None

    # Simple frontmatter parsing — look for summary: in YAML block
    if not text.startswith("---"):
        # No frontmatter, use first line as summary
        first_line = text.strip().split("\n")[0]
        return first_line[:200] if first_line else None

    lines = text.split("\n")
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("summary:"):
            return line[len("summary:"):].strip().strip('"').strip("'")

    return None
