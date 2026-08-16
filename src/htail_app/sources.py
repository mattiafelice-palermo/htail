from __future__ import annotations

import queue
from pathlib import Path
import subprocess
import threading
import time
from typing import List, Optional, TextIO

from .extras import parse_ssh_source, read_compressed_lines
from .watcher import WatchNotice, WatchUpdate


class StreamFollower:
    """Turn a line-oriented text stream into htail update batches."""

    def __init__(self, stream: TextIO, args, label: str = "stdin") -> None:
        self.stream = stream
        self.args = args
        self.label = label
        self.previous: List[str] = []
        self.update_number = 0
        self.last_update_time: Optional[float] = None
        self.initialized = False
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._done = threading.Event()
        self._error: Optional[str] = None
        self._end_reported = False
        self._thread: Optional[threading.Thread] = None

    @property
    def finished(self) -> bool:
        return self._done.is_set() and self._queue.empty() and self._end_reported

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        return ""

    def initialize_if_available(self) -> WatchNotice:
        if not self.initialized:
            self.initialized = True
            self._thread = threading.Thread(target=self._reader, name=f"htail-{self.label}", daemon=True)
            self._thread.start()
        return WatchNotice("initial", initial_tail=[])

    def _reader(self) -> None:
        try:
            while True:
                line = self.stream.readline()
                if line == "":
                    break
                self._queue.put(line)
        except Exception as exc:
            self._error = str(exc)
        finally:
            self._done.set()

    def _end_text(self) -> str:
        return f"{self.label} reached EOF"

    def poll(self, now: Optional[float] = None):
        now = time.monotonic() if now is None else now
        if not self.initialized:
            return self.initialize_if_available()
        fresh: List[str] = []
        while True:
            try:
                fresh.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if fresh:
            start = len(self.previous)
            self.previous.extend(fresh)
            elapsed = None if self.last_update_time is None else now - self.last_update_time
            self.last_update_time = now
            self.update_number += 1
            return WatchUpdate(
                update_number=self.update_number,
                events=(("add", list(fresh)),),
                added=len(fresh), replaced=0, deleted=0, elapsed=elapsed,
                now_monotonic=now, current_snapshot=self.previous,
                changed_new_indices=tuple(range(start, len(self.previous))),
            )
        if self._done.is_set() and not self._end_reported:
            self._end_reported = True
            if self._error:
                return WatchNotice("error", f"{self.label}: {self._error}")
            return WatchNotice("ended", self._end_text())
        return None

    def close(self) -> None:
        return


class CommandFollower(StreamFollower):
    """Run a shell command and expose merged stdout/stderr as a pane source."""

    def __init__(self, command: str, args, label: Optional[str] = None) -> None:
        self.command = command
        self.started_monotonic = time.monotonic()
        self.process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding=args.encoding, errors="replace", bufsize=1,
        )
        assert self.process.stdout is not None
        super().__init__(self.process.stdout, args, label=label or command)

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        now = time.monotonic() if now is None else now
        runtime = max(0.0, now - self.started_monotonic)
        code = self.process.poll()
        if code is None:
            return f"PID {self.process.pid} · {runtime:.0f}s"
        return f"EXIT {code} · {runtime:.0f}s"

    def _end_text(self) -> str:
        code = self.process.poll()
        return f"command ended: {self.command}" if code is None else f"command exited with status {code}"

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.terminate(); self.process.wait(timeout=0.5)
            except Exception:
                try:
                    self.process.kill(); self.process.wait(timeout=0.5)
                except Exception:
                    pass
        if self.process.stdout is not None:
            try:
                self.process.stdout.close()
            except Exception:
                pass


class SSHFollower(StreamFollower):
    """Follow a remote file through the user's existing OpenSSH configuration."""

    def __init__(self, source: str, args) -> None:
        self.source = source
        argv, label = parse_ssh_source(source)
        self.argv = argv
        self.started_monotonic = time.monotonic()
        try:
            self.process = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding=args.encoding, errors="replace", bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("OpenSSH 'ssh' executable was not found on PATH") from exc
        assert self.process.stdout is not None
        super().__init__(self.process.stdout, args, label=label)

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        now = time.monotonic() if now is None else now
        runtime = max(0.0, now - self.started_monotonic)
        code = self.process.poll()
        if code is None:
            return f"SSH PID {self.process.pid} · {runtime:.0f}s"
        return f"SSH EXIT {code} · {runtime:.0f}s"

    def _end_text(self) -> str:
        code = self.process.poll()
        return "SSH source disconnected" if code is None else f"SSH source exited with status {code}"

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.terminate(); self.process.wait(timeout=0.5)
            except Exception:
                try:
                    self.process.kill(); self.process.wait(timeout=0.5)
                except Exception:
                    pass
        if self.process.stdout is not None:
            try:
                self.process.stdout.close()
            except Exception:
                pass


class CompressedFollower:
    """Read a compressed file once. Compressed sources are intentionally static."""

    def __init__(self, path: Path, args) -> None:
        self.path = path
        self.args = args
        self.previous: List[str] = []
        self.initialized = False
        self._ended = False

    @property
    def finished(self) -> bool:
        return self._ended

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        return "STATIC"

    def initialize_if_available(self) -> WatchNotice:
        try:
            self.previous = read_compressed_lines(self.path, self.args.encoding)
        except Exception as exc:
            self.initialized = True
            self._ended = True
            return WatchNotice("error", f"{self.path}: {exc}")
        self.initialized = True
        if self.args.lines is None:
            initial = list(self.previous)
        elif self.args.lines == 0:
            initial = []
        else:
            initial = list(self.previous[-self.args.lines:])
        return WatchNotice("initial", initial_tail=initial)

    def poll(self, now: Optional[float] = None):
        if not self.initialized:
            return self.initialize_if_available()
        if not self._ended:
            self._ended = True
            return WatchNotice("ended", "compressed static source loaded")
        return None

    def close(self) -> None:
        return
