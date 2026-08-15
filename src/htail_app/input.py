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
    x: int  # zero-based terminal column
    y: int  # zero-based terminal row
    button: str  # left, wheel_up, wheel_down, other
    pressed: bool = True


InputEvent = Union[str, MouseEvent]


_SGR_MOUSE = re.compile(r"^\x1b\[<(\d+);(\d+);(\d+)([Mm])$")


def parse_escape_sequence(seq: str) -> Optional[InputEvent]:
    mapping = {
        "\x1b[A": "UP",
        "\x1b[B": "DOWN",
        "\x1b[5~": "PAGEUP",
        "\x1b[6~": "PAGEDOWN",
        "\x1b[H": "HOME",
        "\x1b[F": "END",
        "\x1bOH": "HOME",
        "\x1bOF": "END",
        "\x1b[Z": "SHIFT_TAB",
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
    """Non-blocking keyboard and SGR mouse input reader."""

    def __init__(self, mouse: bool = True) -> None:
        self.mouse = mouse
        self.enabled = False
        self._fd: Optional[int] = None
        self._old_termios = None

    def __enter__(self) -> "InputReader":
        if not sys.stdin.isatty():
            return self
        self.enabled = True
        if os.name != "nt":
            try:
                import termios
                import tty

                self._fd = sys.stdin.fileno()
                self._old_termios = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
            except Exception:
                self.enabled = False
        if self.enabled and self.mouse and os.name != "nt":
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

    def poll(self) -> Optional[InputEvent]:
        if not self.enabled:
            return None
        if os.name == "nt":
            return self._poll_windows()
        return self._poll_posix()

    def _poll_windows(self) -> Optional[InputEvent]:
        try:
            import msvcrt

            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0") and msvcrt.kbhit():
                special = msvcrt.getwch()
                return {
                    "H": "UP",
                    "P": "DOWN",
                    "I": "PAGEUP",
                    "Q": "PAGEDOWN",
                    "G": "HOME",
                    "O": "END",
                }.get(special)
            return ch
        except Exception:
            return None

    def _poll_posix(self) -> Optional[InputEvent]:
        try:
            import select

            fd = self._fd if self._fd is not None else sys.stdin.fileno()
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                return None

            def read_char() -> str:
                data = os.read(fd, 1)
                return data.decode("latin1") if data else ""

            ch = read_char()
            if ch != "\x1b":
                if ch == "\t":
                    return "TAB"
                return ch

            seq = ch
            deadline = time.monotonic() + 0.03
            while time.monotonic() < deadline and len(seq) < 48:
                more, _, _ = select.select([fd], [], [], 0.002)
                if not more:
                    break
                seq += read_char()
                if seq.startswith("\x1b[<") and seq[-1:] in ("M", "m"):
                    break
                if not seq.startswith("\x1b[<") and (seq.endswith("~") or seq in ("\x1b[A", "\x1b[B", "\x1b[H", "\x1b[F", "\x1bOH", "\x1bOF", "\x1b[Z")):
                    break
            return parse_escape_sequence(seq)
        except Exception:
            return None

