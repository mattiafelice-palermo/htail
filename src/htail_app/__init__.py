"""htail application package."""

VERSION = "0.16.13"

# Keep the frozen compatibility core unchanged on disk while layering current
# application rendering features onto its highlighter API.
from .markdown_render import install as _install_rendering_extensions
from .update_transport import install as _install_update_transport

_install_rendering_extensions()
_install_update_transport()
del _install_rendering_extensions
del _install_update_transport
