from __future__ import annotations

from dataclasses import dataclass
import os
import re
import sys
import time
from typing import Optional, Union

MOUSE_ENABLE = "\033[?1000h\033[?1006h"
MOUSE_DISABLE = "\033[?1000l\033[?1006l"


@dataclass(frozen=True)
class MouseEvent:
    x: int
    y: int
    button: str
    pressed: bool = True


InputEvent = Union[str, MouseEvent]
_SGR_MOUSE = re.compile(r"^\x1b\[<(\d+);(\d+);(\d+)([Mm])$")


def normalize_plain_key(ch: str) -> str:
    """Normalize single-byte terminal keys consistently across platforms."""
    return {"\t": "TAB", "\x1b": "ESC", "\x14": "CTRL_T"}.get(ch, ch)


def parse_escape_sequence(seq: str) -> Optional[InputEvent]:
    mapping = {
        "\x1b[A": "UP", "\x1b[B": "DOWN", "\x1b[C": "RIGHT", "\x1b[D": "LEFT", "\x1b[5~": "PAGEUP",
        "\x1b[6~": "PAGEDOWN", "\x1b[H": "HOME", "\x1b[F": "END",
        "\x1bOH": "HOME", "\x1bOF": "END", "\x1b[Z": "SHIFT_TAB",
        "\x1b": "ESC",
    }
    if seq in mapping:
        return mapping[seq]
    match = _SGR_MOUSE.match(seq)
    if not match:
        return None
    code = int(match.group(1))
    x = max(0, int(match.group(2)) - 1)
    y = max(0, int(match.group(3)) - 1)
    pressed = match.group(4) == "M"
    base = code & 0b11
    if code & 64:
        button = "wheel_down" if base == 1 else "wheel_up"
    elif base == 0:
        button = "left"
    else:
        button = "other"
    return MouseEvent(x=x, y=y, button=button, pressed=pressed)


class InputReader:
    """Non-blocking keyboard/SGR mouse reader, including piped-stdin sessions."""

    def __init__(self, mouse: bool = True) -> None:
        self.mouse = mouse
        self.enabled = False
        self._fd: Optional[int] = None
        self._owned_fd: Optional[int] = None
        self._old_termios = None

    def __enter__(self) -> "InputReader":
        if os.name == "nt":
            # msvcrt reads the console keyboard even when standard input is
            # redirected in the usual Windows Terminal/Console setup.
            self.enabled = bool(sys.stdout.isatty())
            return self

        try:
            import termios
            import tty

            if sys.stdin.isatty():
                self._fd = sys.stdin.fileno()
            else:
                # A pipeline owns fd 0. Use the controlling terminal for UI
                # keys so `producer | ht` remains fully interactive.
                self._owned_fd = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_NOCTTY", 0))
                self._fd = self._owned_fd
            self._old_termios = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self.enabled = True
        except Exception:
            self.enabled = False

        if self.enabled and self.mouse:
            sys.stdout.write(MOUSE_ENABLE)
            sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.enabled and self.mouse and os.name != "nt":
            sys.stdout.write(MOUSE_DISABLE)
            sys.stdout.flush()
        if os.name != "nt" and self._old_termios is not None and self._fd is not None:
            try:
                import termios
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_termios)
            except Exception:
                pass
        if self._owned_fd is not None:
            try:
                os.close(self._owned_fd)
            except OSError:
                pass
            self._owned_fd = None

    def poll(self) -> Optional[InputEvent]:
        if not self.enabled:
            return None
        return self._poll_windows() if os.name == "nt" else self._poll_posix()

    def _poll_windows(self) -> Optional[InputEvent]:
        try:
            import msvcrt
            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0") and msvcrt.kbhit():
                special = msvcrt.getwch()
                return {"H": "UP", "P": "DOWN", "M": "RIGHT", "K": "LEFT", "I": "PAGEUP", "Q": "PAGEDOWN", "G": "HOME", "O": "END"}.get(special)
            return normalize_plain_key(ch)
        except Exception:
            return None

    def _poll_posix(self) -> Optional[InputEvent]:
        try:
            import select
            if self._fd is None:
                return None
            fd = self._fd
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                return None

            def read_char() -> str:
                data = os.read(fd, 1)
                return data.decode("latin1") if data else ""

            ch = read_char()
            if ch != "\x1b":
                return normalize_plain_key(ch)

            seq = ch
            deadline = time.monotonic() + 0.03
            while time.monotonic() < deadline and len(seq) < 48:
                more, _, _ = select.select([fd], [], [], 0.002)
                if not more:
                    break
                seq += read_char()
                if seq.startswith("\x1b[<") and seq[-1:] in ("M", "m"):
                    break
                if not seq.startswith("\x1b[<") and (seq.endswith("~") or seq in ("\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D", "\x1b[H", "\x1b[F", "\x1bOH", "\x1bOF", "\x1b[Z")):
                    break
            return parse_escape_sequence(seq)
        except Exception:
            return None
