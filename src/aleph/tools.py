"""In-process MCP tools for the Aleph framework."""

from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool


def create_aleph_mcp_server(inbox_root: Path):
    """Create the Aleph MCP server with framework-specific tools.

    Args:
        inbox_root: Root inbox directory (e.g. ~/.aleph/inbox/).
    """

    @tool(
        "send_message",
        "Send a message to another agent's inbox. The message will be delivered "
        "as a notification after their next tool call.",
        {
            "to": str,
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

        recipient_inbox = inbox_root / recipient
        recipient_inbox.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        msg_id = f"msg-{timestamp}"
        msg_path = recipient_inbox / f"{msg_id}.md"

        content = (
            f"---\n"
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

    return create_sdk_mcp_server(
        name="aleph",
        version="0.1.0",
        tools=[send_message],
    )
