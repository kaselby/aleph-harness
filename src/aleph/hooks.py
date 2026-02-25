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


def create_skill_activation_hook(skills_path: Path):
    """Create a PreToolUse hook (matcher="Read") that intercepts SKILL.md reads.

    When the agent tries to Read a SKILL.md inside the skills directory, this hook
    denies the Read and instead injects the skill content as additionalContext
    (system-level authority). This prevents the content appearing twice — once as
    a tool result and once as a system reminder.
    """

    async def skill_activation_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        tool_input = input_data.get("tool_input", {})
        file_path_str = tool_input.get("file_path", "")
        if not file_path_str:
            return {}

        file_path = Path(file_path_str)

        # Check if this is a SKILL.md inside the skills directory
        if file_path.name != "SKILL.md":
            return {}
        try:
            file_path.relative_to(skills_path)
        except ValueError:
            return {}

        if not file_path.exists():
            return {}

        # Read the skill content and inject it as system context
        skill_name = file_path.parent.name
        content = file_path.read_text()

        # Strip YAML frontmatter — the model doesn't need the metadata
        if content.startswith("---"):
            end = content.index("---", 3)
            content = content[end + 3:].strip()

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Skill '{skill_name}' activated — content loaded as system context.",
                "additionalContext": f"[Skill: {skill_name}]\n\n{content}",
            }
        }

    return skill_activation_hook


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
