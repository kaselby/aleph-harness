"""In-process MCP tools for the Aleph framework."""

import os
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from .shell import PersistentShell


# ---------------------------------------------------------------------------
# Shared file state (populated by Read PostToolUse hook, consumed by Edit/Write)
# ---------------------------------------------------------------------------

class FileState:
    """Track which files have been read and their state at read time.

    This replaces Claude Code's internal readFileState for our MCP Edit/Write
    tools. The built-in Read tool's PostToolUse hook populates this, and
    Edit/Write check it for the "must read first" and "modified since read"
    validations.
    """

    def __init__(self):
        self._state: dict[str, dict] = {}

    def record_read(self, file_path: str, *, partial: bool = False) -> None:
        """Record that a file was read. Call from the Read PostToolUse hook."""
        normalized = str(Path(file_path).resolve())
        try:
            mtime = os.path.getmtime(normalized)
        except OSError:
            mtime = 0.0
        self._state[normalized] = {
            "timestamp": mtime,
            "partial": partial,
        }

    def record_write(self, file_path: str) -> None:
        """Update state after a successful write/edit."""
        normalized = str(Path(file_path).resolve())
        try:
            mtime = os.path.getmtime(normalized)
        except OSError:
            mtime = 0.0
        self._state[normalized] = {
            "timestamp": mtime,
            "partial": False,
        }

    def check(self, file_path: str) -> tuple[bool, str | None]:
        """Validate that a file can be written/edited.

        Returns (ok, error_message). If ok is True, the operation can proceed.
        """
        normalized = str(Path(file_path).resolve())

        if not os.path.exists(normalized):
            # New file — no read required
            return True, None

        entry = self._state.get(normalized)
        if not entry:
            return False, "File has not been read yet. Read it first before writing to it."

        try:
            current_mtime = os.path.getmtime(normalized)
        except OSError:
            return True, None  # Can't check, allow it

        if current_mtime > entry["timestamp"]:
            return False, (
                "File has been modified since read, either by the user or "
                "by a linter. Read it again before attempting to write it."
            )

        return True, None


# ---------------------------------------------------------------------------
# MCP server factory
# ---------------------------------------------------------------------------

def create_aleph_mcp_server(
    inbox_root: Path,
    skills_path: Path,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    file_state: FileState | None = None,
):
    """Create the Aleph MCP server with framework-specific tools.

    Returns:
        Tuple of (server, cleanup_coro_fn) where cleanup_coro_fn is an async
        callable that shuts down the persistent shell. Call it before the
        event loop closes.

    Args:
        inbox_root: Root inbox directory (e.g. ~/.aleph/inbox/).
        skills_path: Skills directory (e.g. ~/.aleph/skills/).
        cwd: Initial working directory for the persistent shell.
        env: Environment variable overrides for the persistent shell.
        file_state: Shared FileState for Read/Edit/Write coordination.
    """
    if file_state is None:
        file_state = FileState()

    # Lazily initialized on first Bash call
    shell: PersistentShell | None = None

    async def cleanup():
        nonlocal shell
        if shell is not None:
            await shell.close()
            shell = None

    # ------------------------------------------------------------------
    # Bash tool
    # ------------------------------------------------------------------

    @tool(
        "Bash",
        "Executes a bash command in a persistent shell. Environment variables, "
        "working directory, and other state persist between calls.",
        {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the command does",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in milliseconds (default 120000)",
                },
            },
            "required": ["command"],
        },
    )
    async def bash_tool(args: dict) -> dict:
        nonlocal shell
        if shell is None:
            shell = PersistentShell(cwd=cwd, env=env)

        command = args.get("command", "")
        timeout_ms = args.get("timeout", 120_000)

        if not command.strip():
            return {
                "content": [{"type": "text", "text": "Error: no command provided."}],
                "isError": True,
            }

        result = await shell.run(command, timeout_ms=timeout_ms)

        # Format output to include metadata
        parts = []
        if result["output"].strip():
            parts.append(result["output"].rstrip())

        # Status line
        status = []
        if result["timed_out"]:
            status.append(f"TIMED OUT after {result['elapsed_ms']}ms")
        elif result["exit_code"] != 0:
            status.append(f"Exit code: {result['exit_code']}")
        if result["elapsed_ms"] >= 1000:
            status.append(f"{result['elapsed_ms']}ms")
        status.append(f"cwd: {result['cwd']}")

        footer = f"[{result['timestamp']}] {' | '.join(status)}"
        parts.append(footer)

        text = "\n".join(parts)
        return {"content": [{"type": "text", "text": text}]}

    # ------------------------------------------------------------------
    # Edit tool (MCP replacement for built-in)
    # ------------------------------------------------------------------

    @tool(
        "Edit",
        "Performs exact string replacements in files.\n\n"
        "Usage:\n"
        "- You must use your `Read` tool at least once in the conversation "
        "before editing. This tool will error if you attempt an edit without "
        "reading the file. \n"
        "- When editing text from Read tool output, ensure you preserve the "
        "exact indentation (tabs/spaces) as it appears AFTER the line number "
        "prefix. The line number prefix format is: spaces + line number + tab. "
        "Everything after that tab is the actual file content to match. Never "
        "include any part of the line number prefix in the old_string or "
        "new_string.\n"
        "- ALWAYS prefer editing existing files in the codebase. NEVER write "
        "new files unless explicitly required.\n"
        "- Only use emojis if the user explicitly requests it. Avoid adding "
        "emojis to files unless asked.\n"
        "- The edit will FAIL if `old_string` is not unique in the file. "
        "Either provide a larger string with more surrounding context to make "
        "it unique or use `replace_all` to change every instance of "
        "`old_string`.\n"
        "- Use `replace_all` for replacing and renaming strings across the "
        "file. This parameter is useful if you want to rename a variable for "
        "instance.",
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The absolute path to the file to modify",
                },
                "old_string": {
                    "type": "string",
                    "description": "The text to replace",
                },
                "new_string": {
                    "type": "string",
                    "description": (
                        "The text to replace it with "
                        "(must be different from old_string)"
                    ),
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences of old_string (default false)",
                    "default": False,
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    )
    async def edit_tool(args: dict) -> dict:
        file_path = args.get("file_path", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        replace_all = args.get("replace_all", False)

        if not file_path:
            return _error("No file_path provided.")

        normalized = str(Path(file_path).resolve())

        # File must exist for Edit
        if not os.path.exists(normalized):
            return _error(f"File does not exist: {file_path}")

        # Must have been read first
        ok, err = file_state.check(normalized)
        if not ok:
            return _error(err)

        # Read current content
        try:
            content = Path(normalized).read_text()
        except OSError as e:
            return _error(f"Failed to read file: {e}")

        # Strip trailing newlines from old_string for matching
        # (matches built-in Edit behavior)
        match_string = old_string.rstrip("\n")

        if not match_string and not old_string:
            # Empty old_string with empty new_string = no-op
            if not new_string:
                return _error(
                    "Original and edited file match exactly. Failed to apply edit."
                )
            # Empty old_string = prepend/insert (built-in behavior for creation)
            new_content = new_string
        else:
            # Count occurrences
            count = content.count(match_string)

            if count == 0:
                return _error("String not found in file. Failed to apply edit.")

            if count > 1 and not replace_all:
                return _error(
                    f"{count} matches of the string to replace, but replace_all is "
                    f"false. To replace all occurrences, set replace_all to true. "
                    f"To replace only one occurrence, please provide more context "
                    f"to uniquely identify the instance."
                )

            if replace_all:
                new_content = content.replace(match_string, new_string)
            else:
                # Replace first occurrence only
                new_content = content.replace(match_string, new_string, 1)

        if new_content == content:
            return _error(
                "Original and edited file match exactly. Failed to apply edit."
            )

        # Write the file back
        try:
            _write_file(normalized, new_content)
        except OSError as e:
            return _error(f"Failed to write file: {e}")

        file_state.record_write(normalized)
        return _ok(f"The file {file_path} has been updated successfully.")

    # ------------------------------------------------------------------
    # Write tool (MCP replacement for built-in)
    # ------------------------------------------------------------------

    @tool(
        "Write",
        "Write a file to the local filesystem. Overwrites the file if it "
        "already exists.\n\n"
        "Usage:\n"
        "- If the file already exists, you must Read it first. The tool will "
        "fail if you haven't.\n"
        "- Prefer editing existing files over creating new ones.\n"
        "- NEVER create documentation files (*.md) or README files unless "
        "explicitly requested by the User.\n"
        "- Only use emojis if the user explicitly requests it. Avoid writing "
        "emojis to files unless asked.",
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "The absolute path to the file to write "
                        "(must be absolute, not relative)"
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["file_path", "content"],
        },
    )
    async def write_tool(args: dict) -> dict:
        file_path = args.get("file_path", "")
        content = args.get("content", "")

        if not file_path:
            return _error("No file_path provided.")

        normalized = str(Path(file_path).resolve())
        is_new = not os.path.exists(normalized)

        # For existing files, must have been read first
        if not is_new:
            ok, err = file_state.check(normalized)
            if not ok:
                return _error(err)

        # Create parent directories if needed
        parent = Path(normalized).parent
        parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        try:
            _write_file(normalized, content)
        except OSError as e:
            return _error(f"Failed to write file: {e}")

        file_state.record_write(normalized)

        if is_new:
            return _ok(f"File created successfully at: {file_path}")
        return _ok(f"The file {file_path} has been updated successfully.")

    # ------------------------------------------------------------------
    # activate_skill tool
    # ------------------------------------------------------------------

    @tool(
        "activate_skill",
        "Activate a skill by name. Loads the skill's instructions as system-level "
        "context for the remainder of the session. Use this when your task calls for "
        "a specific skill listed in your session context.",
        {"name": str},
    )
    async def activate_skill(args: dict) -> dict:
        name = args["name"]
        skill_md = skills_path / name / "SKILL.md"

        if not skill_md.exists():
            return {
                "content": [{"type": "text", "text": f"Error: skill '{name}' not found."}],
                "isError": True,
            }

        content = skill_md.read_text()

        # Strip YAML frontmatter — the model doesn't need the metadata
        if content.startswith("---"):
            try:
                end = content.index("---", 3)
                content = content[end + 3:].strip()
            except ValueError:
                pass  # malformed frontmatter, return as-is

        return {"content": [{"type": "text", "text": content}]}

    # ------------------------------------------------------------------
    # send_message tool
    # ------------------------------------------------------------------

    @tool(
        "send_message",
        "Send a message to another agent's inbox. The message will be delivered "
        "as a notification after their next tool call.",
        {
            "to": str,
            "from": str,
            "summary": str,
            "body": str,
            "priority": str,
        },
    )
    async def send_message(args: dict) -> dict:
        recipient = args["to"]
        summary = args["summary"]
        body = args["body"]
        priority = args.get("priority", "normal")
        sender = args.get("from", "unknown")

        recipient_inbox = inbox_root / recipient
        recipient_inbox.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        import uuid as _uuid
        msg_id = f"msg-{timestamp}-{_uuid.uuid4().hex[:6]}"
        msg_path = recipient_inbox / f"{msg_id}.md"

        content = (
            f"---\n"
            f"from: {sender}\n"
            f"summary: \"{summary}\"\n"
            f"priority: {priority}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"---\n\n"
            f"{body}\n"
        )

        msg_path.write_text(content)

        return {
            "content": [
                {"type": "text", "text": f"Message sent to {recipient} at {msg_path}"}
            ]
        }

    server = create_sdk_mcp_server(
        name="aleph",
        version="0.2.0",
        tools=[bash_tool, edit_tool, write_tool, activate_skill, send_message],
    )
    return server, cleanup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(text: str) -> dict:
    """Return a success MCP tool result."""
    return {"content": [{"type": "text", "text": text}]}


def _error(text: str) -> dict:
    """Return an error MCP tool result."""
    return {
        "content": [{"type": "text", "text": text}],
        "isError": True,
    }


def _write_file(path: str, content: str) -> None:
    """Write content to a file, preserving permissions on existing files.

    Detects encoding of existing files and preserves it.
    """
    p = Path(path)

    # Preserve permissions of existing files
    existing_mode = None
    if p.exists():
        existing_mode = p.stat().st_mode

    p.write_text(content)

    if existing_mode is not None:
        os.chmod(path, existing_mode)
