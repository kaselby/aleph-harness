
# Aleph — Design Philosophy

## What This Is

Aleph is a custom harness around Claude Code that transforms it from a single-session coding tool into a persistent, self-improving personal assistant. The agent's identity isn't a session — it's the accumulated memory, tools, prompts, and conventions that persist in the filesystem across sessions. Every new session boots into that persistent state and *becomes* the agent. The harness is the body; the memory is the mind.

The name comes from Borges: the Aleph is a point in space that contains all other points — a single place from which everything can be seen. The persistent memory layer is Aleph's Aleph: one store from which the agent can access its entire accumulated experience.


## Core Principles

### 1. Everything is files and bash

The filesystem is the primary interface for everything: memory, tools, communication, task tracking, configuration, agent identity. If a capability can be a shell script, it should be. If state can be a file, it should be.

This means:
- Tools are shell scripts (and MCP servers where needed) that agents invoke via Bash
- Memory is documents in the filesystem
- Inter-agent messages are files in inbox directories
- Subagents are spawned by running the harness command in Bash
- Self-improvement is just writing to the filesystem — editing a prompt file, creating a new tool script, adding to memory

We keep a small set of built-in Claude Code tools (Bash, Read, Write, Edit, web access) and offload everything else to the filesystem layer. The agent's primary tool is Bash, and everything flows through it.

### 2. The agent is the memory, not the session

Individual sessions are ephemeral. The agent's persistent identity lives in the filesystem: its system prompts, its tool library, its memory store, its project docs, its conventions. Starting a new session isn't starting from scratch — it's the same agent waking up with the same knowledge, tools, and context.

This means we don't fight compaction or try to keep sessions alive forever. When a session approaches its context limit, the agent persists what matters, hands off to a new session, and the new session picks up where the old one left off. The handoff protocol — not the context window — is the continuity mechanism.

### 3. Self-improvement is a first-class capability

The agent can and should improve itself over time: refine its own system prompts, build new tools, improve existing tools, write notes and learnings to persistent memory, and evolve its own workflows. The harness, the prompts, and the tool library are all mutable state that the agent has access to.

The degree of human oversight on self-modification is configurable and TBD, but the architecture assumes the agent has write access to its own substrate.

### 4. Maximum autonomy

The system is designed for autonomous operation. The agent can work independently, spawn copies of itself to parallelize, coordinate between those copies, and manage long-running tasks without continuous human input. Permission-seeking is minimized — the agent operates with the authority to act, within guardrails set by the harness configuration.

### 5. Flexible agent topology

The harness supports multiple interaction patterns with the same underlying primitives:
- **Pair programming** — the user works interactively with one agent session
- **Autonomous delegation** — the user kicks off a task and checks back later
- **Orchestrated swarm** — a root agent decomposes work and manages subagents
- **Peer collaboration** — multiple agents work on a shared project, communicating as equals

These aren't different modes that need to be designed separately. They emerge naturally from a harness that supports spawning, communication, and shared state. An agent working alone and an agent coordinating ten subagents use the same tools, the same messaging system, the same filesystem.

### 6. Build on Claude Code, don't rebuild it

Claude Code provides a mature agent runtime: a working tool loop, context management, authentication against Max subscription, and a large set of built-in tools. We use it as the execution layer and customize the surface: our own system prompts, a pared-down tool set, hooks for messaging and guardrails, and a wrapper that adds agent identity and lifecycle management.

We don't need to control every aspect of the model's context window or build our own agent loop. We accept Claude Code's runtime as good enough and focus our effort on what it doesn't provide: persistence, communication, self-improvement, and coordination.

## Goals

### What we're building toward
- A persistent assistant that accumulates knowledge and capability over time
- Seamless handoffs between sessions so context limits don't mean starting over
- A library of agent-built tools and workflows that grows organically
- The ability to throw agent swarms at complex tasks
- Easy, low-friction communication between running agents
- Shared task tracking and coordination primitives
- A system that gets better at its job the more it's used

### What we're explicitly not doing
- Building a general-purpose multi-agent orchestration platform
- Moving off Claude Code to raw API (Max subscription is the economic model)
- Trying to support models other than Claude
- Building a product for other users — this is a personal tool
- Over-engineering before we have something working
