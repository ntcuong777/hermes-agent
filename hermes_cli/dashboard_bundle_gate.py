"""Whether the web dashboard can serve a plugin's browser bundle (fork).

``web_routers/dashboard_ui.py`` keeps the upstream route; the gate lives here so upstream's
``web_server_dashboard`` stays upstream's. A manifest may ship only the backend layer: desktop
plugins share just ``/api/plugins/<name>`` with the web dashboard, so their ``dashboard/`` has an
``api`` and no bundle. Read per request rather than at discovery, so a bundle built later needs no
rescan.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def serves_browser_bundle(plugin: dict[str, Any]) -> bool:
    """Whether the asset route can serve the plugin's ``entry`` (a file inside ``dashboard/``)."""
    base = Path(plugin["_dir"]).resolve()
    target = (base / plugin["entry"]).resolve()
    return target.is_relative_to(base) and target.is_file()
