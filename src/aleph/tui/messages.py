"""Internal Textual messages for TUI component communication."""

from textual.message import Message


class StreamText(Message):
    """A chunk of streamed text to append to the current assistant message."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class StreamThinking(Message):
    """A chunk of streamed thinking/reasoning text."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ToolCallStart(Message):
    """The agent invoked a tool, with full input details."""

    def __init__(self, name: str, input: dict, tool_use_id: str) -> None:
        super().__init__()
        self.name = name
        self.input = input
        self.tool_use_id = tool_use_id


class ToolCallResult(Message):
    """A tool call returned a result."""

    def __init__(
        self,
        content: str | list | None,
        is_error: bool | None,
        tool_name: str,
    ) -> None:
        super().__init__()
        self.content = content
        self.is_error = is_error
        self.tool_name = tool_name


class TurnComplete(Message):
    """The agent's turn finished."""

    def __init__(
        self,
        num_turns: int,
        duration_ms: int,
        usage: dict | None,
    ) -> None:
        super().__init__()
        self.num_turns = num_turns
        self.duration_ms = duration_ms
        self.usage = usage


class HarnessReady(Message):
    """The harness has connected and is ready for input."""

    pass


class HarnessError(Message):
    """The harness encountered an error."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error
