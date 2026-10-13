"""Per-machine overlay from ``<hermes home>/host.yaml``, applied to the runtime config only.

``save_config`` persists the dict it is given, so overlay values must stay out of that dict:
``config.load_runtime_config`` applies this overlay on read paths, never on the save path.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import hermes_yaml as yaml
from hermes_constants import get_hermes_home

HOST_FILE_NAME = "host.yaml"
_SUBAGENT_SCOPE = "subagent-scope"

logger = logging.getLogger(__name__)


def read_host_file(home: Path | None = None) -> dict[str, Any] | None:
    """Parse ``<home>/host.yaml``. ``None`` when the file is absent, empty, or not a mapping."""
    path = (home if home is not None else get_hermes_home()) / HOST_FILE_NAME
    if not path.is_file():
        return None
    with open(path, encoding="utf-8-sig") as f:
        data = yaml.safe_load(f)
    if data is None:
        return None
    if not isinstance(data, dict):
        logger.warning("ignoring %s: top level is not a mapping", path)
        return None
    return data


def _set_path(root: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
    """Set ``root[k0][k1]...`` to *value*, creating or replacing non-dict parents on the way."""
    node = root
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def apply_host_overlay(
    config: dict[str, Any], host: dict[str, Any] | None
) -> dict[str, Any]:
    """Return a copy of *config* with host-file values laid over it. *config* is not mutated.

    Copies only ``model.provider``, ``model.default`` (non-empty strings), ``agent.reasoning_effort``
    and per-field roster entries. Gateway keys are never read from the host file.
    """
    merged = copy.deepcopy(config)
    if host is None:
        return merged

    host_model = host.get("model")
    if isinstance(host_model, dict):
        for key in ("provider", "default"):
            value = host_model.get(key)
            if isinstance(value, str) and value:
                _set_path(merged, ("model", key), value)

    host_agent = host.get("agent")
    if isinstance(host_agent, dict) and host_agent.get("reasoning_effort") is not None:
        _set_path(merged, ("agent", "reasoning_effort"), host_agent["reasoning_effort"])

    host_roster = host.get("roster")
    if isinstance(host_roster, dict):
        for role, fields in host_roster.items():
            if not isinstance(fields, dict):
                continue
            for field, value in fields.items():
                _set_path(
                    merged,
                    ("plugins", "entries", _SUBAGENT_SCOPE, "roster", role, field),
                    value,
                )
    return merged
