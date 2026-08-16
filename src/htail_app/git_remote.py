from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .watcher import WatchNotice, WatchUpdate, analyze_changes


REMOTE_CHECK_INTERVAL = 3.0
_CACHE_LOCKS: Dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


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
    sha: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.remote}/{self.branch}"


def _git(root: Path, *args: str, timeout: float = 8.0, text: bool = True) -> subprocess.CompletedProcess:
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


def _cached_remote_refs(context: GitFileContext, remote: str) -> List[GitRemoteRef]:
    result = _git(context.root, "for-each-ref", "--format=%(refname:short) %(objectname)", f"refs/remotes/{remote}")
    if result.returncode != 0:
        return []
    prefix = remote + "/"
    refs: List[GitRemoteRef] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if not parts or not parts[0].startswith(prefix):
            continue
        branch = parts[0][len(prefix):]
        if branch and branch != "HEAD":
            refs.append(GitRemoteRef(remote, branch, parts[1] if len(parts) == 2 else None))
    return sorted(refs, key=lambda ref: ref.branch)


def list_remote_refs(context: GitFileContext) -> Tuple[List[GitRemoteRef], Optional[str]]:
    refs: List[GitRemoteRef] = []
    warnings: List[str] = []
    for remote in context.remotes:
        result = _git(context.root, "ls-remote", "--heads", remote)
        if result.returncode == 0:
            remote_refs: List[GitRemoteRef] = []
            for line in result.stdout.splitlines():
                parts = line.split(None, 1)
                if len(parts) != 2 or not parts[1].startswith("refs/heads/"):
                    continue
                remote_refs.append(GitRemoteRef(remote, parts[1][len("refs/heads/"):], parts[0]))
        else:
            remote_refs = _cached_remote_refs(context, remote)
            warnings.append(f"{remote}: {_failure(result, 'could not query remote branches')}")
        refs.extend(sorted(remote_refs, key=lambda ref: ref.branch))
    return refs, "; ".join(warnings) if warnings else None


def remote_head_sha(context: GitFileContext, remote: str, branch: str, *, progress: Optional[Callable[[str], None]] = None) -> str:
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


def _fetch_branch(context: GitFileContext, remote: str, branch: str, *, progress: Optional[Callable[[str], None]] = None) -> str:
    if progress is not None:
        progress(f"Compatibility fetch for {remote}/{branch}…")
    remote_key = hashlib.sha1(remote.encode("utf-8", errors="replace")).hexdigest()[:12]
    target_ref = f"refs/htail/remotes/{remote_key}/{branch}"
    result = _git(
        context.root,
        "fetch", "--quiet", "--no-tags", "--depth=1", remote,
        f"+refs/heads/{branch}:{target_ref}",
        timeout=15.0,
    )
    if result.returncode != 0:
        raise GitRemoteError(_failure(result, f"could not fetch {remote}/{branch}"))
    resolved = _git(context.root, "rev-parse", target_ref)
    if resolved.returncode != 0:
        raise GitRemoteError(_failure(resolved, "could not resolve fetched commit"))
    return resolved.stdout.strip()


def _commit_is_local(context: GitFileContext, sha: str) -> bool:
    return _git(context.root, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _remote_url(context: GitFileContext, remote: str) -> str:
    result = _git(context.root, "remote", "get-url", remote)
    if result.returncode != 0:
        raise GitRemoteError(_failure(result, f"could not resolve remote {remote}"))
    url = result.stdout.strip()
    if not url:
        raise GitRemoteError(f"remote {remote} has no URL")
    # Git resolves path-like remote URLs relative to the repository. The htail
    # cache lives elsewhere, so preserve that meaning by converting them to an
    # absolute path before configuring the cache remote.
    if "://" not in url and not (":" in url and not url.startswith(("./", "../", "/"))):
        url = str((context.root / url).resolve()) if not Path(url).is_absolute() else str(Path(url).resolve())
    return url


def _cache_root() -> Path:
    override = os.environ.get("HTAIL_GIT_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "htail" / "git-remote"


def _cache_path(remote_url: str) -> Path:
    key = hashlib.sha256(remote_url.encode("utf-8", errors="replace")).hexdigest()[:24]
    return _cache_root() / f"{key}.git"


def _cache_lock(cache: Path) -> threading.Lock:
    key = str(cache)
    with _CACHE_LOCKS_GUARD:
        return _CACHE_LOCKS.setdefault(key, threading.Lock())


def _ensure_partial_cache(context: GitFileContext, remote: str) -> Path:
    url = _remote_url(context, remote)
    cache = _cache_path(url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not (cache / "HEAD").exists():
        cache.mkdir(parents=True, exist_ok=True)
        init = _git(cache, "init", "--bare")
        if init.returncode != 0:
            raise GitRemoteError(_failure(init, "could not initialize htail Git cache"))
    settings = (
        ("remote.htail.url", url),
        ("extensions.partialClone", "htail"),
        ("remote.htail.promisor", "true"),
        ("remote.htail.partialclonefilter", "blob:none"),
        ("promisor.quiet", "true"),
    )
    for key, value in settings:
        configured = _git(cache, "config", key, value)
        if configured.returncode != 0:
            raise GitRemoteError(_failure(configured, f"could not configure htail Git cache ({key})"))
    return cache


def _partial_remote_snapshot(
    context: GitFileContext,
    remote: str,
    branch: str,
    encoding: str,
    *,
    progress: Optional[Callable[[str], None]] = None,
) -> Tuple[str, List[str]]:
    cache = _ensure_partial_cache(context, remote)
    with _cache_lock(cache):
        target_ref = "refs/htail/branches/" + hashlib.sha1(
            f"{remote}\0{branch}".encode("utf-8", errors="replace")
        ).hexdigest()
        if progress is not None:
            progress(f"Fetching Git metadata for {remote}/{branch}…")
        fetched = _git(
            cache,
            "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--depth=1",
            "--filter=blob:none", "htail", f"+refs/heads/{branch}:{target_ref}",
            timeout=15.0,
        )
        if fetched.returncode != 0:
            raise GitRemoteError(_failure(fetched, f"partial fetch unavailable for {remote}/{branch}"))
        resolved = _git(cache, "rev-parse", target_ref)
        if resolved.returncode != 0:
            raise GitRemoteError(_failure(resolved, "could not resolve partial-fetch commit"))
        sha = resolved.stdout.strip()
        if progress is not None:
            progress(f"Fetching {context.relative_path}…")
        shown = _git(cache, "show", f"{sha}:{context.relative_path}", text=False, timeout=15.0)
        if shown.returncode != 0:
            message = _failure(shown, f"{context.relative_path} is unavailable at {remote}/{branch}")
            lowered = message.lower()
            if "does not exist" in lowered or "exists on disk, but not in" in lowered or "path '" in lowered:
                raise GitRemoteMissingFile(f"{context.relative_path} does not exist on {remote}/{branch}")
            raise GitRemoteError(message)
        payload = shown.stdout if isinstance(shown.stdout, bytes) else shown.stdout.encode("utf-8")
        return sha, payload.decode(encoding, errors="replace").splitlines(keepends=True)


def _show_local_snapshot(context: GitFileContext, sha: str, remote: str, branch: str, encoding: str) -> List[str]:
    result = _git(context.root, "show", f"{sha}:{context.relative_path}", text=False)
    if result.returncode != 0:
        message = _failure(result, f"{context.relative_path} is unavailable at {remote}/{branch}")
        lowered = message.lower()
        if "does not exist" in lowered or "exists on disk, but not in" in lowered or "path '" in lowered:
            raise GitRemoteMissingFile(f"{context.relative_path} does not exist on {remote}/{branch}")
        raise GitRemoteError(message)
    payload = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")
    return payload.decode(encoding, errors="replace").splitlines(keepends=True)


def read_remote_snapshot(
    context: GitFileContext,
    remote: str,
    branch: str,
    encoding: str,
    *,
    expected_sha: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Tuple[str, List[str]]:
    wanted_sha = expected_sha or remote_head_sha(context, remote, branch, progress=progress)
    if _commit_is_local(context, wanted_sha):
        if progress is not None:
            progress(f"Using cached Git objects for {remote}/{branch}…")
        return wanted_sha, _show_local_snapshot(context, wanted_sha, remote, branch, encoding)

    try:
        return _partial_remote_snapshot(context, remote, branch, encoding, progress=progress)
    except GitRemoteMissingFile:
        raise
    except GitRemoteError:
        # Older/non-filtering servers still work through the previous shallow
        # fetch path. This may transfer more blobs, but preserves compatibility.
        sha = _fetch_branch(context, remote, branch, progress=progress)
        if progress is not None:
            progress(f"Loading {context.relative_path} from {remote}/{branch}…")
        return sha, _show_local_snapshot(context, sha, remote, branch, encoding)


class GitRemoteFollower:
    finished = False

    def __init__(self, context: GitFileContext, remote: str, branch: str, args, *, check_interval: float = REMOTE_CHECK_INTERVAL, initial_sha: Optional[str] = None) -> None:
        self.context = context
        self.remote = remote
        self.branch = branch
        self.args = args
        self.path = context.root / context.relative_path
        self.label = f"{remote}/{branch}"
        self.check_interval = max(1.0, check_interval)
        self.previous: List[str] = []
        self.remote_sha: Optional[str] = None
        self.initial_sha = initial_sha
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

    def initialize_if_available(self, progress: Optional[Callable[[str], None]] = None) -> WatchNotice:
        if self.initialized:
            return WatchNotice("initial", initial_tail=self._initial_tail())
        try:
            sha, lines = read_remote_snapshot(
                self.context, self.remote, self.branch, self.args.encoding,
                expected_sha=self.initial_sha, progress=progress,
            )
        except GitRemoteError as exc:
            return WatchNotice("error", str(exc))
        self.previous = lines
        self.remote_sha = sha
        self.initial_sha = None
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
                self.context, self.remote, self.branch, self.args.encoding, expected_sha=sha,
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
            update_number=self.update_number, events=analysis.events,
            added=analysis.added, replaced=analysis.replaced, deleted=analysis.deleted,
            elapsed=elapsed, now_monotonic=now, current_snapshot=self.previous,
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
            target=self._check_worker, args=(now,), name=f"htail-git-{self.remote}-{self.branch}", daemon=True,
        )
        self._check_thread.start()
        return None
