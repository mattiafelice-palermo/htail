"""Background Git source discovery and picker recommendation integration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from . import git_remote, watcher
from .git_remote import GitFileContext, GitRemoteRef


_ORIGINAL_FILE_INITIALIZE = watcher.FileFollower.initialize_if_available
_INDEX_TTL = 10.0
_PREFETCH_TTL = 15.0


@dataclass
class _FileSourcePrefetch:
    done: threading.Event
    encoding: str
    context: Optional[GitFileContext] = None
    refs: Tuple[GitRemoteRef, ...] = ()
    warning: Optional[str] = None
    recommended: Optional[Tuple[str, str]] = None
    finished_at: float = 0.0


_PREFETCH_LOCK = threading.Lock()
_PREFETCH: Dict[str, _FileSourcePrefetch] = {}
_INDEX_LOCK = threading.Lock()
_INDEX: Dict[str, Tuple[float, Tuple[GitRemoteRef, ...], Optional[str], Dict[str, bool]]] = {}
_METADATA_LOCK = threading.Lock()
_METADATA_INDEXED_AT: Dict[str, float] = {}


def _run_git(
    root: Path,
    *args: str,
    timeout: float = 12.0,
    text: bool = True,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update(extra_env)
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise git_remote.GitRemoteError(str(exc)) from exc


def _failure(result: subprocess.CompletedProcess, fallback: str) -> str:
    stderr = result.stderr
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return (stderr or "").strip() or fallback


def _context_key(context: GitFileContext) -> str:
    return str(context.root) + "\0" + "\0".join(context.remotes)


def _path_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def _context_path_key(context: GitFileContext) -> str:
    return _path_key(context.root / context.relative_path)


def _cached_remote_refs(context: GitFileContext, remote: str) -> List[GitRemoteRef]:
    result = _run_git(
        context.root,
        "for-each-ref",
        "--format=%(refname:short) %(objectname)",
        f"refs/remotes/{remote}",
    )
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
    return refs


def _remote_refs_with_capabilities(
    context: GitFileContext,
) -> Tuple[List[GitRemoteRef], Optional[str], Dict[str, bool]]:
    key = _context_key(context)
    now = time.monotonic()
    with _INDEX_LOCK:
        cached = _INDEX.get(key)
        if cached is not None and now - cached[0] < _INDEX_TTL:
            return list(cached[1]), cached[2], dict(cached[3])

    refs: List[GitRemoteRef] = []
    warnings: List[str] = []
    capabilities: Dict[str, bool] = {}
    for remote in context.remotes:
        result = _run_git(
            context.root,
            "-c", "protocol.version=2", "ls-remote", "--heads", remote,
            extra_env={"GIT_TRACE_PACKET": "1"},
        )
        trace = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")
        capabilities[remote] = bool(re.search(r"fetch=[^\n]*\bfilter\b", trace))
        if result.returncode == 0:
            remote_refs: List[GitRemoteRef] = []
            for line in result.stdout.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1].startswith("refs/heads/"):
                    remote_refs.append(GitRemoteRef(remote, parts[1][len("refs/heads/"):], parts[0]))
        else:
            remote_refs = _cached_remote_refs(context, remote)
            warnings.append(f"{remote}: {_failure(result, 'could not query remote branches')}")
        refs.extend(sorted(remote_refs, key=lambda ref: ref.branch))

    warning = "; ".join(warnings) if warnings else None
    with _INDEX_LOCK:
        _INDEX[key] = (time.monotonic(), tuple(refs), warning, dict(capabilities))
    return refs, warning, capabilities


def _partial_cache(context: GitFileContext, remote: str) -> Path:
    # Reuse the htail-owned promisor cache introduced in 0.16.13. The user
    # repository's refs/config remain untouched.
    return git_remote._ensure_partial_cache(context, remote)


def _index_remote_metadata(context: GitFileContext, remote: str) -> Path:
    cache = _partial_cache(context, remote)
    cache_key = str(cache)
    now = time.monotonic()
    with _METADATA_LOCK:
        indexed_at = _METADATA_INDEXED_AT.get(cache_key, 0.0)
        if now - indexed_at < _INDEX_TTL:
            return cache

    remote_key = hashlib.sha1(remote.encode("utf-8", errors="replace")).hexdigest()[:12]
    target = f"+refs/heads/*:refs/htail/discovery/{remote_key}/*"
    with git_remote._cache_lock(cache):
        result = _run_git(
            cache,
            "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--depth=1",
            "--filter=blob:none", "htail", target,
            timeout=20.0,
        )
    if result.returncode != 0:
        raise git_remote.GitRemoteError(_failure(result, f"could not index remote branches for {remote}"))
    stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")
    if "filtering not recognized by server" in stderr.lower():
        raise git_remote.GitRemoteError(f"{remote} does not support blobless Git filtering")
    with _METADATA_LOCK:
        _METADATA_INDEXED_AT[cache_key] = time.monotonic()
    return cache


def _ref_contains_file(cache: Path, ref: GitRemoteRef, relative_path: str) -> bool:
    if not ref.sha:
        return False
    result = _run_git(cache, "ls-tree", "-z", "--name-only", ref.sha, "--", relative_path, text=False)
    if result.returncode != 0:
        return False
    payload = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8", errors="surrogateescape")
    return os.fsencode(relative_path) in payload.split(b"\0")


def _local_branch(context: GitFileContext) -> Optional[str]:
    result = _run_git(context.root, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = result.stdout.strip() if result.returncode == 0 else ""
    return branch or None


def _recommended_ref(context: GitFileContext, refs: Sequence[GitRemoteRef]) -> Optional[Tuple[str, str]]:
    branch = _local_branch(context)
    if not branch:
        return None

    upstream = _run_git(context.root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream.returncode == 0:
        name = upstream.stdout.strip()
        for remote in sorted(context.remotes, key=len, reverse=True):
            prefix = remote + "/"
            if name.startswith(prefix):
                candidate = (remote, name[len(prefix):])
                if any((ref.remote, ref.branch) == candidate for ref in refs):
                    return candidate

    configured = _run_git(context.root, "config", "--get", f"branch.{branch}.remote")
    preferred = configured.stdout.strip() if configured.returncode == 0 else ""
    remote_order = [preferred] if preferred in context.remotes else []
    if "origin" in context.remotes and "origin" not in remote_order:
        remote_order.append("origin")
    remote_order.extend(remote for remote in context.remotes if remote not in remote_order)
    for remote in remote_order:
        candidate = (remote, branch)
        if any((ref.remote, ref.branch) == candidate for ref in refs):
            return candidate
    return next(((ref.remote, ref.branch) for ref in refs if ref.branch == branch), None)


def _compute_file_refs(context: GitFileContext) -> Tuple[List[GitRemoteRef], Optional[str], Optional[Tuple[str, str]]]:
    refs, warning, capabilities = _remote_refs_with_capabilities(context)
    warnings = [warning] if warning else []
    filtered: List[GitRemoteRef] = []
    for remote in context.remotes:
        remote_refs = [ref for ref in refs if ref.remote == remote]
        if not remote_refs:
            continue
        if not capabilities.get(remote, False):
            # Do not download every branch merely to test path presence. Older
            # servers retain the old unfiltered picker behavior.
            filtered.extend(remote_refs)
            warnings.append(f"{remote} does not advertise blobless filtering; showing unfiltered branches")
            continue
        try:
            cache = _index_remote_metadata(context, remote)
            filtered.extend(ref for ref in remote_refs if _ref_contains_file(cache, ref, context.relative_path))
        except git_remote.GitRemoteError as exc:
            filtered.extend(remote_refs)
            warnings.append(f"{remote}: {exc}; showing unfiltered branches")

    recommended = _recommended_ref(context, filtered)
    remote_rank = {remote: index for index, remote in enumerate(context.remotes)}
    if recommended is not None:
        recommended_remote = recommended[0]
        order = [recommended_remote] + [remote for remote in context.remotes if remote != recommended_remote]
        remote_rank = {remote: index for index, remote in enumerate(order)}
    filtered.sort(
        key=lambda ref: (
            0 if recommended == (ref.remote, ref.branch) else 1,
            remote_rank.get(ref.remote, len(remote_rank)),
            ref.branch,
        )
    )
    return filtered, "; ".join(item for item in warnings if item) or None, recommended


def _warm_recommended(
    context: GitFileContext,
    refs: Sequence[GitRemoteRef],
    recommended: Optional[Tuple[str, str]],
    encoding: str,
) -> None:
    if recommended is None:
        return
    ref = next((ref for ref in refs if (ref.remote, ref.branch) == recommended), None)
    if ref is None or not ref.sha:
        return
    try:
        git_remote.read_remote_snapshot(
            context,
            ref.remote,
            ref.branch,
            encoding,
            expected_sha=ref.sha,
        )
    except git_remote.GitRemoteError:
        # Warming is opportunistic. Selection can still use the normal async
        # source-switch path if the prefetch fails.
        return


def _prefetch_worker(path: Path, task: _FileSourcePrefetch) -> None:
    try:
        context = git_remote.discover_git_file(path)
        task.context = context
        if context is None or not context.remotes:
            return
        refs, warning, recommended = _compute_file_refs(context)
        task.refs = tuple(refs)
        task.warning = warning
        task.recommended = recommended
        _warm_recommended(context, refs, recommended, task.encoding)
    except Exception as exc:
        task.warning = f"background Git source discovery failed: {exc}"
    finally:
        task.finished_at = time.monotonic()
        task.done.set()


def schedule_file_source_prefetch(path: Path, encoding: str = "utf-8", *, force: bool = False) -> _FileSourcePrefetch:
    path = path.expanduser().resolve()
    key = _path_key(path)
    now = time.monotonic()
    with _PREFETCH_LOCK:
        existing = _PREFETCH.get(key)
        if existing is not None:
            if not existing.done.is_set():
                return existing
            if not force and now - existing.finished_at < _PREFETCH_TTL:
                return existing
        task = _FileSourcePrefetch(done=threading.Event(), encoding=encoding)
        _PREFETCH[key] = task
    threading.Thread(
        target=_prefetch_worker,
        args=(path, task),
        daemon=True,
        name="htail-git-source-prefetch",
    ).start()
    return task


def list_remote_file_refs(context: GitFileContext) -> Tuple[List[GitRemoteRef], Optional[str]]:
    key = _context_path_key(context)
    with _PREFETCH_LOCK:
        task = _PREFETCH.get(key)
    if task is None or (task.done.is_set() and time.monotonic() - task.finished_at >= _PREFETCH_TTL):
        task = schedule_file_source_prefetch(context.root / context.relative_path, force=True)
    task.done.wait(30.0)
    if task.done.is_set() and task.context is not None:
        return list(task.refs), task.warning
    # If an unusually slow background query exceeds the picker worker timeout,
    # preserve the original raw branch-list behavior rather than returning none.
    return git_remote.list_remote_refs(context)


def recommended_source(context: Optional[GitFileContext]) -> Optional[Tuple[str, str]]:
    if context is None:
        return None
    with _PREFETCH_LOCK:
        task = _PREFETCH.get(_context_path_key(context))
    if task is None or not task.done.is_set():
        return None
    return task.recommended


def _initialize_with_git_prefetch(self):
    notice = _ORIGINAL_FILE_INITIALIZE(self)
    try:
        schedule_file_source_prefetch(self.path, getattr(self.args, "encoding", "utf-8"))
    except Exception:
        # File watching must never fail because optional Git discovery failed.
        pass
    return notice


def _decorate_recommended_items(items, recommended, palette_item_type):
    if recommended is None:
        return items
    decorated = []
    for item in items:
        if item.action == "git-source-select" and item.value == recommended:
            label = item.label
            if label.startswith("✓ "):
                label = "✓ ★ " + label[2:]
            else:
                label = "★ " + label
            decorated.append(palette_item_type(label, item.action, item.value, item.detail))
        else:
            decorated.append(item)
    return decorated


def _install_picker_marker() -> None:
    # Importing app here is safe: VERSION and core extensions have already been
    # installed by package __init__, while no MultiApp instance exists yet.
    from . import app

    # app imported the raw branch-list function at module load time. Only the
    # source picker should use the path-filtered/prefetched variant.
    app.list_remote_refs = list_remote_file_refs

    original = app.MultiApp._palette_all_items

    def palette_all_items(self):
        items = original(self)
        if self.palette_mode != "git-source":
            return items
        return _decorate_recommended_items(
            items,
            recommended_source(getattr(self, "_git_source_context", None)),
            app.PaletteItem,
        )

    app.MultiApp._palette_all_items = palette_all_items


def install() -> None:
    if getattr(watcher.FileFollower, "_htail_git_prefetch_extension", False):
        return
    watcher.FileFollower.initialize_if_available = _initialize_with_git_prefetch
    watcher.FileFollower._htail_git_prefetch_extension = True
    _install_picker_marker()
