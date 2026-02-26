"""Core harness — SDK integration and agent lifecycle."""

import os

# Allow launching from inside a Claude Code session (or another Aleph instance)
os.environ.pop("CLAUDECODE", None)
import platform
import uuid
from datetime import date, datetime
from pathlib import Path

import yaml

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
)

# Alias → full model ID. Used to resolve shorthand names (including "default")
# to the actual model string before building the system prompt.
# Update when Claude Code changes its default or new model families are released.
MODEL_ALIASES = {
    "default": "claude-opus-4-6",
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}

# Model ID prefix → knowledge cutoff date. Prefixes are matched in order,
# so more specific prefixes should come first.
KNOWLEDGE_CUTOFFS = {
    "claude-opus-4-6": "May 2025",
    "claude-opus-4-5": "May 2025",
    "claude-opus-4": "May 2025",
    "claude-sonnet-4-6": "May 2025",
    "claude-sonnet-4-5": "May 2025",
    "claude-sonnet-4": "May 2025",
    "claude-haiku-4-5": "May 2025",
    "claude-haiku-4": "May 2025",
    "claude-3-5": "Early 2024",
    "claude-3": "Early 2024",
}


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
    """Resolve a model name through aliases, falling back to the default alias."""
    if model is None:
        model = "default"
    return MODEL_ALIASES.get(model, model)


def _get_knowledge_cutoff(model: str) -> str:
    """Look up the knowledge cutoff for a model string by prefix match."""
    for prefix, cutoff in KNOWLEDGE_CUTOFFS.items():
        if model.startswith(prefix):
            return cutoff
    return "unknown"


from .config import ALLOWED_TOOLS, BASE_TOOLS, AlephConfig
from .hooks import (
    _build_session_recap,
    create_inbox_check_hook,
    create_read_tracking_hook,
    create_reminder_hook,
    create_skill_context_hook,
)
from .tools import create_aleph_mcp_server


class AlephHarness:
    """Manages a single Aleph agent session."""

    def __init__(self, config: AlephConfig):
        self.config = config
        self.agent_id = config.agent_id or f"aleph-{uuid.uuid4().hex[:8]}"
        self.session_id: str | None = None
        self._client: ClaudeSDKClient | None = None
        self._expected_model = _resolve_model(config.model)
        self._model_verified = False

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
            ctx += "\nUse `activate_skill` to load a skill before using it.\n"

        ctx += f"\nToday's date is **{date.today().strftime('%B %d, %Y')}**."

        # Inject memory context (hot tier) if it exists
        context_file = self.config.memory_path / "context.md"
        if context_file.exists():
            ctx += "\n\n---\n## Memory Context\n\n"
            ctx += context_file.read_text()

        # Inject handoff and session recap in a clearly demarcated block
        handoff_file = self.config.memory_path / "handoff.md"
        sessions_path = self.config.memory_path / "sessions"
        handoff_content = None
        recap_content = None

        if handoff_file.exists():
            handoff_content = handoff_file.read_text()
            handoff_file.unlink()

        recap_content = _build_session_recap(sessions_path)

        if handoff_content or recap_content:
            ctx += "\n\n---\n## Session Continuity\n\n"
            ctx += (
                "The following is context carried forward from previous sessions. "
                "Use it to orient yourself — what was recently worked on, what state "
                "things are in, and anything left unfinished.\n\n"
            )
            if handoff_content:
                ctx += "### Handoff\n\n"
                ctx += handoff_content
                ctx += "\n\n"
            if recap_content:
                ctx += "### Recent Sessions (today)\n\n"
                ctx += recap_content
                ctx += "\n"

        full_prompt = system_prompt + ctx

        # Set up inbox directory
        inbox = self.config.agent_inbox(self.agent_id)
        inbox.mkdir(parents=True, exist_ok=True)

        # Build hooks
        inbox_check = create_inbox_check_hook(inbox)
        read_tracker = create_read_tracking_hook(inbox)
        reminder = create_reminder_hook(interval=25)
        skill_context = create_skill_context_hook(self.config.skills_path)

        hooks = {
            "PostToolUse": [
                # Inbox check and periodic reminders on every tool call
                HookMatcher(matcher=None, hooks=[inbox_check, reminder]),
                # Read tracking only fires when the agent uses Read
                HookMatcher(matcher="Read", hooks=[read_tracker]),
                # Skill activation: replace MCP tool output with system context
                HookMatcher(matcher="mcp__aleph__activate_skill", hooks=[skill_context]),
            ],
        }

        # Build MCP server for framework tools
        aleph_server = create_aleph_mcp_server(self.config.inbox_path, self.config.skills_path)

        # tools controls which tool schemas the model sees (--tools flag).
        # allowed_tools is an additional execution-level whitelist (--allowedTools).
        # When ALLOWED_TOOLS is empty, all BASE_TOOLS are callable.
        tools = list(BASE_TOOLS)
        allowed = list(ALLOWED_TOOLS) + ["mcp__aleph__send_message"] if ALLOWED_TOOLS else []

        # Set working directory
        cwd = self.config.project or os.getcwd()

        # Environment: disable Claude Code's auto-memory + pre-activate canonical venv
        venv_path = self.config.home / "venv"
        env = {
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "ALEPH_HOME": str(self.config.home),
            "ALEPH_AGENT_ID": self.agent_id,
        }
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

    def check_model(self, actual_model: str) -> str | None:
        """Check the actual model from an AssistantMessage against our assumption.

        Called by the TUI on the first AssistantMessage. Returns a warning string
        if there's a mismatch, None if everything matches.
        """
        if self._model_verified:
            return None
        self._model_verified = True

        if actual_model == self._expected_model:
            return None

        # Model mismatch — our alias table is stale
        warning = (
            f"Model mismatch: expected '{self._expected_model}' "
            f"but got '{actual_model}'. "
            f"Update MODEL_ALIASES in harness.py."
        )

        # Check if the cutoff table covers this model
        cutoff = _get_knowledge_cutoff(actual_model)
        if cutoff == "unknown":
            warning += (
                f" Knowledge cutoff for '{actual_model}' is also unknown — "
                f"update KNOWLEDGE_CUTOFFS too."
            )

        return warning

    async def interrupt(self):
        """Interrupt the agent's current turn."""
        if self._client:
            await self._client.interrupt()

    def get_summary_prompt(self) -> str:
        """Return the prompt used to request a session summary."""
        today = date.today().strftime("%Y-%m-%d")
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        summary_path = self.config.memory_path / "sessions" / f"{today}-{self.agent_id}.md"
        memory_path = self.config.memory_path

        return (
            f"[Session ending] Before writing the session summary, reflect on "
            f"what you learned this session and update your memory files.\n\n"
            f"## Step 1: Memory updates\n\n"
            f"Review the session and update each file as needed:\n\n"
            f"- **{memory_path}/preferences.md** — Did the user express any "
            f"preferences about how they like to work, communicate, or make "
            f"decisions? What about tool preferences, style preferences, or "
            f"opinions? Add anything new.\n"
            f"- **{memory_path}/patterns.md** — Did you learn any lessons? "
            f"Hit any gotchas or anti-patterns? Discover something that worked "
            f"well? Did the user correct you on something? Add it.\n"
            f"- **{memory_path}/context.md** — Did you learn any durable "
            f"knowledge worth persisting? New project facts, key references, "
            f"important architectural details? This is for things you always "
            f"want to know, not recent state. Keep it under 50 lines.\n"
            f"- **Project memory** — If you worked on a project, does its "
            f"memory.md need updating with anything you learned about the "
            f"codebase, architecture, or conventions?\n\n"
            f"Don't skip this step. Even small observations compound over time. "
            f"If genuinely nothing was learned, that's fine — but think about "
            f"it first.\n\n"
            f"## Step 2: Session summary\n\n"
            f"Write a brief session summary to {summary_path}. "
            f"Start with YAML frontmatter, then the content:\n\n"
            f"```\n"
            f"---\n"
            f"agent: {self.agent_id}\n"
            f"timestamp: {now}\n"
            f"---\n"
            f"# {today} — <brief title> ({self.agent_id})\n\n"
            f"## Summary\n(1-2 sentences)\n\n"
            f"## Decisions\n(key decisions made, if any)\n\n"
            f"## Changes\n(what was built, modified, or configured)\n\n"
            f"## Open threads\n(what's unfinished or needs follow-up)\n"
            f"```\n"
        )

    def commit_memory(self) -> str | None:
        """Commit any changed memory/tools/skills to git.

        Runs synchronously (called at session end). Returns the commit
        summary line on success, None if nothing to commit or on error.
        Handles index lock contention with retries.
        """
        import subprocess
        import time

        repo = self.config.home
        if not (repo / ".git").exists():
            return None

        max_retries = 5
        for attempt in range(max_retries):
            try:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=repo, capture_output=True, timeout=10,
                )
                # Check if there's anything to commit
                result = subprocess.run(
                    ["git", "diff", "--cached", "--quiet"],
                    cwd=repo, capture_output=True, timeout=10,
                )
                if result.returncode == 0:
                    return None  # nothing to commit

                msg = f"Session end: {self.agent_id}"
                result = subprocess.run(
                    ["git", "commit", "-m", msg],
                    cwd=repo, capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    # Extract the summary line (first line of output)
                    return result.stdout.strip().split("\n")[0]
                else:
                    # Might be lock contention
                    if (repo / ".git" / "index.lock").exists():
                        raise FileExistsError("index.lock")
                    return None
            except (FileExistsError, subprocess.TimeoutExpired):
                if attempt < max_retries - 1:
                    time.sleep(1 * (2 ** attempt))  # exponential backoff
                continue
            except Exception:
                return None
        return None

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
