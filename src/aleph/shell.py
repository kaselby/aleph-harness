"""Persistent bash subprocess with sentinel-based output capture.

Provides a long-lived bash process that maintains state (env vars, cwd,
aliases) across commands. Output is captured via a UUID sentinel protocol
that reliably separates command output from the exit code.
"""

import asyncio
import os
import uuid
from datetime import datetime
from time import monotonic


class PersistentShell:
    """A persistent bash subprocess that maintains state across commands."""

    def __init__(self, cwd: str | None = None, env: dict[str, str] | None = None):
        self._cwd = cwd or os.getcwd()
        self._env = self._build_env(env)
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _build_env(overrides: dict[str, str] | None) -> dict[str, str]:
        """Build a clean environment, stripping CLAUDE* vars."""
        base = dict(os.environ)
        # Strip SDK vars so subprocesses don't think they're inside Claude
        for key in list(base):
            if key.startswith("CLAUDE"):
                del base[key]
        if overrides:
            base.update(overrides)
        return base

    async def _spawn(self):
        """Spawn (or respawn) the bash subprocess."""
        self._process = await asyncio.create_subprocess_exec(
            "bash", "--norc", "--noprofile",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
            cwd=self._cwd,
            env=self._env,
        )

    async def _ensure_alive(self):
        """Ensure the subprocess is running, respawning if needed."""
        if self._process is None or self._process.returncode is not None:
            await self._spawn()

    async def run(self, command: str, timeout_ms: int = 120_000) -> dict:
        """Run a command and return its output, exit code, and metadata.

        Returns:
            {
                "output": str,       # stdout+stderr combined
                "exit_code": int,
                "cwd": str,          # working directory after command
                "timestamp": str,    # ISO timestamp when command started
                "elapsed_ms": int,   # wall-clock milliseconds
                "timed_out": bool,
            }
        """
        async with self._lock:
            await self._ensure_alive()
            proc = self._process
            assert proc and proc.stdin and proc.stdout

            sentinel = f"___ALEPH_{uuid.uuid4().hex}___"
            timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            start = monotonic()

            # Write the command, then emit sentinel with exit code and cwd
            # The printf ensures the sentinel is on its own line even if
            # the command doesn't end with a newline
            wrapped = (
                f"{command}\n"
                f"__aleph_ec=$?\n"
                f"printf '\\n{sentinel}%s %s\\n' \"$__aleph_ec\" \"$(pwd)\"\n"
            )
            proc.stdin.write(wrapped.encode())
            await proc.stdin.drain()

            # Read until we see the sentinel
            timeout_s = timeout_ms / 1000.0
            output_lines = []
            timed_out = False

            exit_code = -1
            cwd = self._cwd

            async def _read_until_sentinel():
                nonlocal exit_code, cwd
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        # EOF — process died
                        break
                    decoded = line.decode("utf-8", errors="replace")
                    if sentinel in decoded:
                        # Parse exit code and cwd from sentinel line
                        after = decoded.split(sentinel)[1].strip()
                        parts = after.split(" ", 1)
                        try:
                            exit_code = int(parts[0]) if parts else -1
                        except ValueError:
                            exit_code = -1
                        cwd = parts[1] if len(parts) > 1 else self._cwd
                        self._cwd = cwd
                        break
                    output_lines.append(decoded)

            try:
                await asyncio.wait_for(_read_until_sentinel(), timeout=timeout_s)
            except asyncio.TimeoutError:
                timed_out = True
                exit_code = -1
                cwd = self._cwd
                # Kill the timed-out command — send SIGINT then SIGKILL
                try:
                    proc.send_signal(2)  # SIGINT
                    await asyncio.sleep(0.5)
                    if proc.returncode is None:
                        proc.kill()
                except ProcessLookupError:
                    pass
                # Respawn on next call
                self._process = None

            elapsed_ms = int((monotonic() - start) * 1000)

            output = "".join(output_lines)
            # Truncate if excessively long (30k chars, matching built-in)
            if len(output) > 30_000:
                output = output[:30_000] + "\n... [output truncated at 30000 chars]"

            return {
                "output": output,
                "exit_code": exit_code,
                "cwd": cwd,
                "timestamp": timestamp,
                "elapsed_ms": elapsed_ms,
                "timed_out": timed_out,
            }

    async def restart(self):
        """Kill the current shell and let the next command spawn a fresh one.

        Use this to recover from a corrupted shell state (e.g. a command
        that broke the sentinel protocol or left the process unresponsive).
        Resets cwd to a known-good directory to avoid spawn failures.
        """
        async with self._lock:
            if self._process and self._process.returncode is None:
                self._process.kill()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
            self._process = None
            # Reset cwd — a corrupted sentinel parse can leave _cwd as garbage,
            # which makes _spawn fail with ENOENT.
            if not os.path.isdir(self._cwd):
                self._cwd = os.path.expanduser("~")

    async def close(self):
        """Terminate the subprocess gracefully."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        self._process = None

    def __del__(self):
        """Last-resort cleanup: kill the subprocess via OS signal.

        This avoids the 'Event loop is closed' RuntimeError that occurs when
        asyncio's subprocess transport tries to close on a dead loop during GC.
        We go directly through os.kill() instead of the asyncio Process API.
        """
        if self._process and self._process.returncode is None:
            pid = self._process.pid
            if pid:
                import os
                import signal
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
