"""In-process MCP tools for the Aleph framework."""

from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from .shell import PersistentShell


def create_aleph_mcp_server(
    inbox_root: Path,
    skills_path: Path,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
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
    """
    # Lazily initialized on first Bash call
    shell: PersistentShell | None = None

    async def cleanup():
        nonlocal shell
        if shell is not None:
            await shell.close()
            shell = None

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
            end = content.index("---", 3)
            content = content[end + 3:].strip()

        return {"content": [{"type": "text", "text": content}]}

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
        msg_id = f"msg-{timestamp}"
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
        version="0.1.0",
        tools=[bash_tool, activate_skill, send_message],
    )
    return server, cleanup

