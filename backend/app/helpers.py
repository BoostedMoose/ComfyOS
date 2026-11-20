"""Helper programs of the plugins (since 0.21, plugin API revision 3).

A helper program is a process of its own that a plugin runs next to ComfyOS – for protocols that do not exist in
usable form in Python (first example: `matter.js` under Node in the plugin `matter`). The core knows neither Node
nor Matter; it only provides the mechanism and its limits:

  * **Only when declared:** a plugin starts a helper program only if its manifest names `"helper": {"desc": "…"}`
    (the loader in `plugins.py` checks that); tile and approval dialog show the description.
  * **Only from its own area:** the program must – after resolving all symlinks – be in the plugin's folder or
    in its data folder.
  * **Without a shell, with an empty environment:** started with `asyncio.create_subprocess_exec`. The environment
    only holds `HOME` (= data folder of the plugin), `LANG` and what the plugin passes explicitly. No variable of
    ComfyOS reaches the helper program. The working directory is the data folder of the plugin.
  * **Exchange only as JSON lines** over stdin/stdout: one object per line, at most `MAX_LINE_BYTES` long and
    at most `MAX_LINES_PER_SECOND` lines per second. Whoever exceeds that is terminated.
  * **stderr is foreign text:** it is read and discarded – the log only says "Beiprogramm meldet Fehler" with a
    counter, never the content (like `plugins.error_text`).
  * **Supervised:** the process runs as a long runner of `tasks.py` and restarts after it ends, with a growing
    pause. `Helper.stop()`, disabling the plugin and the shutdown end it (close stdin,
    terminate after `STDIN_GRACE` seconds, kill after `STOP_GRACE` seconds).
  * **Contract with the helper program:** it exits as soon as its stdin closes. That way nothing is orphaned when
    ComfyOS crashes.

Every start of a process for a plugin goes through here – never an own `subprocess` (guard:
tests/security/test_helpers.py).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Sequence

from . import tasks

MAX_LINE_BYTES = 1024 * 1024      # one line in either direction
MAX_LINES_PER_SECOND = 1000       # from the helper program; above this it is terminated
MAX_ARGS = 32
MAX_ARG_LENGTH = 2048
MAX_ENV = 16
MAX_ENV_LENGTH = 2048
QUEUE_SIZE = 2000                 # unread messages; if the plugin does not read along, the helper program is terminated
STDIN_GRACE = 1.0                 # seconds a helper program has to end by itself after stdin was closed
STOP_GRACE = 5.0                  # seconds between terminate and kill
REPORT_ERRORS_EVERY = 100         # every n-th error line is reported (without its content)
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
FIXED_ENV = ("HOME", "LANG")      # set by the core itself; a plugin cannot overwrite them

_helpers: dict[str, "Helper"] = {}     # at most one helper program per plugin


class HelperError(Exception):
    """A helper program cannot be started or was terminated – the text is written for the user."""


def task_name(plugin_id: str) -> str:
    """Name of the long runner under which the helper program of a plugin runs at `tasks.py`."""
    return f"beiprogramm-{plugin_id}"


def checked_program(program: str | Path, roots: Sequence[Path]) -> Path:
    """Check the path of the program: a file that, after resolving all symlinks, is in one of the allowed folders
    (folder of the plugin, data folder of the plugin). Relative paths count from the first folder. Raises HelperError."""
    path = Path(str(program))
    if not path.is_absolute():
        path = Path(roots[0]) / path
    try:
        real = path.resolve()
        allowed = [Path(root).resolve() for root in roots]
    except (OSError, RuntimeError):       # z. B. Symlink im Kreis
        raise HelperError("Das Beiprogramm ist nicht lesbar.")
    if not any(real != root and real.is_relative_to(root) for root in allowed):
        raise HelperError("Das Beiprogramm liegt außerhalb des Plugin-Ordners.")
    if not real.is_file():
        raise HelperError("Das Beiprogramm fehlt.")
    return real


def checked_args(args: Sequence[Any]) -> list[str]:
    """Check the arguments: a limited number of short texts without NUL characters. Raises HelperError."""
    if isinstance(args, (str, bytes)) or len(args) > MAX_ARGS:
        raise HelperError("Zu viele oder ungültige Argumente für das Beiprogramm.")
    out = []
    for arg in args:
        if not isinstance(arg, str) or len(arg) > MAX_ARG_LENGTH or "\x00" in arg:
            raise HelperError("Ungültiges Argument für das Beiprogramm.")
        out.append(arg)
    return out


def build_env(data_dir: Path, extra: Mapping[str, Any] | None) -> dict[str, str]:
    """Environment of the helper program: empty except for `HOME` (data folder of the plugin), `LANG` and the variables
    the plugin passes (names `^[A-Z_][A-Z0-9_]*$`, limited length). Raises HelperError."""
    env = {"HOME": str(data_dir), "LANG": "C.UTF-8"}
    if sys.platform == "win32" and os.environ.get("SystemRoot"):
        env["SystemRoot"] = os.environ["SystemRoot"]     # without them no program opens a socket under Windows
    extra = extra or {}
    if len(extra) > MAX_ENV:
        raise HelperError("Zu viele Umgebungsvariablen für das Beiprogramm.")
    for name, value in extra.items():
        if not isinstance(name, str) or not ENV_NAME_RE.match(name) or name in FIXED_ENV:
            raise HelperError("Ungültige Umgebungsvariable für das Beiprogramm.")
        if not isinstance(value, str) or len(value) > MAX_ENV_LENGTH or "\x00" in value:
            raise HelperError("Ungültiger Wert einer Umgebungsvariable für das Beiprogramm.")
        env[name] = value
    return env


class Helper:
    """A running (or about to restart) helper program of a plugin – the object `context.start_helper`
    returns."""

    def __init__(self, plugin_id: str, program: Path, args: list[str], env: dict[str, str], cwd: Path):
        """Remembers what to start; it is started in `_run` under the supervision of `tasks.py`."""
        self.plugin_id = plugin_id
        self.errors = 0                    # lines on stderr and unreadable lines on stdout since the start
        self.starts = 0                    # how often the process was started
        self._program, self._args, self._env, self._cwd = program, args, env, cwd
        self._process: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(QUEUE_SIZE)
        self._stopped = False

    @property
    def running(self) -> bool:
        """Is the process running right now? Between end and restart that is briefly not the case."""
        return self._process is not None and self._process.returncode is None and not self._stopped

    @property
    def stopped(self) -> bool:
        """Was the helper program ended for good (`stop()`)?"""
        return self._stopped

    async def send(self, obj: Mapping[str, Any]) -> None:
        """Send an object as a JSON line to the helper program. Raises HelperError if it is not running right now or
        the line is too long."""
        process = self._process
        if not self.running or process is None or process.stdin is None:
            raise HelperError("Das Beiprogramm läuft gerade nicht.")
        line = json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        if len(line) > MAX_LINE_BYTES:
            raise HelperError("Nachricht an das Beiprogramm ist zu groß.")
        try:
            process.stdin.write(line)
            await process.stdin.drain()
        except (OSError, RuntimeError):     # the process ended between the check and the write
            raise HelperError("Das Beiprogramm läuft gerade nicht.")

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Messages of the helper program, one after the other (JSON objects only). Continues across restarts
        and only ends after `stop()`."""
        while True:
            message = await self._queue.get()
            if message is None:
                return
            yield message

    async def stop(self) -> None:
        """End the helper program for good (terminate, kill after STOP_GRACE seconds) and let `messages()` end."""
        if self._stopped:
            return
        self._stopped = True
        await tasks.cancel(task_name(self.plugin_id))
        if _helpers.get(self.plugin_id) is self:
            del _helpers[self.plugin_id]
        while True:     # make room for the end marker – whoever still reads now only wants to see the end
            try:
                self._queue.put_nowait(None)
                return
            except asyncio.QueueFull:
                self._queue.get_nowait()

    async def _run(self) -> None:
        """One life of the process: start, read stdout until it ends. Called again by `tasks.spawn` after every end;
        a cancellation (stopping, shutdown) ends the process."""
        self.starts += 1
        process = await asyncio.create_subprocess_exec(
            str(self._program), *self._args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=self._env, cwd=str(self._cwd), limit=MAX_LINE_BYTES)
        self._process = process
        try:
            await asyncio.gather(self._read_messages(process), self._count_errors(process))
        finally:
            await _end(process)

    async def _read_messages(self, process: asyncio.subprocess.Process) -> None:
        """Read stdout line by line and put every JSON object into the queue. A line that is too long or too many
        lines per second end the helper program (HelperError); unreadable input counts as an error."""
        window, count = time.monotonic(), 0
        while True:
            try:
                line = await process.stdout.readline()
            except ValueError:               # line longer than MAX_LINE_BYTES
                raise HelperError("Zeile zu lang")
            if not line:
                return                       # stdout geschlossen: der Prozess endet
            now = time.monotonic()
            if now - window >= 1.0:
                window, count = now, 0
            count += 1
            if count > MAX_LINES_PER_SECOND:
                raise HelperError("zu viele Zeilen")
            try:
                message = json.loads(line)
            except ValueError:
                message = None
            if not isinstance(message, dict):
                self._note_error()
                continue
            try:
                self._queue.put_nowait(message)
            except asyncio.QueueFull:        # the plugin does not read along: pile up nothing it never fetches
                raise HelperError("Schlange voll")

    async def _count_errors(self, process: asyncio.subprocess.Process) -> None:
        """Read stderr and discard it – foreign text never gets into the log. Counted is how often the helper program
        speaks up."""
        while True:
            try:
                chunk = await process.stderr.readline()
            except ValueError:               # overlong line: the rest of the line is discarded with the next read
                chunk = b"?"
            if not chunk:
                return
            self._note_error()

    def _note_error(self) -> None:
        """Count an error line and report it now and then – without its content."""
        self.errors += 1
        if self.errors == 1 or self.errors % REPORT_ERRORS_EVERY == 0:
            tasks.report(task_name(self.plugin_id), f"{self.errors} Zeilen", "Beiprogramm meldet Fehler")


async def _end(process: asyncio.subprocess.Process) -> None:
    """End the process if it is still running: close stdin (by contract a helper program then ends by itself and
    can still clean up), terminate after STDIN_GRACE seconds, kill after a further STOP_GRACE seconds."""
    if process.returncode is not None:
        return
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass   # pipe already closed
    for grace, signal in ((STDIN_GRACE, process.terminate), (STOP_GRACE, process.kill)):
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), grace)
            return
        except asyncio.TimeoutError:
            try:
                signal()
            except (OSError, ProcessLookupError):
                pass   # gerade von selbst geendet
    await process.wait()


def start(plugin_id: str, roots: Sequence[Path], data_dir: Path, program: str | Path, args: Sequence[Any] = (),
          env: Mapping[str, Any] | None = None) -> Helper:
    """Start the helper program of a plugin (a single one per plugin). `roots` are the folders the program may
    be in. Raises HelperError if path, arguments or environment do not fit or one is already running."""
    current = _helpers.get(plugin_id)
    if current is not None and not current.stopped and task_name(plugin_id) in tasks.running():
        raise HelperError("Für dieses Plugin läuft schon ein Beiprogramm.")
    helper = Helper(plugin_id, checked_program(program, roots), checked_args(args), build_env(data_dir, env), data_dir)
    _helpers[plugin_id] = helper
    tasks.spawn(task_name(plugin_id), helper._run)
    return helper


def get(plugin_id: str) -> Helper | None:
    """Das Beiprogramm eines Plugins, falls eines gestartet ist."""
    return _helpers.get(plugin_id)


async def stop(plugin_id: str) -> None:
    """End the helper program of a plugin (when the plugin stops or is disabled). Without a helper program nothing
    happens."""
    helper = _helpers.get(plugin_id)
    if helper is not None:
        await helper.stop()
