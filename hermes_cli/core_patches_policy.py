"""Fail-safe policy of the core-patch registry: the settings and where its files live (fork-owned).

STDLIB ONLY at module level, never raises. The policy: NOTHING in the patch system may prevent the
agent from starting or running. A patch that drifted, is missing, ungranted, broken, slow or
raising is skipped or switched off and the core runs its own upstream behaviour; the registry
reports it (logs, ``<hermes home>/core-patches/``, a notice to the home channel, a doctor finding).
Refusing to start exists only behind ``core_patches.refuse_on_critical: true`` (default false).

Config (top-level ``core_patches:`` in config.yaml; unknown top-level keys are not validated by Hermes)::

    core_patches:
      refuse_on_critical: false   # true: a critical patch that is not installed refuses startup (exit 78)
      max_failures: 3             # consecutive call-time failures before a patch is switched off
      apply_timeout_s: 5          # startup budget of apply_core_patches; it never blocks start longer
      notify: true                # one notice per new state to the home channel (gateway only)
      events_max_bytes: 262144    # events.jsonl rotates past this size
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger("hermes_cli.core_patches")

STATE_DIR_NAME = "core-patches"


@dataclass(frozen=True)
class Settings:
    refuse_on_critical: bool = False
    max_failures: int = 3
    replace_fallback: str = "bug-class"
    apply_timeout_s: float = 5.0
    notify: bool = True
    events_max_bytes: int = 262144


def _int_in(value: Any, low: int, high: int, default: int) -> int:
    ok = isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    return value if ok else default


def _choice_in(value: Any, choices: tuple, default: str) -> str:
    return value if isinstance(value, str) and value in choices else default


def _number_in(value: Any, low: float, high: float, default: float) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high
    return float(value) if ok else default


def parse_settings(section: Any) -> Settings:
    """``Settings`` from the ``core_patches:`` mapping; anything invalid keeps its default."""
    data: Mapping[str, Any] = section if isinstance(section, Mapping) else {}
    default = Settings()
    return Settings(
        refuse_on_critical=data.get("refuse_on_critical") is True,
        max_failures=_int_in(data.get("max_failures"), 1, 1000, default.max_failures),
        replace_fallback=_choice_in(data.get("replace_fallback"), ("always", "never", "bug-class"),
                                    default.replace_fallback),
        apply_timeout_s=_number_in(data.get("apply_timeout_s"), 0.05, 120.0, default.apply_timeout_s),
        notify=data.get("notify") is not False,
        events_max_bytes=_int_in(data.get("events_max_bytes"), 4096, 16 * 1024 * 1024, default.events_max_bytes),
    )


def load_settings() -> Settings:
    """The settings of the current Hermes home. Unreadable config means the defaults (never refuses)."""
    try:
        from hermes_cli.config import load_config_readonly

        return parse_settings((load_config_readonly() or {}).get("core_patches"))
    except Exception:  # health: allow BLE001 -- the policy must never depend on a readable config
        logger.debug("core patches: could not read core_patches settings; using the defaults", exc_info=True)
        return Settings()


def state_dir(home: Optional[Path] = None) -> Path:
    """``<hermes home>/core-patches`` (status.json, events.jsonl, notified.json)."""
    if home is None:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    return Path(home) / STATE_DIR_NAME


def safe_state_dir() -> Optional[Path]:
    try:
        return state_dir()
    except Exception:  # health: allow BLE001
        return None


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temp file and ``os.replace`` (readers never see half a file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
