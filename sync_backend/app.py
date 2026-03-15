from __future__ import annotations

try:
    from app_core import app  # noqa: F401
    import app_routes_auth  # noqa: F401
    import app_routes_storage  # noqa: F401
    import app_routes_images  # noqa: F401
    import app_routes_sync  # noqa: F401
except ImportError:
    from .app_core import app  # noqa: F401
    from . import app_routes_auth  # noqa: F401
    from . import app_routes_storage  # noqa: F401
    from . import app_routes_images  # noqa: F401
    from . import app_routes_sync  # noqa: F401

__all__ = ["app"]
