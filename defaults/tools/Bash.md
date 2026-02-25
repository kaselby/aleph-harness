### Bash

Executes bash commands. The shell environment resets between invocations — env vars, aliases, venv activations, and other state do not persist. Only the working directory carries over. Chain dependent commands with `&&` in a single call rather than relying on state from a previous one.

- You can specify an optional timeout in milliseconds (up to 600000ms / 10 minutes). Default is 120000ms (2 minutes).
- If output exceeds 30000 characters, it will be truncated.
- Write a clear, concise description of what each command does. For simple commands, keep it brief. For complex or piped commands, add enough context to clarify intent.
- You can use `run_in_background` to run a command in the background when you don't need the result immediately.
- When issuing multiple independent commands, make multiple Bash tool calls in parallel. When commands depend on each other, chain them with `&&` in a single call.
