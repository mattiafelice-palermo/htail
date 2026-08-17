"""htail application package."""

VERSION = "0.16.19"

# Keep the frozen compatibility core unchanged on disk while layering current
# application rendering features onto its highlighter API.
from .markdown_render import install as _install_rendering_extensions
from .update_transport import install as _install_update_transport
from .release_notes import install as _install_release_notes
from .git_source_prefetch import install as _install_git_source_prefetch
from .render_perf import install as _install_render_perf
from .terminal_fast import install as _install_terminal_fast
from .input_accel import install as _install_input_accel
from .perf_trace import install as _install_perf_trace
from .syntax_features import install as _install_syntax_features
from .terminal_cells import install as _install_terminal_cells

_install_rendering_extensions()
_install_update_transport()
_install_release_notes()
_install_git_source_prefetch()
_install_render_perf()
_install_terminal_fast()
_install_input_accel()
_install_perf_trace()
_install_syntax_features()
_install_terminal_cells()
del _install_rendering_extensions
del _install_update_transport
del _install_release_notes
del _install_git_source_prefetch
del _install_render_perf
del _install_terminal_fast
del _install_input_accel
del _install_perf_trace
del _install_syntax_features
del _install_terminal_cells
