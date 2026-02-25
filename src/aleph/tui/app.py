"""Aleph TUI — scrollback-mode terminal interface.

Uses Rich for formatted output and prompt_toolkit for input.
Prints to the terminal's normal scrollback buffer (not alternate screen),
so native text selection and scrolling work naturally.
"""

from __future__ import annotations

import asyncio
import json
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.text import Text

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import StreamEvent

from ..harness import AlephHarness

# Max lines of tool result output to show inline
TOOL_RESULT_MAX_LINES = 10

console = Console(highlight=False)


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234 -> '1.2k', 12345 -> '12k'."""
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}k"
    return f"{n // 1000}k"


def _format_tool_input(name: str, input: dict) -> str:
    """Format tool input for display, tailored per tool type."""
    match name:
        case "Bash":
            cmd = input.get("command", "")
            desc = input.get("description", "")
            lines = cmd.split("\n")
            if len(lines) > 3:
                cmd_display = "\n".join(lines[:3]) + f"\n... ({len(lines) - 3} more lines)"
            else:
                cmd_display = cmd
            if desc:
                return f"{desc}\n$ {cmd_display}"
            return f"$ {cmd_display}"
        case "Read":
            path = input.get("file_path", "")
            parts = [path]
            if "offset" in input:
                parts.append(f"from line {input['offset']}")
            if "limit" in input:
                parts.append(f"({input['limit']} lines)")
            return " ".join(parts)
        case "Write":
            return input.get("file_path", "")
        case "Edit":
            path = input.get("file_path", "")
            old = input.get("old_string", "")
            if old:
                preview = old[:80].replace("\n", "\\n")
                if len(old) > 80:
                    preview += "..."
                return f"{path}  '{preview}'"
            return path
        case "WebSearch":
            return input.get("query", "")
        case "WebFetch":
            return input.get("url", "")
        case _:
            compact = json.dumps(input, separators=(",", ":"))
            if len(compact) > 120:
                return compact[:117] + "..."
            return compact


def _format_tool_result(name: str, content: str | list | None, is_error: bool | None) -> str:
    """Format tool result for display — summary line + truncated output."""
    if content is None:
        return "(no output)"

    # Normalize content to string
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        text = "\n".join(parts)
    else:
        text = str(content)

    if not text.strip():
        return "(empty)"

    lines = text.split("\n")

    if is_error:
        error_text = text[:500]
        if len(text) > 500:
            error_text += f"\n... ({len(text) - 500} more chars)"
        return f"Error:\n{error_text}"

    match name:
        case "Read":
            summary = f"{len(lines)} lines"
        case "Bash":
            summary = "output:"
        case "Write":
            summary = f"wrote {len(text)} bytes"
        case "Edit":
            summary = "applied"
        case _:
            summary = ""

    if len(lines) <= TOOL_RESULT_MAX_LINES:
        output = text
    else:
        output = "\n".join(lines[:TOOL_RESULT_MAX_LINES])
        output += f"\n... ({len(lines) - TOOL_RESULT_MAX_LINES} more lines)"

    if summary:
        return f"{summary}\n{output}"
    return output


class AlephApp:
    """Scrollback-mode terminal interface for Aleph."""

    def __init__(self, harness: AlephHarness) -> None:
        self._harness = harness
        self._stream_buffer = ""
        self._thinking_buffer = ""
        self._last_tool_name = ""
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._status = "Ready"
        self._prompt_session = PromptSession(bottom_toolbar=self._toolbar)

    def _toolbar(self) -> HTML:
        """Build the persistent bottom toolbar content."""
        parts = [self._status, self._harness.agent_id]
        total = self._total_input_tokens + self._total_output_tokens
        if total:
            parts.append(f"{_fmt_tokens(total)} tokens")
        return HTML(f" {' | '.join(parts)}")

    def run(self) -> None:
        """Run the TUI event loop."""
        asyncio.run(self._main())

    async def _main(self) -> None:
        """Main async loop: connect, then alternate between input and response."""
        console.print(f"[dim]Connecting...[/dim]")

        try:
            await self._harness.start()
        except Exception as e:
            console.print(f"[red bold]Connection error:[/red bold] {e}")
            return

        console.print(f"[dim]Session started: {self._harness.agent_id}[/dim]\n")

        try:
            await self._input_loop()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            console.print(f"\n[dim]Disconnecting...[/dim]")
            await self._harness.stop()

    async def _input_loop(self) -> None:
        """Read user input and send to the agent."""
        while True:
            try:
                with patch_stdout():
                    text = await self._prompt_session.prompt_async(
                        "\n> ",
                    )
            except (KeyboardInterrupt, EOFError):
                return

            text = text.strip()
            if not text:
                continue

            if text == "/exit":
                return

            console.print(f"\n[bold cyan]You:[/bold cyan] {escape(text)}")

            await self._send_and_receive(text)

    async def _send_and_receive(self, text: str) -> None:
        """Send a message and render the full response."""
        self._stream_buffer = ""
        self._thinking_buffer = ""

        try:
            await self._harness.send(text)

            async for msg in self._harness.receive():
                self._handle_sdk_message(msg)

            # Flush any remaining buffers
            self._commit_stream()
            self._commit_thinking()

        except KeyboardInterrupt:
            self._commit_stream()
            self._commit_thinking()
            console.print("\n[dim italic]--- interrupted ---[/dim italic]")
            try:
                await self._harness.interrupt()
            except Exception:
                pass
        except Exception as e:
            console.print(f"\n[red bold]Error:[/red bold] {e}")

    def _handle_sdk_message(self, msg: object) -> None:
        """Route an incoming SDK message to the appropriate handler."""
        if isinstance(msg, StreamEvent):
            event = msg.event
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                delta_type = delta.get("type", "")
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        self._on_stream_text(text)
                elif delta_type == "thinking_delta":
                    thinking = delta.get("thinking", "")
                    if thinking:
                        self._on_stream_thinking(thinking)

        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    self._last_tool_name = block.name
                    self._on_tool_call_start(block.name, block.input)

        elif isinstance(msg, UserMessage):
            content = msg.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        self._on_tool_call_result(
                            self._last_tool_name, block.content, block.is_error
                        )

        elif isinstance(msg, ResultMessage):
            self._on_turn_complete(msg)

        elif isinstance(msg, SystemMessage):
            if msg.subtype not in ("init",):
                console.print(f"[dim italic]System: {msg.subtype}[/dim italic]")

    # ---- Rendering ----

    def _on_stream_text(self, text: str) -> None:
        """Handle a chunk of streamed text."""
        if not self._stream_buffer:
            # First text chunk — flush thinking and print assistant label
            self._commit_thinking()
            console.print("\n[bold green]Assistant:[/bold green]")

        self._stream_buffer += text
        # Print the chunk inline (no newline) for real-time streaming
        sys.stdout.write(text)
        sys.stdout.flush()

    def _on_stream_thinking(self, text: str) -> None:
        """Handle a chunk of streamed thinking text."""
        if not self._thinking_buffer:
            console.print("\n[dim italic]Thinking...[/dim italic]")

        self._thinking_buffer += text

    def _on_tool_call_start(self, name: str, input: dict) -> None:
        """Render a tool call with its input details."""
        self._commit_stream()
        self._commit_thinking()

        console.print(f"\n  [bold yellow]\u2192 {name}[/bold yellow]")
        details = _format_tool_input(name, input)
        if details:
            for line in details.split("\n"):
                console.print(f"    [dim]{escape(line)}[/dim]")

    def _on_tool_call_result(
        self, name: str, content: str | list | None, is_error: bool | None
    ) -> None:
        """Render a tool result."""
        formatted = _format_tool_result(name, content, is_error)
        for line in formatted.split("\n"):
            if is_error:
                console.print(f"    [red]{escape(line)}[/red]")
            else:
                console.print(f"    [dim]{escape(line)}[/dim]")

    def _on_turn_complete(self, msg: ResultMessage) -> None:
        """Render turn completion stats."""
        self._commit_stream()
        self._commit_thinking()

        usage = msg.usage or {}
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        self._total_input_tokens += input_tok
        self._total_output_tokens += output_tok

        parts = [f"{msg.num_turns} turns", f"{msg.duration_ms}ms"]
        if input_tok or output_tok:
            parts.append(f"{_fmt_tokens(input_tok)} in / {_fmt_tokens(output_tok)} out")
        total = self._total_input_tokens + self._total_output_tokens
        if total:
            parts.append(f"total: {_fmt_tokens(total)}")
        summary = "  |  ".join(parts)
        console.print(f"\n[dim]--- {summary} ---[/dim]")

    # ---- Helpers ----

    def _commit_stream(self) -> None:
        """Finalize the streaming text buffer."""
        if self._stream_buffer:
            # The text was already printed char-by-char via sys.stdout.write.
            # Just add a newline to close it out.
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._stream_buffer = ""

    def _commit_thinking(self) -> None:
        """Flush the thinking buffer as dimmed text."""
        if self._thinking_buffer:
            for line in self._thinking_buffer.split("\n"):
                console.print(f"[dim italic]{escape(line)}[/dim italic]")
            self._thinking_buffer = ""
