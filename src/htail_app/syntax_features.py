"""Bundled syntax-highlighting UX and persistent terminal color themes."""

from __future__ import annotations

import builtins
import os
import subprocess
import sys
from typing import Optional

from . import core


DEFAULT_THEME = "default"
CURATED_THEMES = (
    "default",
    "monokai",
    "native",
    "dracula",
    "github-dark",
    "gruvbox-dark",
    "nord",
    "one-dark",
    "solarized-dark",
    "material",
    "vim",
    "zenburn",
    "solarized-light",
    "friendly",
    "xcode",
)

_ACTIVE_THEME = DEFAULT_THEME
_ORIGINAL_FORMATTER = None
_ORIGINAL_LOAD_PYGMENTS = None


def _load_state() -> dict:
    try:
        state = core._load_app_state()
    except Exception:
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(state: dict) -> None:
    try:
        core._save_app_state(state)
    except Exception:
        pass


def current_theme() -> str:
    return _ACTIVE_THEME


def _set_active_theme(name: Optional[str], *, persist: bool = False) -> str:
    global _ACTIVE_THEME
    candidate = (name or DEFAULT_THEME).strip().lower()
    if candidate not in CURATED_THEMES:
        candidate = DEFAULT_THEME
    _ACTIVE_THEME = candidate
    if persist:
        state = _load_state()
        state["syntax_theme"] = candidate
        _save_state(state)
    return candidate


def _resolve_theme(explicit: Optional[str]) -> str:
    if explicit:
        return _set_active_theme(explicit, persist=True)
    env_theme = os.environ.get("HTAIL_THEME", "").strip().lower()
    if env_theme in CURATED_THEMES:
        return _set_active_theme(env_theme)
    state_theme = str(_load_state().get("syntax_theme", DEFAULT_THEME)).strip().lower()
    return _set_active_theme(state_theme)


def _wrap_terminal_formatter() -> None:
    """Make every Pygments terminal256 formatter use the selected theme."""
    global _ORIGINAL_FORMATTER
    formatter = core.Terminal256Formatter
    if formatter is None or getattr(formatter, "_htail_theme_formatter", False):
        return
    _ORIGINAL_FORMATTER = formatter

    class ThemedTerminal256Formatter(formatter):
        _htail_theme_formatter = True

        def __init__(self, *args, **kwargs) -> None:
            kwargs.setdefault("style", _ACTIVE_THEME)
            super().__init__(*args, **kwargs)

    core.Terminal256Formatter = ThemedTerminal256Formatter


def _install_pygments_loader_hook() -> None:
    global _ORIGINAL_LOAD_PYGMENTS
    if getattr(core.load_pygments, "_htail_theme_loader", False):
        _wrap_terminal_formatter()
        return
    _ORIGINAL_LOAD_PYGMENTS = core.load_pygments

    def load_pygments() -> bool:
        ok = bool(_ORIGINAL_LOAD_PYGMENTS())
        if ok:
            _wrap_terminal_formatter()
        return ok

    load_pygments._htail_theme_loader = True
    core.load_pygments = load_pygments
    _wrap_terminal_formatter()


def _persistent_pygments_offer(args, color: bool) -> None:
    """Offer optional source-checkout installation once, remembering the answer."""
    if core.HAVE_PYGMENTS:
        return
    if args.syntax.lower() == "none" or args.no_color or args.no_install_prompt:
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return

    state = _load_state()
    decision = state.get("pygments_install_prompt_decision")
    if decision in {"declined", "failed"}:
        return

    print(
        core.paint("[htail] Pygments is not installed.", core.BOLD_YELLOW, color)
        + " Install it now for richer syntax highlighting? [Y/n] ",
        end="",
        flush=True,
    )
    try:
        answer = builtins.input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer not in ("", "y", "yes"):
        state["pygments_install_prompt_decision"] = "declined"
        _save_state(state)
        return

    print("[htail] installing Pygments with this Python interpreter...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "Pygments"],
            check=False,
        )
    except OSError as exc:
        result = None
        print(
            core.paint(f"[htail] could not start pip: {exc}", core.BOLD_YELLOW, color),
            file=sys.stderr,
        )

    if result is None or result.returncode != 0 or not core.load_pygments():
        state["pygments_install_prompt_decision"] = "failed"
        _save_state(state)
        print(
            core.paint(
                "[htail] Pygments installation did not succeed; this prompt will not be repeated. "
                "Install Pygments manually to enable syntax highlighting.",
                core.BOLD_YELLOW,
                color,
            ),
            file=sys.stderr,
        )
        return

    state.pop("pygments_install_prompt_decision", None)
    _save_state(state)
    print("[htail] Pygments installed successfully.", flush=True)


def _install_app_theme_option() -> None:
    from . import app as app_module

    if getattr(app_module, "_htail_syntax_theme_extension", False):
        return

    original_build_parser = app_module.build_parser
    original_parse_args = app_module.parse_args
    original_main = app_module.main

    def build_parser():
        parser = original_build_parser()
        parser.add_argument(
            "--theme",
            choices=CURATED_THEMES,
            default=None,
            metavar="NAME",
            help=(
                "syntax color theme; an explicit choice is saved for future runs "
                "(default, monokai, native, dracula, github-dark, gruvbox-dark, nord, "
                "one-dark, solarized-dark, material, vim, zenburn, solarized-light, friendly, xcode)"
            ),
        )
        return parser

    def parse_args(argv=None):
        args = original_parse_args(argv)
        args.theme = _resolve_theme(getattr(args, "theme", None))
        return args

    def main(argv=None):
        requested = list(argv) if argv is not None else list(sys.argv[1:])
        if requested == ["--bundle-self-test"]:
            if not core.HAVE_PYGMENTS:
                print("htail bundle self-test failed: Pygments unavailable", file=sys.stderr)
                return 1
            try:
                core.get_lexer_for_filename("demo.py")
                core.get_lexer_for_filename("demo.sh")
            except Exception as exc:
                print(f"htail bundle self-test failed: Pygments lexers unavailable: {exc}", file=sys.stderr)
                return 1
        return original_main(argv)

    app_module.build_parser = build_parser
    app_module.parse_args = parse_args
    app_module.main = main
    app_module._htail_syntax_theme_extension = True


def install() -> None:
    if getattr(core, "_htail_syntax_features_extension", False):
        return
    _install_pygments_loader_hook()
    core.maybe_offer_pygments_install = _persistent_pygments_offer
    _install_app_theme_option()
    core._htail_syntax_features_extension = True
