"""Hook callbacks for message delivery, read tracking, periodic reminders, planning nudges, and usage logging."""

import json
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

                # Extract summary and channel from frontmatter
                parsed = parse_message(msg_file)
                if parsed and parsed["summary"]:
                    if parsed["channel"]:
                        prefix = f"[Channel: {parsed['channel']}]"
                    else:
                        prefix = "[Message]"
                    summaries.append(f"{prefix}: {parsed['summary']} — Full message at {msg_file}")

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


def create_read_tracking_hook(inbox_path: Path, file_state=None):
    """Create a PostToolUse hook (matcher="Read") that:

    1. Marks inbox messages as read (creates .read marker files).
    2. Records file reads in the shared FileState so MCP Edit/Write can
       enforce the "must read first" and "modified since read" validations.

    Args:
        inbox_path: The agent's inbox directory.
        file_state: A FileState instance shared with the MCP Edit/Write tools.
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

        # Record in shared file state (for MCP Edit/Write validation)
        if file_state is not None:
            has_offset = tool_input.get("offset") is not None
            has_limit = tool_input.get("limit") is not None
            file_state.record_read(
                str(file_path), partial=(has_offset or has_limit)
            )

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
        "work, communicate, or make decisions? Update ~/.aleph/memory/latent/preferences.md "
        "if so."
    ),
    (
        "Have you learned any lessons or hit any gotchas this session? Has the "
        "user corrected you on something? Update ~/.aleph/memory/latent/patterns.md "
        "if so."
    ),
    (
        "Have you learned any durable knowledge worth adding to "
        "~/.aleph/memory/core.md? New project facts, key references, "
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


def create_context_warning_hook(session_control, max_tokens: int = 200_000):
    """Create a PostToolUse hook that warns when context usage crosses thresholds.

    Fires at 50% (100k), then every 10% after that. Each threshold fires
    only once. The warning includes the current usage and a suggestion to
    consider handoffs at higher levels.

    Args:
        session_control: SessionControl instance with context_tokens field.
        max_tokens: Maximum context window size (default 200k).
    """
    # Thresholds as fractions: 0.50, 0.60, 0.70, 0.80, 0.90
    thresholds = [0.5, 0.6, 0.7, 0.8, 0.9]
    fired: set[float] = set()

    async def context_warning_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if session_control is None:
            return {}

        tokens = session_control.context_tokens
        if tokens <= 0:
            return {}

        fraction = tokens / max_tokens

        # Find the highest threshold we've crossed that hasn't fired yet
        crossed = None
        for t in thresholds:
            if fraction >= t and t not in fired:
                crossed = t

        if crossed is None:
            return {}

        fired.add(crossed)
        pct = int(crossed * 100)
        token_k = f"{tokens // 1000}k"

        if crossed >= 0.8:
            urgency = (
                "Context is getting tight. If you're working autonomously, "
                "write a handoff now and prepare to spawn a continuation."
            )
        elif crossed >= 0.6:
            urgency = (
                "If this is a long task, start thinking about what a handoff "
                "would look like."
            )
        else:
            urgency = "No action needed yet — this is informational."

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"[Context: {pct}%] Using ~{token_k} of {max_tokens // 1000}k tokens. "
                    f"{urgency}"
                ),
            }
        }

    return context_warning_hook


def create_plan_nudge_hook(plan_path: Path, interval: int = 20):
    """Create a PostToolUse hook that periodically injects the agent's current plan.

    Reads the agent's plan file from disk and injects it as additionalContext.
    Nudges happen every `interval` tool calls, but only when a plan file exists.
    If there's no plan, stays silent — plan creation is prompted by ALEPH.md
    instructions, not nagging hooks.

    Args:
        plan_path: Path to this agent's plan YAML file.
        interval: Number of tool calls between plan injections.
    """
    call_count = 0

    async def plan_nudge_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal call_count
        call_count += 1

        if call_count % interval != 0:
            return {}

        if not plan_path.exists():
            return {}

        try:
            data = yaml.safe_load(plan_path.read_text())
        except (yaml.YAMLError, OSError):
            return {}

        if not data or not data.get("tasks"):
            return {}

        # Format the plan
        lines = [f"Goal: {data.get('goal', '(none)')}"]
        tasks = data.get("tasks", [])
        for t in tasks:
            status = t.get("status", "pending")
            desc = t.get("description", "")
            lines.append(f"  [{status}] {desc}")
        done = sum(1 for t in tasks if t.get("status") == "done")
        total = len(tasks)
        lines.append(f"Progress: {done}/{total} done.")

        # Don't nudge if all tasks are done
        if done == total:
            return {}

        plan_text = "\n".join(lines)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"[Plan check] Your current plan:\n{plan_text}\n"
                    f"Update with the `plan` tool if this is stale or if you've completed tasks."
                ),
            }
        }

    return plan_nudge_hook


def create_worklog_hooks(worklog_path: Path, interval_minutes: int = 5):
    """Create a coordinated pair of hooks for periodic worklog capture.

    Returns (stop_hook, post_tool_hook).

    The Stop hook fires periodically and prompts the agent to write a broader
    cognitive snapshot to the worklog. The PostToolUse hook then fires on the
    next tool call (the worklog write) and returns control to the user via
    ``continue: false``.

    This avoids the double-response problem: the agent writes the worklog
    and its turn ends cleanly, rather than producing both a worklog
    acknowledgment and a separate response.
    """
    last_fired: list[float] = [0.0]
    pending_cutoff: list[bool] = [False]

    async def stop_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        import time

        # Don't recurse — if we already blocked once, let the agent stop.
        # Clear pending_cutoff so a stale flag doesn't fire on a later tool call.
        if input_data.get("stop_hook_active", False):
            pending_cutoff[0] = False
            return {}

        # Throttle: only fire every interval_minutes of real time
        now = time.time()
        if now - last_fired[0] < interval_minutes * 60:
            return {}

        last_fired[0] = now
        pending_cutoff[0] = True

        return {
            "decision": "block",
            "reason": (
                f"It's been a while — before responding, capture a broader snapshot "
                f"of where your head is to the worklog. Run a Bash command to append "
                f"to {worklog_path}: not just what you're doing right now, but where "
                f"things stand overall, what's resolved, what's open, what you're "
                f"thinking about. A few sentences — a wider-angle view than the "
                f"per-tool-call annotations."
            ),
        }

    async def post_tool_cutoff(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """After the worklog write, end the agent's turn."""
        if pending_cutoff[0]:
            pending_cutoff[0] = False
            return {
                "continue_": False,
                "stopReason": "Worklog snapshot captured.",
            }
        return {}

    return stop_hook, post_tool_cutoff


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


def create_usage_log_hook(logs_path: Path, agent_id: str, tools_bin: Path | None = None):
    """Create a PostToolUse hook that logs custom tool and skill usage to a JSONL file.

    Logs two categories:
    - Custom tools: Bash calls to tools/bin/* (e.g. exa, tavily)
    - Skill activations: mcp__aleph__activate_skill calls
    Built-in tools (Read, Write, Bash, etc.) are skipped.
    """
    log_file = logs_path / "tool-usage.jsonl"
    bin_prefix = str(tools_bin) + "/" if tools_bin else None

    def _append(entry: dict) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    async def usage_log_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        tool_output = input_data.get("tool_output", {})
        is_error = False
        if isinstance(tool_output, dict):
            is_error = bool(tool_output.get("is_error"))

        # Skill activations
        if tool_name == "mcp__aleph__activate_skill":
            skill = tool_input.get("name", "unknown")
            _append({
                "ts": datetime.now().isoformat(),
                "agent": agent_id,
                "type": "skill",
                "name": skill,
            })
            return {}

        # Custom tools (Bash calls to tools/bin/)
        if tool_name in ("Bash", "mcp__aleph__Bash") and bin_prefix:
            command = tool_input.get("command", "")
            if bin_prefix in command:
                try:
                    idx = command.index(bin_prefix)
                    rest = command[idx + len(bin_prefix):]
                    custom_tool = rest.split()[0] if rest else "unknown"
                except (ValueError, IndexError):
                    custom_tool = "unknown"
                _append({
                    "ts": datetime.now().isoformat(),
                    "agent": agent_id,
                    "type": "tool",
                    "name": custom_tool,
                    "error": is_error,
                })

        return {}

    return usage_log_hook


def parse_message(msg_file: Path) -> dict | None:
    """Parse a message file into its components.

    Returns a dict with keys: from, summary, priority, body, path.
    Returns None if the file can't be read.
    """
    try:
        text = msg_file.read_text()
    except OSError:
        return None

    result = {
        "from": "",
        "summary": "",
        "priority": "normal",
        "channel": "",
        "body": "",
        "path": str(msg_file),
    }

    if not text.startswith("---"):
        # No frontmatter — treat entire content as body, first line as summary
        first_line = text.strip().split("\n")[0]
        result["summary"] = first_line[:200] if first_line else ""
        result["body"] = text.strip()
        return result

    # Parse YAML frontmatter
    lines = text.split("\n")
    fm_end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            fm_end = i
            break

    if fm_end is None:
        result["body"] = text.strip()
        return result

    for line in lines[1:fm_end]:
        if line.startswith("from:"):
            result["from"] = line[len("from:"):].strip().strip('"').strip("'")
        elif line.startswith("summary:"):
            result["summary"] = line[len("summary:"):].strip().strip('"').strip("'")
        elif line.startswith("priority:"):
            result["priority"] = line[len("priority:"):].strip().strip('"').strip("'")
        elif line.startswith("channel:"):
            result["channel"] = line[len("channel:"):].strip().strip('"').strip("'")

    result["body"] = "\n".join(lines[fm_end + 1:]).strip()
    return result


def _extract_summary(msg_file: Path) -> str | None:
    """Extract the summary field from a message file's YAML frontmatter."""
    parsed = parse_message(msg_file)
    if parsed is None:
        return None
    return parsed["summary"] or None
