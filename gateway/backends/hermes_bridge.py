"""Hermes sidecar bridge — ``SearchBackend`` over a JSON-lines stdio worker.

Spawns ``<hermes_python> gateway/bridge/worker.py`` under the Hermes runtime
venv (resolved via ``HERMES_GATEWAY_HERMES_PYTHON`` or ``hermes doctor``),
talks the frozen request/response protocol of r8-interfaces.md section 4, and
maps results onto ``SearchItem`` / ``ExtractItem``. The worker is spawned lazily
on first use, restarted on death (max 3 respawns per 10 minutes), serialized by
a lock, and bounded by per-op timeouts (ping 5s / search 60s / extract 120s).
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from itertools import count
from pathlib import Path

from gateway.protocols import ExtractItem, SearchItem

_WORKER_PATH = Path(__file__).resolve().parents[1] / "bridge" / "worker.py"
_DOCTOR_VENV_RE = re.compile(r"Runtime venv staged \(([^)]+)\)", re.IGNORECASE)
_DOCTOR_TIMEOUT_S = 90.0

_RESOLVED_PYTHON: str | None = None
_RESOLVE_LOCK = threading.Lock()


class HermesBridgeError(RuntimeError):
    """The sidecar is unreachable, dead, or returned a protocol error."""


def _python_in_venv(venv: Path) -> Path | None:
    """Runtime python inside a venv dir (Windows ``Scripts`` or POSIX ``bin``)."""
    for rel in (("Scripts", "python.exe"), ("bin", "python"), ("bin", "python3")):
        candidate = venv.joinpath(*rel)
        if candidate.is_file():
            return candidate
    return None


def resolve_hermes_python(override: str | None = None) -> str:
    """Resolve the Hermes runtime venv python; cached process-wide.

    Order: explicit *override* -> ``HERMES_GATEWAY_HERMES_PYTHON`` -> parse
    ``hermes doctor`` for ``Runtime venv staged (<path>)``. Raises
    :class:`HermesBridgeError` with an actionable message when all fail.
    """
    global _RESOLVED_PYTHON
    with _RESOLVE_LOCK:
        if _RESOLVED_PYTHON:
            return _RESOLVED_PYTHON
        for candidate in (override, os.environ.get("HERMES_GATEWAY_HERMES_PYTHON", "").strip() or None):
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            if path.is_dir():
                found = _python_in_venv(path)
                if found:
                    _RESOLVED_PYTHON = str(found)
                    return _RESOLVED_PYTHON
                raise HermesBridgeError(f"{candidate!r} is a directory but contains no venv python")
            if path.is_file():
                _RESOLVED_PYTHON = str(path)
                return _RESOLVED_PYTHON
            raise HermesBridgeError(f"HERMES_GATEWAY_HERMES_PYTHON points at a missing file: {candidate!r}")
        try:
            proc = subprocess.run(
                ["hermes", "doctor"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_DOCTOR_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HermesBridgeError(
                "cannot resolve the Hermes runtime python: `hermes doctor` could not run "
                f"({exc}). Set HERMES_GATEWAY_HERMES_PYTHON to the runtime venv python."
            ) from exc
        match = _DOCTOR_VENV_RE.search(proc.stdout or "")
        if match:
            python = _python_in_venv(Path(match.group(1).strip()))
            if python:
                _RESOLVED_PYTHON = str(python)
                return _RESOLVED_PYTHON
        raise HermesBridgeError(
            "cannot resolve the Hermes runtime python: `hermes doctor` reported no "
            "'Runtime venv staged' path. Set HERMES_GATEWAY_HERMES_PYTHON to the "
            "runtime venv python (e.g. .../environments/<id>/venv/Scripts/python.exe)."
        )


class HermesBridge:
    """``SearchBackend`` client for the Hermes sidecar worker (section 4)."""

    name = "hermes"
    DEFAULT_TIMEOUTS = {"ping": 5.0, "search": 60.0, "extract": 120.0}
    RESPAWN_LIMIT = 3
    RESPAWN_WINDOW_S = 600.0

    def __init__(
        self,
        *,
        hermes_python: str | None = None,
        hermes_home: str | None = None,
        repo_root: str | os.PathLike | None = None,
        worker_path: str | os.PathLike | None = None,
        timeouts: dict[str, float] | None = None,
    ):
        self._python_override = hermes_python
        self._hermes_home = hermes_home or os.environ.get("HERMES_HOME", "").strip() or None
        self._repo_root = Path(repo_root) if repo_root else _WORKER_PATH.parents[2]
        self._worker_path = Path(worker_path) if worker_path else _WORKER_PATH
        self._timeouts = dict(self.DEFAULT_TIMEOUTS, **(timeouts or {}))
        self._lock = threading.Lock()
        self._ids = count(1)
        self._proc: subprocess.Popen | None = None
        self._responses: queue.Queue = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=30)
        self._spawn_times: deque[float] = deque()

    # ---------- SearchBackend ----------

    def search(self, query: str, *, max_results: int = 10) -> list[SearchItem]:
        result = self._call("search", {"query": query, "max_results": int(max_results)})
        items: list[SearchItem] = []
        for i, r in enumerate(result if isinstance(result, list) else []):
            if not isinstance(r, dict) or not r.get("url"):
                continue
            items.append(
                SearchItem(
                    title=str(r.get("title") or ""),
                    url=str(r["url"]),
                    description=str(r.get("description") or ""),
                    position=int(r.get("position") or i + 1),
                )
            )
        return items

    def extract(self, urls: list[str], *, char_limit: int = 15000) -> list[ExtractItem]:
        result = self._call("extract", {"urls": list(urls), "char_limit": int(char_limit)})
        items: list[ExtractItem] = []
        for r in result if isinstance(result, list) else []:
            if not isinstance(r, dict):
                continue
            items.append(
                ExtractItem(
                    url=str(r.get("url") or ""),
                    title=str(r.get("title") or ""),
                    content=str(r.get("content") or ""),
                    error=r.get("error"),
                )
            )
        return items

    def ping(self) -> dict:
        try:
            result = self._call("ping", {})
        except HermesBridgeError as exc:
            return {"ok": False, "detail": str(exc)}
        if isinstance(result, dict) and result.get("pong"):
            return {"ok": True, "detail": f"hermes worker alive ({result.get('python', 'unknown python')})"}
        return {"ok": False, "detail": f"unexpected ping result: {result!r}"}

    def close(self) -> None:
        with self._lock:
            self._terminate()

    def __enter__(self) -> HermesBridge:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- request/response ----------

    def _call(self, op: str, params: dict) -> object:
        """One serialized request/response round-trip with the worker."""
        timeout = self._timeouts.get(op, 60.0)
        with self._lock:
            proc = self._ensure_proc()
            req_id = next(self._ids)
            line = json.dumps({"id": req_id, "op": op, "params": params}, ensure_ascii=False).encode("utf-8") + b"\n"
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._terminate()
                raise HermesBridgeError(f"hermes worker stdin write failed ({exc}); worker was killed") from exc
            try:
                resp = self._responses.get(timeout=timeout)
            except queue.Empty:
                self._terminate()
                raise HermesBridgeError(f"hermes worker '{op}' timed out after {timeout:.0f}s; worker killed") from None
            if resp is None:
                self._terminate()
                raise HermesBridgeError("hermes worker exited mid-request" + self._stderr_hint())
            if not isinstance(resp, dict) or resp.get("id") != req_id:
                raise HermesBridgeError(f"hermes worker protocol error: {resp!r}")
            if not resp.get("ok"):
                raise HermesBridgeError(str(resp.get("error") or "worker reported failure"))
            return resp.get("result")

    def _ensure_proc(self) -> subprocess.Popen:
        """Return a live worker process, (re)spawning under the restart cap."""
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        now = time.monotonic()
        while self._spawn_times and now - self._spawn_times[0] > self.RESPAWN_WINDOW_S:
            self._spawn_times.popleft()
        if len(self._spawn_times) >= self.RESPAWN_LIMIT:
            raise HermesBridgeError(
                f"hermes worker restart limit exceeded ({self.RESPAWN_LIMIT} per "
                f"{self.RESPAWN_WINDOW_S / 60:.0f} min); refusing to respawn" + self._stderr_hint()
            )
        python = resolve_hermes_python(self._python_override)
        env = os.environ.copy()
        if self._hermes_home:
            env["HERMES_HOME"] = self._hermes_home
            hermes_agent = str(Path(self._hermes_home) / "hermes-agent")
        else:
            hermes_agent = ""
        pythonpath = [str(self._repo_root)]
        if hermes_agent:
            pythonpath.append(hermes_agent)
        if env.get("PYTHONPATH"):
            pythonpath.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            self._proc = subprocess.Popen(
                [python, str(self._worker_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._repo_root),
                env=env,
            )
        except OSError as exc:
            raise HermesBridgeError(f"failed to spawn hermes worker with {python!r}: {exc}") from exc
        self._spawn_times.append(now)
        self._responses = queue.Queue()
        threading.Thread(target=self._stdout_reader, args=(self._proc,), daemon=True).start()
        threading.Thread(target=self._stderr_reader, args=(self._proc,), daemon=True).start()
        return self._proc

    def _stdout_reader(self, proc: subprocess.Popen) -> None:
        """Push decoded response lines onto the queue; ``None`` at EOF (death)."""
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.strip()
                if not line:
                    continue
                try:
                    self._responses.put(json.loads(line.decode("utf-8", errors="replace")))
                except json.JSONDecodeError:
                    self._responses.put({"id": None, "ok": False, "error": f"malformed worker output: {line[:200]!r}"})
        finally:
            self._responses.put(None)

    def _stderr_reader(self, proc: subprocess.Popen) -> None:
        """Drain worker stderr into a small tail buffer for error reporting."""
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._stderr_tail.append(line)
        except (OSError, ValueError):
            pass

    def _stderr_hint(self) -> str:
        tail = list(self._stderr_tail)[-3:]
        return "; worker stderr: " + " | ".join(tail) if tail else ""

    def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
