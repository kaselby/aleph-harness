"""Aleph TUI application -- main Textual app."""

from __future__ import annotations

import json

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, RichLog, Static

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
from .messages import (
    HarnessError,
    HarnessReady,
    StreamText,
    StreamThinking,
    ToolCallResult,
    ToolCallStart,
    TurnComplete,
)

# Max lines of tool result output to show inline
TOOL_RESULT_MAX_LINES = 10


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
            # MCP tools or unknown — compact JSON
            compact = json.dumps(input, separators=(",", ":"))
            if len(compact) > 120:
                return compact[:117] + "..."
            return compact


def _format_tool_result(name: str, content: str | list | None, is_error: bool | None) -> str:
    """Format tool result for display — summary line + truncated output."""
    if content is None:
        return "[dim](no output)[/dim]"

    # Normalize content to string
    if isinstance(content, list):
        # List of content blocks from the SDK
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
        return "[dim](empty)[/dim]"

    lines = text.split("\n")

    # Build summary based on tool type
    if is_error:
        # Always show full error text
        error_text = text[:500]
        if len(text) > 500:
            error_text += f"\n... ({len(text) - 500} more chars)"
        return f"[red]Error:[/red]\n{error_text}"

    match name:
        case "Read":
            summary = f"[dim]{len(lines)} lines[/dim]"
        case "Bash":
            summary = "[dim]output:[/dim]"
        case "Write":
            summary = f"[dim]wrote {len(text)} bytes[/dim]"
        case "Edit":
            summary = "[dim]applied[/dim]"
        case _:
            summary = ""

    # Truncate output
    if len(lines) <= TOOL_RESULT_MAX_LINES:
        output = text
    else:
        output = "\n".join(lines[:TOOL_RESULT_MAX_LINES])
        output += f"\n[dim]... ({len(lines) - TOOL_RESULT_MAX_LINES} more lines)[/dim]"

    if summary:
        return f"{summary}\n{output}"
    return output


class AlephApp(App):
    """Terminal UI for Aleph."""

    TITLE = "Aleph"

    CSS = """
    #chat-log {
        height: 1fr;
        scrollbar-size: 1 1;
    }

    #streaming-text {
        height: auto;
        max-height: 50vh;
        padding: 0 1;
        display: none;
    }

    #streaming-text.visible {
        display: block;
    }

    #status-bar {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }

    #input-box {
        dock: bottom;
        margin: 0 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("escape", "interrupt", "Interrupt", show=True),
    ]

    def __init__(self, harness: AlephHarness) -> None:
        super().__init__()
        self._harness = harness
        self._receiving = False
        self._stream_buffer = ""
        self._thinking_buffer = ""
        self._last_tool_name = ""
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", wrap=True, markup=True)
        yield Static("", id="streaming-text")
        yield Static("Connecting...", id="status-bar")
        yield Input(placeholder="Type a message...", id="input-box", disabled=True)

    def on_mount(self) -> None:
        self._connect_harness()

    @work(thread=False)
    async def _connect_harness(self) -> None:
        try:
            await self._harness.start()
            self.post_message(HarnessReady())
        except Exception as e:
            self.post_message(HarnessError(str(e)))

    def on_harness_ready(self, message: HarnessReady) -> None:
        self._update_status("Ready")
        input_box = self.query_one("#input-box", Input)
        input_box.disabled = False
        input_box.focus()
        log = self.query_one("#chat-log", RichLog)
        log.write(f"[dim]Session started: {self._harness.agent_id}[/dim]")

    def on_harness_error(self, message: HarnessError) -> None:
        status = self.query_one("#status-bar", Static)
        status.update(f"[red]Error: {message.error}[/red]")
        log = self.query_one("#chat-log", RichLog)
        log.write(f"[red bold]Connection error:[/red bold] {message.error}")
        # Re-enable input so user can retry or quit
        self._set_receiving(False)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._receiving:
            return

        event.input.value = ""

        # Handle slash commands
        if text == "/exit":
            await self.action_quit()
            return

        log = self.query_one("#chat-log", RichLog)
        log.write(f"\n[bold cyan]You:[/bold cyan] {text}")
        log.scroll_end(animate=False)

        self._set_receiving(True)
        self._send_and_receive(text)

    @work(thread=False)
    async def _send_and_receive(self, text: str) -> None:
        try:
            await self._harness.send(text)
            self._stream_buffer = ""
            self._thinking_buffer = ""

            async for msg in self._harness.receive():
                self._handle_sdk_message(msg)

        except Exception as e:
            self.post_message(HarnessError(str(e)))
        finally:
            self._set_receiving(False)

    def _handle_sdk_message(self, msg: object) -> None:
        """Route an incoming SDK message to the appropriate TUI handler."""
        if isinstance(msg, StreamEvent):
            event = msg.event
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                delta_type = delta.get("type", "")
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        self.post_message(StreamText(text))
                elif delta_type == "thinking_delta":
                    thinking = delta.get("thinking", "")
                    if thinking:
                        self.post_message(StreamThinking(thinking))

        elif isinstance(msg, AssistantMessage):
            # Text and thinking content already handled via stream events.
            # Extract tool use blocks.
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    self._last_tool_name = block.name
                    self.post_message(ToolCallStart(block.name, block.input, block.id))

        elif isinstance(msg, UserMessage):
            # Tool results arrive as UserMessage content
            content = msg.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        self.post_message(
                            ToolCallResult(block.content, block.is_error, self._last_tool_name)
                        )

        elif isinstance(msg, ResultMessage):
            self.post_message(
                TurnComplete(
                    num_turns=msg.num_turns,
                    duration_ms=msg.duration_ms,
                    usage=msg.usage,
                )
            )

        elif isinstance(msg, SystemMessage):
            if msg.subtype not in ("init",):
                log = self.query_one("#chat-log", RichLog)
                log.write(f"[dim italic]System: {msg.subtype}[/dim italic]")

    # ---- Message handlers ----

    def on_stream_text(self, message: StreamText) -> None:
        """Append streamed text to the live streaming display."""
        was_empty = self._stream_buffer == ""
        self._stream_buffer += message.text

        streaming = self.query_one("#streaming-text", Static)

        if was_empty:
            # Flush any pending thinking first
            self._commit_thinking()
            # Show the streaming widget and add the assistant label to the log
            log = self.query_one("#chat-log", RichLog)
            log.write("")  # spacing
            log.write("[bold green]Assistant:[/bold green]")
            log.scroll_end(animate=False)
            streaming.add_class("visible")

        # Update the streaming display with the full buffer
        streaming.update(self._stream_buffer)

    def on_stream_thinking(self, message: StreamThinking) -> None:
        """Append streamed thinking text to the thinking buffer."""
        was_empty = self._thinking_buffer == ""
        self._thinking_buffer += message.text

        if was_empty:
            log = self.query_one("#chat-log", RichLog)
            log.write("")  # spacing
            log.write("[dim italic]Thinking...[/dim italic]")
            log.scroll_end(animate=False)

    def on_tool_call_start(self, message: ToolCallStart) -> None:
        """Show a tool call with its input details."""
        self._commit_stream()
        self._commit_thinking()
        log = self.query_one("#chat-log", RichLog)

        # Tool name header
        log.write(f"  [bold yellow]\u2192 {message.name}[/bold yellow]")

        # Formatted input details
        details = _format_tool_input(message.name, message.input)
        if details:
            for line in details.split("\n"):
                log.write(f"    [dim]{line}[/dim]")

        log.scroll_end(animate=False)

    def on_tool_call_result(self, message: ToolCallResult) -> None:
        """Show a tool result with truncated output."""
        log = self.query_one("#chat-log", RichLog)

        formatted = _format_tool_result(message.tool_name, message.content, message.is_error)
        for line in formatted.split("\n"):
            log.write(f"    {line}")

        log.scroll_end(animate=False)

    def on_turn_complete(self, message: TurnComplete) -> None:
        """Show turn stats and re-enable input."""
        self._commit_stream()
        self._commit_thinking()
        log = self.query_one("#chat-log", RichLog)

        # Extract token counts from usage dict
        usage = message.usage or {}
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        self._total_input_tokens += input_tok
        self._total_output_tokens += output_tok

        parts = [f"{message.num_turns} turns", f"{message.duration_ms}ms"]
        if input_tok or output_tok:
            parts.append(f"{_fmt_tokens(input_tok)} in / {_fmt_tokens(output_tok)} out")
        summary = "  |  ".join(parts)
        log.write(f"[dim]--- {summary} ---[/dim]")
        log.scroll_end(animate=False)

        # Refresh status bar with updated token totals
        self._update_status("Ready")

    # ---- Internal helpers ----

    def _commit_stream(self) -> None:
        """Move accumulated stream text into the permanent chat log."""
        if self._stream_buffer:
            log = self.query_one("#chat-log", RichLog)
            # Write the final text as permanent log entries
            for line in self._stream_buffer.split("\n"):
                log.write(line)
            log.scroll_end(animate=False)

        # Hide and clear the streaming widget
        streaming = self.query_one("#streaming-text", Static)
        streaming.remove_class("visible")
        streaming.update("")
        self._stream_buffer = ""

    def _commit_thinking(self) -> None:
        """Move accumulated thinking text into the permanent chat log (dimmed)."""
        if self._thinking_buffer:
            log = self.query_one("#chat-log", RichLog)
            for line in self._thinking_buffer.split("\n"):
                log.write(f"[dim italic]{line}[/dim italic]")
            log.scroll_end(animate=False)
        self._thinking_buffer = ""

    def _set_receiving(self, receiving: bool) -> None:
        """Toggle receiving state and update UI."""
        self._receiving = receiving
        input_box = self.query_one("#input-box", Input)

        if receiving:
            input_box.disabled = True
            self._update_status("Thinking...")
        else:
            input_box.disabled = False
            input_box.focus()
            self._update_status("Ready")

    def _update_status(self, state: str) -> None:
        """Update the status bar with state + session token usage."""
        status = self.query_one("#status-bar", Static)
        parts = [state, self._harness.agent_id]
        total = self._total_input_tokens + self._total_output_tokens
        if total:
            parts.append(f"{_fmt_tokens(total)} tokens")
        status.update("  |  ".join(parts))

    async def action_interrupt(self) -> None:
        """Interrupt the agent's current turn (Escape key)."""
        # Always re-focus input — Escape blurs it in Textual
        self.query_one("#input-box", Input).focus()
        if not self._receiving:
            return
        # Send interrupt signal — the SDK will finish the turn and yield
        # a ResultMessage, so the worker exits naturally via receive().
        try:
            await self._harness.interrupt()
        except Exception:
            pass
        # Show immediate visual feedback
        self._commit_stream()
        self._commit_thinking()
        log = self.query_one("#chat-log", RichLog)
        log.write("[dim italic]--- interrupted ---[/dim italic]")
        log.scroll_end(animate=False)

    async def action_quit(self) -> None:
        if self._receiving:
            try:
                await self._harness.interrupt()
            except Exception:
                pass
        try:
            await self._harness.stop()
        except Exception:
            pass
        self.exit()
