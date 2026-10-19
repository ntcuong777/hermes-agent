"""Cursor provider profile (fork patch P41).

Choosing this provider hands the whole turn to the Cursor SDK worker (``agent/cursor_runtime.py``): Cursor owns the
model loop, Hermes executes every tool call, so approvals, the sandbox and redaction stay Hermes'. The wire fields
here (base URL, auth) serve model discovery and the picker only; the worker authenticates with ``CURSOR_API_KEY``.
"""

import base64
import json
import urllib.request
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile, _profile_user_agent


def _ids(data: Any) -> list[str]:
    """Model ids from a catalog body: a list or a dict holding one (``models``/``items``/``data``), of strings or
    ``{"id": ...}`` objects. Order kept, duplicates dropped."""
    items = data
    if isinstance(data, dict):
        items = next((data[key] for key in ("models", "items", "data") if isinstance(data.get(key), list)), [])
    found = [it if isinstance(it, str) else (it or {}).get("id") for it in items or []]
    return list(dict.fromkeys(str(i) for i in found if i))


class _CursorProfile(ProviderProfile):
    def fetch_models(self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0):
        """Every model the account can use, from Cursor's catalog (HTTP Basic, the key as the user name), or None
        when it cannot be read: the worker then checks the chosen id against the SDK catalog itself."""
        if not api_key:
            return None
        from hermes_cli.urllib_security import open_credentialed_url

        req = urllib.request.Request(self.models_url)
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{api_key}:".encode()).decode())
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", _profile_user_agent())
        try:
            with open_credentialed_url(req, timeout=timeout) as resp:
                return _ids(json.loads(resp.read().decode())) or None
        except (OSError, ValueError):
            return None


cursor = _CursorProfile(
    name="cursor",
    display_name="Cursor",
    description="Cursor models on the Cursor SDK; Hermes runs the tools",
    signup_url="https://cursor.com/dashboard?tab=integrations",
    env_vars=("CURSOR_API_KEY",),
    base_url="https://api.cursor.com",
    models_url="https://api.cursor.com/v0/models",
    auth_type="api_key",
)

register_provider(cursor)
