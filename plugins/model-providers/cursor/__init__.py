"""Cursor provider profile (fork patch P41).

Choosing this provider hands the whole turn to the Cursor SDK worker (``agent/cursor_runtime.py``): Cursor owns the
model loop, Hermes executes every tool call, so approvals, the sandbox and redaction stay Hermes'. The wire fields
here (base URL, auth) serve model discovery and the picker only; the worker authenticates with ``CURSOR_API_KEY``.
"""

import base64
import json
import re
import subprocess
import tempfile
import urllib.request
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile, _profile_user_agent

# Picker variant suffixes Hermes synthesizes from the catalog (see _variant_ids); the worker
# (hermes/cursor-runtime model-params.ts) parses the same shapes back into catalog params.
_VARIANT_CONTEXT_SUFFIX = re.compile(r"-(\d+)(k|m)$")
_CONTEXT_SCALE = {"k": 1_000, "m": 1_000_000}


def _ids(data: Any) -> list[str]:
    """Model ids from a catalog body: a list or a dict holding one (``models``/``items``/``data``), of strings or
    ``{"id": ...}`` objects. Order kept, duplicates dropped."""
    items = data
    if isinstance(data, dict):
        items = next((data[key] for key in ("models", "items", "data") if isinstance(data.get(key), list)), [])
    found = [it if isinstance(it, str) else (it or {}).get("id") for it in items or []]
    return list(dict.fromkeys(str(i) for i in found if i))


def _catalog_entry_ids(entry: Any) -> list[str]:
    """The picker ids for one SDK-catalog model: its base id plus one id per selectable context/fast
    variant. Context values come from the entry's variants (ground truth — the declared ``context``
    parameter can lag them), falling back to that parameter's declared values; fast pairs with each
    context value when a variant turns it on. A bare ``-fast`` id only exists for context-less models."""
    if not isinstance(entry, dict):
        return []
    base = str(entry.get("id") or "")
    if not base:
        return []
    variants = [v for v in entry.get("variants") or [] if isinstance(v, dict)]
    variant_params = [p for v in variants for p in (v.get("params") or []) if isinstance(p, dict)]
    context_values = list(
        dict.fromkeys(
            str(p.get("value")) for p in variant_params if p.get("id") == "context" and p.get("value")
        )
    )
    if not context_values:
        context_values = [
            str(v.get("value"))
            for p in entry.get("parameters") or []
            if isinstance(p, dict) and p.get("id") == "context"
            for v in p.get("values") or []
            if isinstance(v, dict) and v.get("value")
        ]
    fast_offered = any(p.get("id") == "fast" and p.get("value") == "true" for p in variant_params)
    ids = [base]
    if context_values:
        for value in context_values:
            ids.append(f"{base}-{value}")
            if fast_offered:
                ids.append(f"{base}-{value}-fast")
    elif fast_offered:
        ids.append(f"{base}-fast")
    return ids


class _CursorProfile(ProviderProfile):
    def fetch_models(self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0):
        """Every model the account can use, from Cursor's catalog (HTTP Basic, the key as the user name), or None
        when it cannot be read: the worker then checks the chosen id against the SDK catalog itself."""
        if not api_key:
            return None
        from_sdk = self._sdk_models(api_key)
        if from_sdk:
            return from_sdk
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

    @staticmethod
    def _sdk_models(api_key: str) -> list[str] | None:
        """The SDK's own catalog through the worker (`--list-models`): the base models (grok-4.7, grok-4.6 ...) the
        worker will accept, with effort and fast as parameters, expanded into picker variant ids. Cursor's REST list
        holds only a few pre-flattened variants, so it hid models the SDK serves. None when no worker is configured
        or it fails."""
        from agent.cursor_runtime import CursorRuntimeError, _worker_command, _worker_env

        try:
            command = [*_worker_command(), "--list-models"]
            with tempfile.TemporaryDirectory() as scratch:
                done = subprocess.run(
                    command, env=_worker_env(api_key, scratch), capture_output=True, text=True, timeout=30,
                    stdin=subprocess.DEVNULL, check=False,
                )
            if done.returncode != 0:
                return None
            catalog = json.loads(done.stdout)
            return list(dict.fromkeys(
                model_id
                for entry in (catalog if isinstance(catalog, list) else [])
                for model_id in _catalog_entry_ids(entry)
            )) or None
        except (CursorRuntimeError, OSError, ValueError, subprocess.SubprocessError):
            return None

    def get_model_context_length(self, model: str) -> int | None:
        """A picker variant id carries its own window (`grok-4.7-500k`, `claude-sonnet-5-5-1m`) and must not fall
        through to the generic chain, which knows the bare slug only and would leave the variant at the 256K
        fallback — compressing a 500K turn at a 256K threshold. The worker validates the full id against the live
        catalog at turn time, so an unknown base fails there instead."""
        slug = str(model or "").strip().lower()
        match = _VARIANT_CONTEXT_SUFFIX.search(slug)
        if match is None and slug.endswith("-fast"):
            match = _VARIANT_CONTEXT_SUFFIX.search(slug[: -len("-fast")])
        return int(match.group(1)) * _CONTEXT_SCALE[match.group(2)] if match else None


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
