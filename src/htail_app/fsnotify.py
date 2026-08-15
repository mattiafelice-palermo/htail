from __future__ import annotations

from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import struct
import sys
from typing import Dict, Optional, Set


def _norm(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


@dataclass(frozen=True)
class FsEvents:
    paths: Set[Path]
    directories: Set[Path]


class NativeWatchHub:
    """Best-effort native filesystem wakeups with a polling fallback.

    Notifications are intentionally only a *hint*: FileFollower still owns all
    verification/debounce/change semantics. If a platform/backend is unavailable,
    ``available`` is False and callers can keep the previous polling behaviour.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.backend = "poll"
        self._fd: Optional[int] = None
        self._libc = None
        self._wd_to_dir: Dict[int, Path] = {}
        self._dir_to_wd: Dict[Path, int] = {}
        self._win_handles: Dict[Path, int] = {}
        if not enabled:
            return
        if sys.platform.startswith("linux"):
            self._init_linux()
        elif os.name == "nt":
            self._init_windows()

    @property
    def available(self) -> bool:
        return self.backend != "poll"

    def _init_linux(self) -> None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            init1 = libc.inotify_init1
            init1.argtypes = [ctypes.c_int]
            init1.restype = ctypes.c_int
            flags = os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            fd = init1(flags)
            if fd < 0:
                return
            libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            libc.inotify_add_watch.restype = ctypes.c_int
            libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
            self._libc = libc
            self._fd = fd
            self.backend = "inotify"
        except Exception:
            self.close()

    def _init_windows(self) -> None:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.FindFirstChangeNotificationW.argtypes = [ctypes.c_wchar_p, ctypes.c_bool, ctypes.c_uint32]
            kernel32.FindFirstChangeNotificationW.restype = ctypes.c_void_p
            kernel32.FindNextChangeNotification.argtypes = [ctypes.c_void_p]
            kernel32.FindNextChangeNotification.restype = ctypes.c_bool
            kernel32.FindCloseChangeNotification.argtypes = [ctypes.c_void_p]
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            self._kernel32 = kernel32
            self.backend = "ReadDirectoryChangesW"
        except Exception:
            self.backend = "poll"

    def add_file(self, path: Path) -> None:
        self.add_directory(path.parent)

    def add_directory(self, path: Path) -> None:
        directory = _norm(path)
        if not directory.is_dir() or not self.available:
            return
        if self.backend == "inotify":
            if directory in self._dir_to_wd or self._fd is None or self._libc is None:
                return
            mask = (
                0x00000002  # IN_MODIFY
                | 0x00000004  # IN_ATTRIB
                | 0x00000008  # IN_CLOSE_WRITE
                | 0x00000040  # IN_MOVED_FROM
                | 0x00000080  # IN_MOVED_TO
                | 0x00000100  # IN_CREATE
                | 0x00000200  # IN_DELETE
                | 0x00000400  # IN_DELETE_SELF
                | 0x00000800  # IN_MOVE_SELF
            )
            try:
                wd = self._libc.inotify_add_watch(self._fd, os.fsencode(directory), mask)
                if wd >= 0:
                    self._dir_to_wd[directory] = wd
                    self._wd_to_dir[wd] = directory
            except Exception:
                pass
            return

        if self.backend == "ReadDirectoryChangesW":
            if directory in self._win_handles:
                return
            mask = 0x00000001 | 0x00000002 | 0x00000008 | 0x00000010 | 0x00000040
            try:
                handle = self._kernel32.FindFirstChangeNotificationW(str(directory), False, mask)
                invalid = ctypes.c_void_p(-1).value
                if handle and handle != invalid:
                    self._win_handles[directory] = int(handle)
            except Exception:
                pass

    def poll(self) -> FsEvents:
        paths: Set[Path] = set()
        directories: Set[Path] = set()
        if self.backend == "inotify" and self._fd is not None:
            event_struct = struct.Struct("iIII")
            while True:
                try:
                    payload = os.read(self._fd, 65536)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if not payload:
                    break
                offset = 0
                while offset + event_struct.size <= len(payload):
                    wd, mask, _cookie, name_len = event_struct.unpack_from(payload, offset)
                    offset += event_struct.size
                    raw_name = payload[offset : offset + name_len]
                    offset += name_len
                    directory = self._wd_to_dir.get(wd)
                    if directory is None:
                        continue
                    directories.add(directory)
                    name = raw_name.split(b"\x00", 1)[0]
                    paths.add(directory / os.fsdecode(name) if name else directory)
                    if mask & 0x00008000:  # IN_IGNORED
                        self._wd_to_dir.pop(wd, None)
                        self._dir_to_wd.pop(directory, None)
            return FsEvents(paths, directories)

        if self.backend == "ReadDirectoryChangesW":
            WAIT_OBJECT_0 = 0
            for directory, handle in list(self._win_handles.items()):
                try:
                    state = self._kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0)
                    if state == WAIT_OBJECT_0:
                        directories.add(directory)
                        self._kernel32.FindNextChangeNotification(ctypes.c_void_p(handle))
                except Exception:
                    pass
        return FsEvents(paths, directories)

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        kernel32 = getattr(self, "_kernel32", None)
        if kernel32 is not None:
            for handle in list(self._win_handles.values()):
                try:
                    kernel32.FindCloseChangeNotification(ctypes.c_void_p(handle))
                except Exception:
                    pass
        self._win_handles.clear()
        self._wd_to_dir.clear()
        self._dir_to_wd.clear()
