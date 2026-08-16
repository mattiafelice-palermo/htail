from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, List, Optional, Sequence, Tuple

from .watcher import WatchNotice, WatchUpdate, analyze_changes


REMOTE_CHECK_INTERVAL = 3.0


class GitRemoteError(RuntimeError):
    pass


class GitRemoteMissingFile(GitRemoteError):
    pass


@dataclass(frozen=True)
class GitFileContext:
    root: Path
    relative_path: str
    remotes: Tuple[str, ...]


@dataclass(frozen=True)
class GitRemoteRef:
    remote: str
    branch: str

    @property
    def label(self) -> str:
        return f"{self.remote}/{self.branch}"


def _git(
    root: Path,
    *args: str,
    timeout: float = 8.0,
    text: bool = True,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitRemoteError("git executable was not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitRemoteError("git command timed out") from exc


def _failure(result: subprocess.CompletedProcess, fallback: str) -> str:
    stderr = result.stderr
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return (stderr or "").strip() or fallback


def discover_git_file(path: Path) -> Optional[GitFileContext]:
    """Resolve a local file to its repository identity and configured remotes."""
    path = path.expanduser().resolve()
    result = _git(path.parent, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    root = Path(result.stdout.strip()).resolve()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return None
    remotes_result = _git(root, "remote")
    if remotes_result.returncode != 0:
        return None
    remotes = tuple(line.strip() for line in remotes_result.stdout.splitlines() if line.strip())
    return GitFileContext(root=root, relative_path=relative, remotes=remotes)


def _cached_remote_branches(context: GitFileContext, remote: str) -> List[str]:
    result = _git(
        context.root,
        "for-each-ref",
        "--format=%(refname:short)",
        f"refs/remotes/{remote}",
    )
    if result.returncode != 0:
        return []
    prefix = remote + "/"
    branches = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        branch = line[len(prefix):]
        if branch and branch != "HEAD":
            branches.append(branch)
    return sorted(set(branches))


def list_remote_refs(context: GitFileContext) -> Tuple[List[GitRemoteRef], Optional[str]]:
    """List live remote branches, falling back to cached tracking refs on errors."""
    refs: List[GitRemoteRef] = []
    warnings: List[str] = []
    for remote in context.remotes:
        result = _git(context.root, "ls-remote", "--heads", remote)
        branches: List[str] = []
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split(None, 1)
                if len(parts) != 2 or not parts[1].startswith("refs/heads/"):
                    continue
                branches.append(parts[1][len("refs/heads/"):])
        else:
            branches = _cached_remote_branches(context, remote)
            warnings.append(f"{remote}: {_failure(result, 'could not query remote branches')}")
        refs.extend(GitRemoteRef(remote, branch) for branch in sorted(set(branches)))
    return refs, "; ".join(warnings) if warnings else None


def remote_head_sha(
    context: GitFileContext,
    remote: str,
    branch: str,
    *,
    progress: Optional[Callable[[str], None]] = None,
) -> str:
    if progress is not None:
        progress(f"Checking {remote}/{branch}…")
    result = _git(context.root, "ls-remote", remote, f"refs/heads/{branch}")
    if result.returncode != 0:
        raise GitRemoteError(_failure(result, f"could not query {remote}/{branch}"))
    for line in result.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1] == f"refs/heads/{branch}":
            return parts[0]
    raise GitRemoteError(f"remote branch {remote}/{branch} does not exist")


def _fetch_branch(
    context: GitFileContext,
    remote: str,
    branch: str,
    *,
    progress: Optional[Callable[[str], None]] = None,
) -> str:
    if progress is not None:
        progress(f"Fetching Git objects for {remote}/{branch}…")
    result = _git(
        context.root,
        "fetch",
        "--quiet",
        "--no-tags",
        remote,
        f"refs/heads/{branch}",
        timeout=15.0,
    )
    if result.returncode != 0:
        raise GitRemoteError(_failure(result, f"could not fetch {remote}/{branch}"))
    if progress is not None:
        progress("Resolving fetched commit…")
    resolved = _git(context.root, "rev-parse", "FETCH_HEAD")
    if resolved.returncode != 0:
        raise GitRemoteError(_failure(resolved, "could not resolve fetched commit"))
    return resolved.stdout.strip()


def read_remote_snapshot(
    context: GitFileContext,
    remote: str,
    branch: str,
    encoding: str,
    *,
    expected_sha: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Tuple[str, List[str]]:
    """Fetch one remote branch and return the repository-relative file snapshot."""
    wanted_sha = expected_sha or remote_head_sha(context, remote, branch, progress=progress)
    fetched_sha = _fetch_branch(context, remote, branch, progress=progress)
    sha = fetched_sha or wanted_sha
    if progress is not None:
        progress(f"Loading {context.relative_path} from {remote}/{branch}…")
    result = _git(
        context.root,
        "show",
        f"{sha}:{context.relative_path}",
        text=False,
    )
    if result.returncode != 0:
        message = _failure(result, f"{context.relative_path} is unavailable at {remote}/{branch}")
        lowered = message.lower()
        if "does not exist" in lowered or "exists on disk, but not in" in lowered or "path '" in lowered:
            raise GitRemoteMissingFile(
                f"{context.relative_path} does not exist on {remote}/{branch}"
            )
        raise GitRemoteError(message)
    payload = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")
    text = payload.decode(encoding, errors="replace")
    return sha, text.splitlines(keepends=True)


class GitRemoteFollower:
    """Follow one repository-relative file as a remote branch advances."""

    finished = False

    def __init__(
        self,
        context: GitFileContext,
        remote: str,
        branch: str,
        args,
        *,
        check_interval: float = REMOTE_CHECK_INTERVAL,
    ) -> None:
        self.context = context
        self.remote = remote
        self.branch = branch
        self.args = args
        self.path = context.root / context.relative_path
        self.label = f"{remote}/{branch}"
        self.check_interval = max(1.0, check_interval)
        self.previous: List[str] = []
        self.remote_sha: Optional[str] = None
        self.initialized = False
        self.file_missing = False
        self.last_update_time: Optional[float] = None
        self.update_number = 0
        self._next_check = 0.0
        self._last_error: Optional[str] = None
        self._check_thread: Optional[threading.Thread] = None
        self._check_result = None

    def close(self) -> None:
        return

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        return f"@{self.remote_sha[:7]}" if self.remote_sha else "REMOTE"

    def _initial_tail(self) -> List[str]:
        if self.args.lines is None:
            return list(self.previous)
        if self.args.lines == 0:
            return []
        return list(self.previous[-self.args.lines:])

    def initialize_if_available(
        self,
        progress: Optional[Callable[[str], None]] = None,
    ) -> WatchNotice:
        if self.initialized:
            return WatchNotice("initial", initial_tail=self._initial_tail())
        try:
            sha, lines = read_remote_snapshot(
                self.context,
                self.remote,
                self.branch,
                self.args.encoding,
                progress=progress,
            )
        except GitRemoteError as exc:
            return WatchNotice("error", str(exc))
        self.previous = lines
        self.remote_sha = sha
        self.initialized = True
        self.file_missing = False
        self._last_error = None
        self._next_check = time.monotonic() + self.check_interval
        return WatchNotice("initial", initial_tail=self._initial_tail())

    def _check_once(self, now: float):
        try:
            sha = remote_head_sha(self.context, self.remote, self.branch)
        except GitRemoteError as exc:
            message = str(exc)
            if message == self._last_error:
                return None
            self._last_error = message
            return WatchNotice("error", message)
        self._last_error = None
        if sha == self.remote_sha:
            return None

        was_missing = self.file_missing
        try:
            fetched_sha, current = read_remote_snapshot(
                self.context,
                self.remote,
                self.branch,
                self.args.encoding,
                expected_sha=sha,
            )
        except GitRemoteMissingFile as exc:
            self.remote_sha = sha
            self.file_missing = True
            if not was_missing:
                return WatchNotice("missing", str(exc))
            return None
        except GitRemoteError as exc:
            message = str(exc)
            if message == self._last_error:
                return None
            self._last_error = message
            return WatchNotice("error", message)

        analysis = analyze_changes(self.previous, current)
        self.previous = current
        self.remote_sha = fetched_sha
        self.file_missing = False
        if not analysis.events:
            return WatchNotice("resumed", f"resumed {self.label}") if was_missing else None

        elapsed = None if self.last_update_time is None else now - self.last_update_time
        self.last_update_time = now
        self.update_number += 1
        return WatchUpdate(
            update_number=self.update_number,
            events=analysis.events,
            added=analysis.added,
            replaced=analysis.replaced,
            deleted=analysis.deleted,
            elapsed=elapsed,
            now_monotonic=now,
            current_snapshot=self.previous,
            changed_new_indices=analysis.changed_new_indices,
        )

    def _check_worker(self, now: float) -> None:
        try:
            self._check_result = self._check_once(now)
        except Exception as exc:
            self._check_result = WatchNotice("error", f"{self.label}: {exc}")

    def poll(self, now: Optional[float] = None):
        now = time.monotonic() if now is None else now
        if not self.initialized:
            return self.initialize_if_available()

        if self._check_thread is not None:
            if self._check_thread.is_alive():
                return None
            self._check_thread = None
            result = self._check_result
            self._check_result = None
            if result is not None:
                return result

        if now < self._next_check:
            return None
        self._next_check = now + self.check_interval
        self._check_thread = threading.Thread(
            target=self._check_worker,
            args=(now,),
            name=f"htail-git-{self.remote}-{self.branch}",
            daemon=True,
        )
        self._check_thread.start()
        return None