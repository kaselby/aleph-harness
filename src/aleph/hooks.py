"""Hook callbacks for message delivery, read tracking, and periodic reminders."""

import os
import subprocess
from datetime import date, datetime
from pathlib import Path

import yaml

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



_MEMORY_PROMPTS = [
    (
        "Has the user expressed any preferences this session — how they like to "
        "work, communicate, or make decisions? Update ~/.aleph/memory/preferences.md "
        "if so."
    ),
    (
        "Have you learned any lessons or hit any gotchas this session? Has the "
        "user corrected you on something? Update ~/.aleph/memory/patterns.md "
        "if so."
    ),
    (
        "Have you learned any durable knowledge worth adding to "
        "~/.aleph/memory/context.md? New project facts, key references, "
        "architectural details you'll always want to know?"
    ),
    (
        "Have you discovered anything about the codebase, architecture, or "
        "conventions worth recording? Update the project's memory.md if so."
    ),
]


def create_reminder_hook(interval: int = 50):
    """Create a PostToolUse hook that periodically reminds the agent to update memory.

    Rotates through specific, targeted prompts rather than repeating the same
    generic message — makes the reminders harder to tune out.

    Args:
        interval: Number of tool calls between reminders.
    """
    call_count = 0
    prompt_index = 0

    async def reminder_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal call_count, prompt_index
        call_count += 1

        if call_count % interval != 0:
            return {}

        prompt = _MEMORY_PROMPTS[prompt_index % len(_MEMORY_PROMPTS)]
        prompt_index += 1

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": f"[Memory check]: {prompt}",
            }
        }

    return reminder_hook


def _get_session_timestamp(path: Path) -> datetime:
    """Extract timestamp from session file frontmatter, falling back to file mtime."""
    try:
        text = path.read_text()
        if text.startswith("---"):
            end = text.index("---", 3)
            frontmatter = yaml.safe_load(text[3:end])
            if frontmatter and "timestamp" in frontmatter:
                ts = frontmatter["timestamp"]
                if isinstance(ts, datetime):
                    return ts
                return datetime.fromisoformat(str(ts))
    except (ValueError, yaml.YAMLError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def _build_session_recap(sessions_path: Path) -> str:
    """Summarize today's recent sessions using Haiku.

    Reads up to 5 most recent session files from today, calls Haiku to
    produce a concise recap. Returns empty string on failure or if no
    sessions exist.
    """
    if not sessions_path.exists():
        return ""

    today_prefix = date.today().strftime("%Y-%m-%d")
    today_files = sorted(
        [
            f
            for f in sessions_path.iterdir()
            if f.name.startswith(today_prefix) and f.suffix == ".md"
        ],
        key=_get_session_timestamp,
        reverse=True,
    )[:5]

    if not today_files:
        return ""

    content_parts = []
    for f in today_files:
        content_parts.append(f"### {f.stem}\nFile: {f}\n\n{f.read_text()}")
    combined = "\n\n---\n\n".join(content_parts)

    prompt = (
        "Below are session summaries from today for a persistent AI assistant called Aleph, "
        "ordered from MOST RECENT to oldest. Each session header includes the file path.\n\n"
        "Produce a recap covering: what was worked on, key decisions, current state, "
        "and anything unfinished. Structure the recap in chronological order (most recent "
        "session FIRST, clearly labeled). For each session mentioned, include its file path "
        "so the agent can read the full summary if needed.\n\n"
        "Write in second person ('you did X'). Be specific — names, paths, "
        "details matter more than vague summaries. Keep it concise but don't sacrifice "
        "clarity for brevity.\n\n"
        f"{combined}"
    )

    try:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            ["claude", "-p", "--model", "haiku", "--no-session-persistence", "--effort", "low"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except Exception:
        return ""


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
