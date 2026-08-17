from __future__ import annotations

from dataclasses import dataclass
import os
import re
import sys
import time
from typing import Optional, Union

# Button-event tracking (1002) reports drag motion while a mouse button is
# held. SGR coordinates (1006) keep the existing unambiguous event encoding.
MOUSE_ENABLE = "\033[?1000h\033[?1002h\033[?1006h"
MOUSE_DISABLE = "\033[?1002l\033[?1000l\033[?1006l"


@dataclass(frozen=True)
class MouseEvent:
    x: int
    y: int
    button: str
    pressed: bool = True
    motion: bool = False


InputEvent = Union[str, MouseEvent]
_SGR_MOUSE = re.compile(r"^\x1b\[<(\d+);(\d+);(\d+)([Mm])$")


def normalize_plain_key(ch: str) -> str:
    """Normalize single-byte terminal keys consistently across platforms."""
    return {
        "\t": "TAB",
        "\x1b": "ESC",
        "\x06": "CTRL_F",
        "\x0f": "CTRL_O",
        "\x10": "CTRL_P",
        "\x14": "CTRL_T",
        "\x17": "CTRL_W",
    }.get(ch, ch)


def parse_escape_sequence(seq: str) -> Optional[InputEvent]:
    mapping = {
        "\x1b[A": "UP", "\x1b[B": "DOWN", "\x1b[C": "RIGHT", "\x1b[D": "LEFT", "\x1b[5~": "PAGEUP",
        "\x1b[6~": "PAGEDOWN", "\x1b[H": "HOME", "\x1b[F": "END",
        "\x1bOH": "HOME", "\x1bOF": "END", "\x1b[Z": "SHIFT_TAB",
        "\x1b[1;2A": "SHIFT_UP", "\x1b[1;2B": "SHIFT_DOWN",
        "\x1b[1;5A": "CTRL_UP", "\x1b[1;5B": "CTRL_DOWN",
        "\x1b[1;5C": "CTRL_RIGHT", "\x1b[1;5D": "CTRL_LEFT",
        "\x1b[5;5~": "CTRL_PAGEUP", "\x1b[6;5~": "CTRL_PAGEDOWN",
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
    motion = bool(code & 32)
    if code & 64:
        button = "wheel_down" if base == 1 else "wheel_up"
    elif motion and base == 3:
        # A number of terminal emulators report held-button movement with the
        # legacy "no button" low bits even while button-event tracking (1002)
        # is active. The application tracks whether the left button is down,
        # so preserve this as generic motion rather than discarding the drag.
        button = "motion"
    elif not pressed and base == 3:
        button = "release"
    elif base == 0:
        button = "left"
    else:
        button = "other"
    return MouseEvent(x=x, y=y, button=button, pressed=pressed, motion=motion)


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
            self.enabled = bool(sys.stdout.isatty())
            return self

        try:
            import termios
            import tty

            if sys.stdin.isatty():
                self._fd = sys.stdin.fileno()
            else:
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
        self._fd = None
        self._owned_fd = None
        self.enabled = False

    def _read_byte(self) -> Optional[bytes]:
        if self._fd is None:
            return None
        try:
            return os.read(self._fd, 1)
        except BlockingIOError:
            return None
        except OSError:
            return None

    def _input_ready(self, timeout: float) -> bool:
        if self._fd is None:
            return False
        try:
            import select
            ready, _, _ = select.select([self._fd], [], [], max(0.0, timeout))
            return bool(ready)
        except Exception:
            return False

    def _read_escape_sequence(self, first: bytes) -> str:
        seq = first.decode("utf-8", errors="ignore")
        # ESC is both a complete key and an ANSI-sequence prefix. Never issue
        # another blocking read unless select confirms continuation data.
        deadline = time.monotonic() + 0.010
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._input_ready(remaining):
                break
            byte = self._read_byte()
            if not byte:
                break
            seq += byte.decode("utf-8", errors="ignore")
            if seq.endswith(("~", "A", "B", "C", "D", "H", "F", "M", "m", "Z")):
                break
        return seq

    def _poll_posix(self) -> Optional[InputEvent]:
        if self._fd is None:
            return None
        try:
            import select
            ready, _, _ = select.select([self._fd], [], [], 0)
        except Exception:
            return None
        if not ready:
            return None
        byte = self._read_byte()
        if not byte:
            return None
        ch = byte.decode("utf-8", errors="ignore")
        if ch != "\x1b":
            return normalize_plain_key(ch)
        seq = self._read_escape_sequence(byte)
        return parse_escape_sequence(seq) or seq

    def _poll_windows(self) -> Optional[InputEvent]:
        try:
            import msvcrt
            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                special = msvcrt.getwch()
                mapping = {
                    "H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                    "I": "PAGEUP", "Q": "PAGEDOWN", "G": "HOME", "O": "END",
                }
                key = mapping.get(special)
                if key is None:
                    return None
                try:
                    import ctypes
                    VK_CONTROL = 0x11
                    if ctypes.windll.user32.GetKeyState(VK_CONTROL) & 0x8000:
                        controlled = {
                            "UP": "CTRL_UP", "DOWN": "CTRL_DOWN",
                            "LEFT": "CTRL_LEFT", "RIGHT": "CTRL_RIGHT",
                            "PAGEUP": "CTRL_PAGEUP", "PAGEDOWN": "CTRL_PAGEDOWN",
                        }
                        return controlled.get(key, key)
                except Exception:
                    pass
                return key
            return normalize_plain_key(ch)
        except Exception:
            return None

    def poll(self) -> Optional[InputEvent]:
        if not self.enabled:
            return None
        return self._poll_windows() if os.name == "nt" else self._poll_posix()
