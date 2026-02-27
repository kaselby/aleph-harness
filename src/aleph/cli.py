"""Aleph CLI entry point."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid

from .config import ALEPH_HOME, AlephConfig
from .harness import AlephHarness

# Env var set inside the tmux session to prevent the inner aleph
# from trying to create another tmux session (infinite recursion).
_TMUX_GUARD = "ALEPH_IN_TMUX"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aleph",
        description="Aleph -- persistent self-improving agent harness",
    )
    parser.add_argument(
        "--id",
        help="Agent identifier (auto-generated if not provided)",
    )
    parser.add_argument(
        "--project",
        help="Project directory (sets working directory)",
    )
    parser.add_argument(
        "--model",
        help="Model to use (e.g. claude-sonnet-4-5)",
    )
    parser.add_argument(
        "--parent",
        help="Parent agent ID (for spawned subagents)",
    )
    parser.add_argument(
        "--prompt",
        help="Initial prompt (sent automatically on session start)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=0,
        help="Spawning depth (for recursion control)",
    )
    parser.add_argument(
        "--ephemeral",
        action="store_true",
        help="Ephemeral session: skip handoffs, session recaps, and exit summary",
    )
    parser.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="Continue the most recent session instead of starting fresh",
    )
    parser.add_argument(
        "--resume",
        metavar="AGENT_ID",
        help="Resume a specific session by agent ID (e.g. aleph-ed2331a5)",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Don't attach to the tmux session after launch",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List known sessions from the registry with their status",
    )
    return parser.parse_args()


def _build_inner_command(args: argparse.Namespace, agent_id: str) -> str:
    """Build the shell command that runs inside the tmux session."""
    cmd_parts = [shutil.which("aleph") or "aleph", "--id", agent_id]
    if args.project:
        cmd_parts += ["--project", args.project]
    if args.model:
        cmd_parts += ["--model", args.model]
    if args.parent:
        cmd_parts += ["--parent", args.parent]
    if args.prompt:
        cmd_parts += ["--prompt", args.prompt]
    if args.depth:
        cmd_parts += ["--depth", str(args.depth)]
    if args.ephemeral:
        cmd_parts.append("--ephemeral")
    if args.continue_session:
        cmd_parts.append("--continue")
    if args.resume:
        cmd_parts += ["--resume", args.resume]
    return shlex.join(cmd_parts)


def _launch_in_tmux(args: argparse.Namespace) -> None:
    """Launch aleph in a tmux session."""
    if not shutil.which("tmux"):
        print("Error: tmux is not installed. Install it with: brew install tmux")
        sys.exit(1)

    agent_id = args.resume or args.id or f"aleph-{uuid.uuid4().hex[:8]}"
    inner_cmd = _build_inner_command(args, agent_id)

    # Create the session detached, with the guard env var set
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", agent_id, "-e", f"{_TMUX_GUARD}=1", inner_cmd],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(f"Error launching tmux session: {stderr}")
        sys.exit(1)

    if args.detach:
        print(f"Aleph session started: {agent_id}")
        print(f"  tmux attach -t {agent_id}")
    else:
        # Attach to the session, replacing this process
        if os.environ.get("TMUX"):
            # Already inside tmux — switch client to avoid nesting
            os.execvp("tmux", ["tmux", "switch-client", "-t", agent_id])
        else:
            os.execvp("tmux", ["tmux", "attach", "-t", agent_id])


def _list_sessions() -> None:
    """Print known sessions from the registry, most recent first."""
    registry_path = ALEPH_HOME / "logs" / "session-registry.json"
    if not registry_path.exists():
        print("No session registry found.")
        return

    try:
        registry = json.loads(registry_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading registry: {e}")
        return

    if not registry:
        print("No sessions in registry.")
        return

    # Get running tmux sessions for status check
    running = set()
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        running = set(result.stdout.strip().splitlines())

    # Sort by started_at descending
    entries = sorted(
        registry.items(),
        key=lambda kv: kv[1].get("started_at", ""),
        reverse=True,
    )

    for agent_id, info in entries:
        status = "\033[32mrunning\033[0m" if agent_id in running else "\033[90mdead\033[0m"
        started = info.get("started_at", "?")[:19]  # trim to seconds
        model = info.get("model") or "default"
        print(f"  {agent_id:<24} {status:<20} {started}  {model}")


def main():
    args = parse_args()

    if args.list:
        _list_sessions()
        return

    # If we're not already inside our tmux session, launch through tmux.
    # Also bypass the guard when --detach is set — that means we're spawning
    # a peer agent and always want a new tmux session, even from inside one.
    if not os.environ.get(_TMUX_GUARD) or args.detach:
        _launch_in_tmux(args)
        return

    config = AlephConfig(
        agent_id=args.resume or args.id,
        model=args.model,
        project=args.project,
        prompt=args.prompt,
        parent=args.parent,
        depth=args.depth,
        ephemeral=args.ephemeral,
        continue_session=args.continue_session,
        resume_session=args.resume,
    )

    harness = AlephHarness(config)

    from .tui import AlephApp

    app = AlephApp(harness)
    app.run()

    if harness.restart_requested:
        # Replace this process with a fresh aleph invocation.
        # Clean shutdown (summary, archive, commit) already happened in app.run().
        # exec replaces the entire process image — clean slate, modules reloaded from disk.
        aleph_bin = shutil.which("aleph") or sys.argv[0]
        os.execvp(aleph_bin, [aleph_bin])


if __name__ == "__main__":
    main()
