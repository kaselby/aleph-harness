"""Aleph TUI — scrollback-mode terminal interface.

Uses prompt_toolkit's Application (full_screen=False) for persistent keybinding
handling: Escape to interrupt, Ctrl+C to quit, Enter to submit.  Styled output
goes to the terminal's normal scrollback buffer via print_formatted_text so
native text selection and scrolling work naturally.

Responses are not streamed live — tokens accumulate silently and the full
markdown-rendered response prints to scrollback when the turn completes (or
when a tool call begins).  Multi-agent composition is handled by tmux, not
the TUI — this is a single-agent interface.
"""

from __future__ import annotations

import asyncio
import json
from markdown_it import MarkdownIt

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
# Register Shift+Enter (CSI u: \x1b[13;2u) for terminals that support it.
# Map to an unused function key so we can bind it alongside Alt+Enter.
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.keys import Keys
ANSI_SEQUENCES["\x1b[13;2u"] = Keys.F20       # kitty/CSI u protocol
ANSI_SEQUENCES["\x1b[27;2;13~"] = Keys.F20    # xterm modifyOtherKeys format
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML, FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import Style

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import StreamEvent

from ..harness import AlephHarness

# Max lines of tool result output to show inline
TOOL_RESULT_MAX_LINES = 10

# Semantic style map for the TUI
TUI_STYLE = Style.from_dict({
    "user": "ansicyan bold",
    "assistant": "ansigreen bold",
    "tool": "ansiyellow bold",
    "dim": "#888888",
    "dim-i": "#888888 italic",
    "err": "ansired",
    "err-b": "ansired bold",
    "text": "#cccccc",
    "text-heading": "#e0e0e0 bold",
    "md-code": "#88c0d0",
})


def _tprint(html_str: str, *args, **kwargs) -> None:
    """Print styled text to scrollback above the Application layout.

    Uses prompt_toolkit's print_formatted_text which handles its own
    run_in_terminal coordination. HTML.format() auto-escapes arguments.
    """
    html = HTML(html_str)
    if args or kwargs:
        html = html.format(*args, **kwargs)
    print_formatted_text(html, style=TUI_STYLE)


# ---- Markdown rendering ----
#
# Uses markdown-it-py to parse complete text into tokens, then converts
# to prompt_toolkit FormattedText. Runs at commit time.

_md = MarkdownIt("commonmark").enable("table")

_StyleTuples = list[tuple[str, str]]


def _markdown_to_ft(text: str) -> FormattedText:
    """Convert markdown text to FormattedText via markdown-it-py."""
    tokens = _md.parse(text)
    result: _StyleTuples = []
    _render_block_tokens(tokens, result)
    # Trim trailing newlines
    while result and result[-1][1] == "\n":
        result.pop()
    return FormattedText(result)


def _render_block_tokens(tokens: list, result: _StyleTuples) -> None:
    """Walk the flat block-level token list and render into styled tuples."""
    i = 0
    style_ctx: list[str] = []  # block-level styles (e.g. heading → bold)
    list_stack: list[tuple[str, int]] = []  # ("bullet"|"ordered", counter)

    while i < len(tokens):
        tok = tokens[i]

        # --- Headings ---
        if tok.type == "heading_open":
            style_ctx.append("class:text-heading")
        elif tok.type == "heading_close":
            style_ctx.pop()
            result.append(("", "\n"))

        # --- Paragraphs ---
        elif tok.type == "paragraph_open":
            pass
        elif tok.type == "paragraph_close":
            if not tok.hidden:
                result.append(("", "\n"))

        # --- Inline content ---
        elif tok.type == "inline":
            _render_inline(tok.children or [], result, list(style_ctx))

        # --- Fenced code blocks ---
        elif tok.type == "fence":
            lang = tok.info.strip()
            if lang:
                result.append(("class:dim-i", f"  {lang}\n"))
            for line in tok.content.rstrip("\n").split("\n"):
                result.append(("class:md-code", f"  {line}\n"))

        # --- Indented code blocks ---
        elif tok.type == "code_block":
            for line in tok.content.rstrip("\n").split("\n"):
                result.append(("class:md-code", f"  {line}\n"))

        # --- Lists ---
        elif tok.type == "bullet_list_open":
            list_stack.append(("bullet", 0))
        elif tok.type == "bullet_list_close":
            if list_stack:
                list_stack.pop()
        elif tok.type == "ordered_list_open":
            list_stack.append(("ordered", 0))
        elif tok.type == "ordered_list_close":
            if list_stack:
                list_stack.pop()
        elif tok.type == "list_item_open":
            if list_stack:
                kind, count = list_stack[-1]
                count += 1
                list_stack[-1] = (kind, count)
                indent = "  " * len(list_stack)
                if kind == "bullet":
                    result.append(("class:text", f"{indent}\u2022 "))
                else:
                    result.append(("class:text", f"{indent}{count}. "))
        elif tok.type == "list_item_close":
            result.append(("", "\n"))

        # --- Blockquotes ---
        elif tok.type == "blockquote_open":
            style_ctx.append("class:dim")
        elif tok.type == "blockquote_close":
            if "class:dim" in style_ctx:
                style_ctx.remove("class:dim")

        # --- Horizontal rules ---
        elif tok.type == "hr":
            result.append(("class:dim", "\u2500" * 40 + "\n"))

        # --- Tables ---
        elif tok.type == "table_open":
            table_tokens = []
            i += 1
            while i < len(tokens) and tokens[i].type != "table_close":
                table_tokens.append(tokens[i])
                i += 1
            _render_table(table_tokens, result)

        # --- HTML blocks (show raw) ---
        elif tok.type == "html_block":
            result.append(("class:dim", tok.content))

        i += 1


def _render_inline(
    children: list, result: _StyleTuples, style_stack: list[str]
) -> None:
    """Render inline token children with a style stack for nesting."""
    for tok in children:
        if tok.type == "text":
            parts = list(style_stack) if style_stack else []
            # Ensure text color unless an explicit class is already set
            if not any(p.startswith("class:") for p in parts):
                parts.insert(0, "class:text")
            result.append((" ".join(parts), tok.content))
        elif tok.type == "strong_open":
            style_stack.append("bold")
        elif tok.type == "strong_close":
            if "bold" in style_stack:
                style_stack.remove("bold")
        elif tok.type == "em_open":
            style_stack.append("italic")
        elif tok.type == "em_close":
            if "italic" in style_stack:
                style_stack.remove("italic")
        elif tok.type == "code_inline":
            result.append(("class:md-code", tok.content))
        elif tok.type in ("softbreak", "hardbreak"):
            result.append(("", "\n"))
        elif tok.type == "link_open":
            pass  # text shows via child text tokens
        elif tok.type == "link_close":
            pass
        elif tok.type == "image":
            result.append(("class:dim", f"[image: {tok.content}]"))


def _render_table(tokens: list, result: _StyleTuples) -> None:
    """Render table tokens with aligned columns."""
    rows: list[tuple[bool, list[str]]] = []  # (is_header, cells)
    current_row: list[str] = []
    in_header = False

    for tok in tokens:
        if tok.type == "thead_open":
            in_header = True
        elif tok.type == "thead_close":
            in_header = False
        elif tok.type == "tr_open":
            current_row = []
        elif tok.type == "tr_close":
            rows.append((in_header, current_row))
        elif tok.type == "inline":
            current_row.append(_inline_to_plain(tok.children or []))

    if not rows:
        return

    num_cols = max(len(cells) for _, cells in rows)
    col_widths = [0] * num_cols
    for _, cells in rows:
        for j, cell in enumerate(cells):
            col_widths[j] = max(col_widths[j], len(cell))

    for is_hdr, cells in rows:
        padded = [
            (cells[j] if j < len(cells) else "").ljust(col_widths[j])
            for j in range(num_cols)
        ]
        line = "  " + " \u2502 ".join(padded) + "\n"
        if is_hdr:
            result.append(("class:text-heading", line))
            sep = "  " + "\u2500\u253c\u2500".join(
                "\u2500" * w for w in col_widths
            ) + "\n"
            result.append(("class:dim", sep))
        else:
            result.append(("class:text", line))


def _inline_to_plain(children: list) -> str:
    """Extract plain text from inline children (for table cell measurement)."""
    parts = []
    for tok in children:
        if tok.type == "text":
            parts.append(tok.content)
        elif tok.type == "code_inline":
            parts.append(tok.content)
        elif tok.type == "softbreak":
            parts.append(" ")
    return "".join(parts)


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
    """Scrollback-mode terminal interface for Aleph.

    Uses a prompt_toolkit Application (full_screen=False) for persistent
    keybinding handling. Styled output goes to scrollback via print_formatted_text.
    Responses print in full at commit time (no live streaming).
    """

    def __init__(self, harness: AlephHarness) -> None:
        self._harness = harness
        self._stream_chunks: list[str] = []
        self._thinking_buffer = ""
        self._tool_name_queue: list[str] = []
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._context_tokens = 0  # latest API call's total input ≈ current context size
        self._last_call_usage = {}  # per-API-call usage from message_delta events
        self._receiving = False
        self._interrupt_in_flight = False
        self._app: Application | None = None

        # Build the prompt_toolkit Application
        self._input_buffer = Buffer(multiline=True)
        kb = self._build_keybindings()

        layout = Layout(
            HSplit([
                Window(
                    BufferControl(buffer=self._input_buffer),
                    height=D(min=1, max=10),
                    wrap_lines=True,
                    dont_extend_height=True,
                    get_line_prefix=self._input_prefix,
                ),
                Window(FormattedTextControl(self._toolbar), height=1),
            ])
        )

        self._app = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=False,
            style=TUI_STYLE,
        )

    def _input_prefix(self, line_number: int, wrap_count: int) -> list[tuple[str, str]]:
        """Prefix for input lines: '> ' on first line, '  ' on continuations."""
        if line_number == 0 and wrap_count == 0:
            return [("", "> ")]
        return [("", "  ")]

    def _build_keybindings(self) -> KeyBindings:
        """Create keybindings with state-based filters."""
        kb = KeyBindings()
        app_ref = self  # closure reference

        @Condition
        def is_receiving():
            return app_ref._receiving

        @Condition
        def is_idle():
            return not app_ref._receiving

        # Enter submits input (only when not receiving a response)
        @kb.add("enter", filter=is_idle)
        def handle_enter(event):
            text = app_ref._input_buffer.text.strip()
            if not text:
                return

            app_ref._input_buffer.reset()

            if text == "/exit":
                event.app.exit()
                return

            # Lock out further submissions immediately (before ensure_future yields)
            app_ref._receiving = True
            if app_ref._app:
                app_ref._app.invalidate()

            # Print the user's message
            _tprint("<user>You:</user> {}", text)

            # Run response as background task — Application keeps processing keys
            asyncio.ensure_future(app_ref._send_and_receive(text))

        # Enter while receiving — suppress default multiline newline insertion
        @kb.add("enter", filter=is_receiving)
        def handle_enter_receiving(event):
            pass

        # Newline insertion: Alt+Enter, Shift+Enter, or CSI u Shift+Enter
        @kb.add("escape", "enter")  # Alt+Enter (universal)
        @kb.add("c-j")              # \n from iTerm2 Shift+Enter mapping
        @kb.add(Keys.F20)           # CSI u Shift+Enter (kitty/WezTerm/Ghostty)
        def handle_newline(event):
            event.current_buffer.newline()

        # Escape interrupts the current response
        @kb.add("escape", filter=is_receiving)
        def handle_escape(event):
            asyncio.ensure_future(app_ref._do_interrupt())

        # Ctrl+C exits (interrupts first if receiving)
        @kb.add("c-c")
        async def handle_quit(event):
            if app_ref._receiving:
                await app_ref._do_interrupt()
            event.app.exit()

        return kb

    def _toolbar(self) -> HTML:
        """Build the persistent bottom toolbar content."""
        if self._receiving:
            status = "Working..."
        else:
            status = "Ready"
        parts = [status, self._harness.agent_id]
        if self._context_tokens:
            parts.append(f"{_fmt_tokens(self._context_tokens)} / 200k")

        if self._receiving:
            parts.append("Esc to interrupt")

        return HTML(f" {' | '.join(parts)}")

    def run(self) -> None:
        """Run the TUI event loop."""
        asyncio.run(self._main())

    async def _main(self) -> None:
        """Main async loop: connect, then run the Application."""
        _tprint("<dim>Connecting...</dim>")

        try:
            await self._harness.start()
        except Exception as e:
            _tprint("<err-b>Connection error:</err-b> {}", str(e))
            return

        _tprint("<dim>Session started: {}</dim>\n", self._harness.agent_id)

        try:
            with patch_stdout():
                # Auto-send initial prompt if provided (e.g. subagent launch)
                initial_prompt = self._harness.config.prompt
                if initial_prompt:
                    _tprint("<user>Prompt:</user> {}", initial_prompt)

                    async def send_initial():
                        await asyncio.sleep(0)  # yield once to let Application start
                        await self._send_and_receive(initial_prompt)

                    asyncio.ensure_future(send_initial())

                await self._app.run_async()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            _tprint("\n<dim>Saving session summary...</dim>")
            try:
                await self._harness.send(self._harness.get_summary_prompt())
                async for _ in self._harness.receive():
                    pass
            except Exception:
                pass
            _tprint("<dim>Disconnecting...</dim>")
            await self._harness.stop()

    async def _send_and_receive(self, text: str) -> None:
        """Send a message and render the full response."""
        self._stream_chunks = []
        self._thinking_buffer = ""
        # _receiving is set True by the caller (handle_enter) synchronously
        # to prevent race conditions with double-Enter.
        self._receiving = True

        try:
            await self._harness.send(text)

            async for msg in self._harness.receive():
                self._handle_sdk_message(msg)

            self._commit_stream()
            self._commit_thinking()

        except Exception as e:
            _tprint("\n<err-b>Error:</err-b> {}", str(e))
        finally:
            self._receiving = False
            self._interrupt_in_flight = False
            if self._app:
                self._app.invalidate()

    async def _do_interrupt(self) -> None:
        """Interrupt the current response."""
        if not self._receiving or self._interrupt_in_flight:
            return
        self._interrupt_in_flight = True
        self._commit_stream()
        self._commit_thinking()
        _tprint("\n<dim-i>--- interrupted ---</dim-i>")
        try:
            await self._harness.interrupt()
        except Exception:
            pass

    def _handle_sdk_message(self, msg: object) -> None:
        """Route an incoming SDK message to the appropriate handler."""
        if isinstance(msg, StreamEvent):
            event = msg.event
            etype = event.get("type", "")
            if etype == "content_block_delta":
                delta = event.get("delta", {})
                delta_type = delta.get("type", "")
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        if not self._stream_chunks:
                            self._commit_thinking()
                        self._stream_chunks.append(text)
                elif delta_type == "thinking_delta":
                    thinking = delta.get("thinking", "")
                    if thinking:
                        self._on_stream_thinking(thinking)
            elif etype == "message_delta":
                # Per-API-call usage — track the latest for context display
                usage = event.get("usage", {})
                if usage:
                    self._last_call_usage = usage

        elif isinstance(msg, AssistantMessage):
            # Verify model on first response
            warning = self._harness.check_model(msg.model)
            if warning:
                _tprint("\n<err-b>Warning:</err-b> <err>{}</err>", warning)

            for block in msg.content:
                if isinstance(block, TextBlock) and block.text:
                    # Fallback: capture text from the final message in case
                    # StreamEvent deltas weren't sent (e.g. no partial messages).
                    if not self._stream_chunks:
                        self._stream_chunks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    self._tool_name_queue.append(block.name)
                    self._on_tool_call_start(block.name, block.input)

        elif isinstance(msg, UserMessage):
            content = msg.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        tool_name = self._tool_name_queue.pop(0) if self._tool_name_queue else ""
                        self._on_tool_call_result(
                            tool_name, block.content, block.is_error
                        )

        elif isinstance(msg, ResultMessage):
            self._on_turn_complete(msg)

        elif isinstance(msg, SystemMessage):
            if msg.subtype not in ("init",):
                _tprint("<dim-i>System: {}</dim-i>", msg.subtype)

    # ---- Rendering ----

    def _on_stream_thinking(self, text: str) -> None:
        """Handle a chunk of streamed thinking text."""
        if not self._thinking_buffer:
            _tprint("\n<dim-i>Thinking...</dim-i>")

        self._thinking_buffer += text

    def _on_tool_call_start(self, name: str, input: dict) -> None:
        """Render a tool call with its input details."""
        self._commit_stream()
        self._commit_thinking()

        details = _format_tool_input(name, input)
        if details:
            indented = "\n".join(f"    {line}" for line in details.split("\n"))
            _tprint("\n  <tool>\u2192 {}</tool>\n<dim>{}</dim>", name, indented)
        else:
            _tprint("\n  <tool>\u2192 {}</tool>", name)

    def _on_tool_call_result(
        self, name: str, content: str | list | None, is_error: bool | None
    ) -> None:
        """Render a tool result."""
        formatted = _format_tool_result(name, content, is_error)
        indented = "\n".join(f"    {line}" for line in formatted.split("\n"))
        if is_error:
            _tprint("<err>{}</err>", indented)
        else:
            _tprint("<dim>{}</dim>", indented)

    def _on_turn_complete(self, msg: ResultMessage) -> None:
        """Render turn completion stats."""
        self._commit_stream()
        self._commit_thinking()

        # Use per-API-call usage from the last message_delta for context tracking.
        # ResultMessage.usage is aggregated across all API calls in the tool loop,
        # so it overstates context size for multi-turn interactions.
        last = self._last_call_usage
        if last:
            self._context_tokens = (
                last.get("input_tokens", 0)
                + last.get("cache_read_input_tokens", 0)
                + last.get("cache_creation_input_tokens", 0)
            )

        # Aggregated stats for the summary line
        usage = msg.usage or {}
        input_tok = (
            usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
        output_tok = usage.get("output_tokens", 0)
        self._total_input_tokens += input_tok
        self._total_output_tokens += output_tok

        parts = [f"{msg.num_turns} turns", f"{msg.duration_ms}ms"]
        if input_tok or output_tok:
            parts.append(f"{_fmt_tokens(input_tok)} in / {_fmt_tokens(output_tok)} out")
        summary = "  |  ".join(parts)
        _tprint("\n<dim>--- {} ---</dim>", summary)

        self._last_call_usage = {}

    # ---- Helpers ----

    def _commit_stream(self) -> None:
        """Render accumulated text as markdown and print to scrollback."""
        if self._stream_chunks:
            full_text = "".join(self._stream_chunks)
            _tprint("\n<assistant>Aleph:</assistant>")
            print_formatted_text(_markdown_to_ft(full_text), style=TUI_STYLE)
            self._stream_chunks = []

    def _commit_thinking(self) -> None:
        """Flush the thinking buffer as dimmed text."""
        if self._thinking_buffer:
            _tprint("<dim-i>{}</dim-i>", self._thinking_buffer)
            self._thinking_buffer = ""
