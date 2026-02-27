"""Configuration loading and defaults."""

from dataclasses import dataclass, field
from pathlib import Path

ALEPH_HOME = Path.home() / ".aleph"

# Base tool set — controls which tool schemas the model sees.
# This is passed as --tools to the CLI and determines what's in the model's context.
BASE_TOOLS = [
    # "Bash" — replaced by custom MCP tool (mcp__aleph__Bash)
    "Read",
    "Write",
    "Edit",
    "WebSearch",
    "WebFetch",
    # "TodoWrite" — not using built-in; planning via scratch files instead (see ALEPH.md)
]

# Execution-level whitelist — controls which tools the model can actually call.
# Passed as --allowedTools. Must be a subset of BASE_TOOLS + any MCP tools.
# When empty, all BASE_TOOLS are allowed.
ALLOWED_TOOLS = []


@dataclass
class AlephConfig:
    """Harness configuration."""

    home: Path = field(default_factory=lambda: ALEPH_HOME)

    # Agent identity
    agent_id: str | None = None

    # Project path (sets cwd and enables project-level context)
    project: str | None = None

    # Model
    model: str | None = None

    # Initial prompt (for non-interactive launch — currently unused, all sessions interactive)
    prompt: str | None = None

    # Spawning hierarchy
    parent: str | None = None
    depth: int = 0

    # Ephemeral mode — skip handoffs, session recaps, and exit summary
    ephemeral: bool = False

    # Continue the most recent session instead of starting fresh
    continue_session: bool = False

    @property
    def system_prompt_path(self) -> Path:
        return self.home / "ALEPH.md"

    @property
    def memory_path(self) -> Path:
        return self.home / "memory"

    @property
    def inbox_path(self) -> Path:
        return self.home / "inbox"

    @property
    def tools_path(self) -> Path:
        return self.home / "tools"

    @property
    def skills_path(self) -> Path:
        return self.home / "skills"

    @property
    def scratch_path(self) -> Path:
        return self.home / "scratch"

    def agent_inbox(self, agent_id: str) -> Path:
        return self.inbox_path / agent_id

    def load_system_prompt(self) -> str:
        """Load the system prompt from ALEPH.md. Returns empty string if missing."""
        path = self.system_prompt_path
        if path.exists():
            return path.read_text()
        return ""
