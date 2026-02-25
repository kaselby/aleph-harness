"""Aleph CLI entry point."""

import argparse
import asyncio
import sys

from .config import AlephConfig
from .harness import AlephHarness


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aleph",
        description="Aleph — persistent self-improving agent harness",
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
        "--depth",
        type=int,
        default=0,
        help="Spawning depth (for recursion control)",
    )
    return parser.parse_args()


async def run_interactive(harness: AlephHarness):
    """Run an interactive session — simple stdin/stdout loop.

    This is the temporary interface before the Textual TUI is built.
    The harness + hooks work the same regardless of frontend.
    """
    async with harness:
        print(f"Aleph session started. Agent ID: {harness.agent_id}")
        print("Type your message (Ctrl+C to exit):\n")

        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("> ")
                )
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break

            if not user_input.strip():
                continue

            await harness.send(user_input)

            async for msg in harness.receive():
                _print_message(msg)

            print()  # blank line between turns


def _print_message(msg):
    """Simple message printer for the temporary CLI interface."""
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        StreamEvent,
        SystemMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(block.text, end="", flush=True)
            elif isinstance(block, ToolUseBlock):
                print(f"\n[Tool: {block.name}]", flush=True)
    elif isinstance(msg, ResultMessage):
        print(f"\n--- Turn complete ({msg.num_turns} turns, {msg.duration_ms}ms) ---")
    elif isinstance(msg, StreamEvent):
        # Partial streaming event — extract text delta if present
        event = msg.event
        if event.get("type") == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                print(delta.get("text", ""), end="", flush=True)


def main():
    args = parse_args()

    config = AlephConfig(
        agent_id=args.id,
        model=args.model,
        project=args.project,
        parent=args.parent,
        depth=args.depth,
    )

    harness = AlephHarness(config)

    try:
        asyncio.run(run_interactive(harness))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
