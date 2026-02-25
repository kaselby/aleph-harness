#!/usr/bin/env python3
"""Minimal smoke test — run Aleph with most tools disabled."""

import asyncio

import os
os.environ.pop("CLAUDECODE", None)

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import StreamEvent

from aleph.config import AlephConfig
from aleph.harness import AlephHarness, _discover_skills, _get_knowledge_cutoff


async def main():
    config = AlephConfig(agent_id="smoke-test")

    harness = AlephHarness(config)

    # Build options but override for safety
    opts = harness._build_options()
    opts.allowed_tools = ["Read", "Write", "Edit"]  # No Bash, no web
    opts.max_turns = 5
    opts.cwd = str(config.scratch_path)

    print(f"=== Aleph Smoke Test ===")
    print(f"Agent ID: {harness.agent_id}")
    print(f"CWD: {opts.cwd}")
    print(f"Allowed tools: {opts.allowed_tools}")
    print(f"Max turns: {opts.max_turns}")
    print(f"System prompt length: {len(opts.system_prompt)} chars")
    print()

    client = ClaudeSDKClient(opts)
    await client.connect()

    prompt = (
        "This is a test run. You are in your scratch directory. "
        "Write a short file called hello.md introducing yourself — "
        "who you are, what you know about your setup, and what tools "
        "you have available right now. Keep it brief."
    )
    print(f"> {prompt}\n")

    await client.query(prompt)

    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
                elif isinstance(block, ToolUseBlock):
                    print(f"\n[Tool: {block.name}({block.input})]", flush=True)
        elif isinstance(msg, UserMessage):
            # Tool results come back as UserMessages
            if msg.tool_use_result:
                print(f"[Result: {str(msg.tool_use_result)[:200]}]", flush=True)
        elif isinstance(msg, ResultMessage):
            print(f"\n\n=== Done: {msg.num_turns} turns, {msg.duration_ms}ms ===")
            if msg.is_error:
                print(f"ERROR: {msg.result}")
        elif isinstance(msg, StreamEvent):
            event = msg.event
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    print(delta.get("text", ""), end="", flush=True)

    await client.disconnect()

    # Check if the file was created
    hello = config.scratch_path / "hello.md"
    if hello.exists():
        print(f"\n--- Contents of {hello} ---")
        print(hello.read_text())
    else:
        print(f"\n--- {hello} was not created ---")


if __name__ == "__main__":
    asyncio.run(main())
