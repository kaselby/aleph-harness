"""Configuration loading and defaults."""

from dataclasses import dataclass, field
from pathlib import Path

ALEPH_HOME = Path.home() / ".aleph"

# Tools the agent is allowed to use
ALLOWED_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "WebSearch",
    "WebFetch",
]


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

    @property
    def system_prompt_path(self) -> Path:
        return self.home / "ALEPH.md"

    @property
    def memory_path(self) -> Path:
        return self.home / "memory.md"

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
