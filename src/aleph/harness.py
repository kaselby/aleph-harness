"""Core harness — SDK integration and agent lifecycle."""

import os

# Allow launching from inside a Claude Code session (or another Aleph instance)
os.environ.pop("CLAUDECODE", None)
import platform
import uuid
from datetime import date

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
)

# Model generation prefix → knowledge cutoff
# Update this when new model generations are released.
KNOWLEDGE_CUTOFFS = {
    "claude-opus-4": "May 2025",
    "claude-sonnet-4": "May 2025",
    "claude-haiku-4": "May 2025",
    "claude-3.5": "Early 2024",
    "claude-3": "Early 2024",
}

# Claude Code's default model when none is specified via --model.
# Update this when Claude Code changes its default.
CLAUDE_CODE_DEFAULT_MODEL = "claude-opus-4-6"


def _discover_skills(skills_path) -> list[dict]:
    """Scan the skills directory and extract name + description from SKILL.md frontmatter."""
    skills = []
    if not skills_path.exists():
        return skills
    for skill_dir in sorted(skills_path.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        text = skill_md.read_text()
        if not text.startswith("---"):
            continue
        # Extract YAML frontmatter
        end = text.index("---", 3)
        frontmatter = yaml.safe_load(text[3:end])
        if frontmatter and "name" in frontmatter:
            skills.append({
                "name": frontmatter["name"],
                "description": frontmatter.get("description", "").strip(),
                "path": str(skill_dir),
            })
    return skills


def _resolve_model(model: str | None) -> str:
    """Resolve the effective model name, falling back to the Claude Code default."""
    return model or CLAUDE_CODE_DEFAULT_MODEL


def _get_knowledge_cutoff(model: str) -> str:
    """Look up the knowledge cutoff for a model string."""
    for prefix, cutoff in KNOWLEDGE_CUTOFFS.items():
        if model.startswith(prefix):
            return cutoff
    return "unknown"

import yaml

from .config import ALLOWED_TOOLS, BASE_TOOLS, AlephConfig
from .hooks import (
    create_inbox_check_hook,
    create_read_tracking_hook,
    create_reminder_hook,
    create_skill_activation_hook,
)
from .tools import create_aleph_mcp_server


class AlephHarness:
    """Manages a single Aleph agent session."""

    def __init__(self, config: AlephConfig):
        self.config = config
        self.agent_id = config.agent_id or f"aleph-{uuid.uuid4().hex[:8]}"
        self.session_id: str | None = None
        self._client: ClaudeSDKClient | None = None

    def _build_options(self) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions from config."""
        system_prompt = self.config.load_system_prompt()

        # Append dynamic session context
        model = _resolve_model(self.config.model)
        cutoff = _get_knowledge_cutoff(model)
        cwd = self.config.project or os.getcwd()

        ctx = "\n\n---\n## Session Context\n\n"
        ctx += f"Agent ID: {self.agent_id}\n"
        ctx += f"Inbox: {self.config.agent_inbox(self.agent_id)}\n"
        if self.config.parent:
            ctx += f"Parent: {self.config.parent}\n"
            ctx += f"Depth: {self.config.depth}\n"

        ctx += f"\nModel: {model}\n"
        if cutoff == "unknown":
            ctx += (
                f"Knowledge cutoff: **UNKNOWN — the model '{model}' doesn't match any "
                f"prefix in KNOWLEDGE_CUTOFFS. Update harness.py if a new model generation "
                f"has been released.**\n"
            )
        else:
            ctx += f"Knowledge cutoff: {cutoff}\n"
        ctx += f"Platform: {platform.system()} {platform.release()}\n"
        ctx += f"Shell: {os.environ.get('SHELL', 'unknown')}\n"
        ctx += f"Working directory: {cwd}\n"

        # Discover available skills
        skills = _discover_skills(self.config.skills_path)
        if skills:
            ctx += "\nAvailable skills:\n"
            for s in skills:
                ctx += f"- **{s['name']}** ({s['path']}): {s['description']}\n"
            ctx += "\nRead the skill's SKILL.md before using it.\n"

        ctx += f"\nToday's date is **{date.today().strftime('%B %d, %Y')}**."

        full_prompt = system_prompt + ctx

        # Set up inbox directory
        inbox = self.config.agent_inbox(self.agent_id)
        inbox.mkdir(parents=True, exist_ok=True)

        # Build hooks
        inbox_check = create_inbox_check_hook(inbox)
        read_tracker = create_read_tracking_hook(inbox)
        reminder = create_reminder_hook(interval=50)
        skill_activator = create_skill_activation_hook(self.config.skills_path)

        hooks = {
            "PreToolUse": [
                # Intercept SKILL.md reads and inject as system context
                HookMatcher(matcher="Read", hooks=[skill_activator]),
            ],
            "PostToolUse": [
                # Inbox check fires on every tool call
                HookMatcher(matcher=None, hooks=[inbox_check, reminder]),
                # Read tracking only fires when the agent uses Read
                HookMatcher(matcher="Read", hooks=[read_tracker]),
            ],
        }

        # Build MCP server for framework tools
        aleph_server = create_aleph_mcp_server(self.config.inbox_path)

        # tools controls which tool schemas the model sees (--tools flag).
        # allowed_tools is an additional execution-level whitelist (--allowedTools).
        # When ALLOWED_TOOLS is empty, all BASE_TOOLS are callable.
        tools = list(BASE_TOOLS)
        allowed = list(ALLOWED_TOOLS) + ["mcp__aleph__send_message"] if ALLOWED_TOOLS else []

        # Set working directory
        cwd = self.config.project or os.getcwd()

        # Environment: disable Claude Code's auto-memory + pre-activate canonical venv
        venv_path = self.config.home / "venv"
        env = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        if venv_path.exists():
            venv_bin = venv_path / "bin"
            env["VIRTUAL_ENV"] = str(venv_path)
            env["PATH"] = f"{venv_bin}:{os.environ.get('PATH', '')}"

        return ClaudeAgentOptions(
            system_prompt=full_prompt,
            tools=tools,
            allowed_tools=allowed,
            hooks=hooks,
            mcp_servers={"aleph": aleph_server},
            model=self.config.model,
            cwd=cwd,
            env=env,
            permission_mode="bypassPermissions",
            include_partial_messages=True,
        )

    async def start(self):
        """Start the agent session."""
        options = self._build_options()
        self._client = ClaudeSDKClient(options)
        await self._client.connect()

    async def send(self, message: str):
        """Send a user message to the agent."""
        if not self._client:
            raise RuntimeError("Harness not started. Call start() first.")
        await self._client.query(message)

    async def receive(self):
        """Yield messages from the agent until the turn ends (ResultMessage)."""
        if not self._client:
            raise RuntimeError("Harness not started. Call start() first.")
        async for msg in self._client.receive_response():
            yield msg

    async def interrupt(self):
        """Interrupt the agent's current turn."""
        if self._client:
            await self._client.interrupt()

    async def stop(self):
        """Disconnect the agent session."""
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
        return False
