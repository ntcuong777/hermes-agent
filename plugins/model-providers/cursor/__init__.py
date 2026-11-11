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
import time
import urllib.request
from pathlib import Path
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile, _profile_user_agent

# Picker variant suffixes Hermes synthesizes from the catalog (see _variant_ids); the worker
# (hermes/cursor-runtime model-params.ts) parses the same shapes back into catalog params.
_VARIANT_CONTEXT_SUFFIX = re.compile(r"-(\d+)(k|m)$")
_CONTEXT_SCALE = {"k": 1_000, "m": 1_000_000}
# Display metadata for the /model picker rows (context window, fast, max mode), read by
# hermes_cli/cursor_model_meta.py. Regenerated on every SDK-catalog fetch; display-only.
_META_SIDECAR = Path("cache") / "cursor_models_meta.json"


def _ids(data: Any) -> list[str]:
    """Model ids from a catalog body: a list or a dict holding one (``models``/``items``/``data``), of strings or
    ``{"id": ...}`` objects. Order kept, duplicates dropped."""
    items = data
    if isinstance(data, dict):
        items = next((data[key] for key in ("models", "items", "data") if isinstance(data.get(key), list)), [])
    found = [it if isinstance(it, str) else (it or {}).get("id") for it in items or []]
    return list(dict.fromkeys(str(i) for i in found if i))


def _catalog_entry_ids_and_meta(entry: Any) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """The picker ids for one SDK-catalog model — its base id plus one id per selectable context/fast
    variant — with the display metadata each id shows in the picker. Context values come from the
    entry's variants (ground truth — the declared ``context`` parameter can lag them), falling back to
    that parameter's declared values; fast pairs with each context value when a variant turns it on.
    A bare ``-fast`` id only exists for context-less models. The base id's metadata is the default
    variant's (that is what the backend runs when only the base id is sent)."""
    if not isinstance(entry, dict):
        return [], {}
    base = str(entry.get("id") or "")
    if not base:
        return [], {}
    variants = [v for v in entry.get("variants") or [] if isinstance(v, dict)]

    def param(variant: dict, pid: str) -> str | None:
        return next(
            (str(p.get("value")) for p in variant.get("params") or []
             if isinstance(p, dict) and p.get("id") == pid and p.get("value") is not None),
            None,
        )

    context_values = list(dict.fromkeys(
        ctx for ctx in (param(v, "context") for v in variants) if ctx
    ))
    if not context_values:
        context_values = [
            str(v.get("value"))
            for p in entry.get("parameters") or []
            if isinstance(p, dict) and p.get("id") == "context"
            for v in p.get("values") or []
            if isinstance(v, dict) and v.get("value")
        ]
    fast_offered = any(param(v, "fast") == "true" for v in variants)

    def group_meta(context: str | None, fast: bool, members: list[dict]) -> dict[str, Any]:
        out: dict[str, Any] = {"fast": fast, "max": any(v.get("isMaxMode") for v in members)}
        if context is not None:
            out = {"context": context, **out}
        return out

    meta: dict[str, dict[str, Any]] = {}
    ids = [base]
    if context_values:
        for value in context_values:
            ids.append(f"{base}-{value}")
            meta[f"{base}-{value}"] = group_meta(
                value, False, [v for v in variants if param(v, "context") == value and param(v, "fast") != "true"])
            if fast_offered:
                ids.append(f"{base}-{value}-fast")
                meta[f"{base}-{value}-fast"] = group_meta(
                    value, True, [v for v in variants if param(v, "context") == value and param(v, "fast") == "true"])
    elif fast_offered:
        ids.append(f"{base}-fast")
        meta[f"{base}-fast"] = group_meta(
            None, True, [v for v in variants if param(v, "fast") == "true"])
    default = next((v for v in variants if v.get("isDefault")), variants[0] if variants else None)
    if default is not None:
        meta[base] = {
            **({"context": ctx} if (ctx := param(default, "context")) is not None else {}),
            "fast": param(default, "fast") == "true",
            "max": bool(default.get("isMaxMode")),
        }
    else:
        meta[base] = {"fast": False, "max": False}
    return ids, meta


def _catalog_entry_ids(entry: Any) -> list[str]:
    """Picker ids only; see :func:`_catalog_entry_ids_and_meta`."""
    return _catalog_entry_ids_and_meta(entry)[0]


def _write_meta_sidecar(meta: dict[str, dict[str, Any]]) -> None:
    """Persist the picker metadata next to the models cache. Best-effort: a failed write only
    costs the row tags, never the model list."""
    if not meta:
        return
    try:
        from hermes_constants import get_hermes_home

        path = Path(get_hermes_home()) / _META_SIDECAR
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": int(time.time()), "models": meta}))
    except OSError:
        pass


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
            if not isinstance(catalog, list):
                return None
            ids: list[str] = []
            meta: dict[str, dict[str, Any]] = {}
            for entry in catalog:
                entry_ids, entry_meta = _catalog_entry_ids_and_meta(entry)
                for model_id in entry_ids:
                    if model_id not in meta:
                        ids.append(model_id)
                        meta[model_id] = entry_meta[model_id]
            if not ids:
                return None
            _write_meta_sidecar(meta)
            return ids
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
