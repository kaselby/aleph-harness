"""Aleph CLI entry point."""

import argparse

from .config import AlephConfig
from .harness import AlephHarness


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
    return parser.parse_args()


def main():
    args = parse_args()

    config = AlephConfig(
        agent_id=args.id,
        model=args.model,
        project=args.project,
        prompt=args.prompt,
        parent=args.parent,
        depth=args.depth,
        ephemeral=args.ephemeral,
        continue_session=args.continue_session,
    )

    harness = AlephHarness(config)

    from .tui import AlephApp

    app = AlephApp(harness)
    app.run()


if __name__ == "__main__":
    main()
