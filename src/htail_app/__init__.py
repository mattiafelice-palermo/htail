"""htail application package."""

VERSION = "0.16.16"

# Keep the frozen compatibility core unchanged on disk while layering current
# application rendering features onto its highlighter API.
from .markdown_render import install as _install_rendering_extensions
from .update_transport import install as _install_update_transport
from .release_notes import install as _install_release_notes
from .git_source_prefetch import install as _install_git_source_prefetch
from .render_perf import install as _install_render_perf
from .terminal_fast import install as _install_terminal_fast

_install_rendering_extensions()
_install_update_transport()
_install_release_notes()
_install_git_source_prefetch()
_install_render_perf()
_install_terminal_fast()
del _install_rendering_extensions
del _install_update_transport
del _install_release_notes
del _install_git_source_prefetch
del _install_render_perf
del _install_terminal_fast
