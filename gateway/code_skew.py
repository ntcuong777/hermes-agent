"""Detect when the gateway is running stale code after a hot ``git pull``.

The gateway's ``sys.modules`` is frozen at boot.  If the checkout is updated
underneath it, a first-time lazy import can resolve a freshly-pulled module
against a stale cached dependency -> ImportError.  We snapshot the revision at
startup so risky callers (e.g. ``/model`` switching) can refuse with a clear
"restart the gateway" message.  If the revision can't be read (non-git install,
IO error) the boot snapshot stays ``None`` and detection no-ops — never a false positive.
"""

from __future__ import annotations

from pathlib import Path

from hermes_cli._startup_fast import project_root_str

_PROJECT_ROOT = Path(project_root_str())
_boot_fingerprint: str | None = None


def _fingerprint() -> str | None:
    """Current checkout fingerprint via the CLI's worktree-aware git-rev reader
    (``hermes_cli.main`` is always already imported in a gateway process)."""
    try:
        from hermes_cli.main import _read_git_revision_fingerprint

        return _read_git_revision_fingerprint(_PROJECT_ROOT)
    except Exception:
        return None


def record_boot_fingerprint() -> None:
    """Snapshot the checkout revision at gateway startup (idempotent)."""
    global _boot_fingerprint
    if _boot_fingerprint is None:
        _boot_fingerprint = _fingerprint()


def _short(fingerprint: str) -> str:
    """Render a ``git:<ref>:<sha>`` fingerprint as a compact label."""
    sha = fingerprint.rsplit(":", 1)[-1]
    return sha[:10] if sha and sha != "unresolved" and len(sha) > 10 else (sha or fingerprint)


def current_code_sha() -> str | None:
    """Full SHA for the checkout currently on disk, or None when unresolved."""
    fingerprint = _fingerprint()
    if fingerprint is None:
        return None
    sha = fingerprint.rsplit(":", 1)[-1]
    return sha if sha and sha != "unresolved" else None


def detect_code_skew() -> tuple[str, str] | None:
    """``(boot_rev, disk_rev)`` short labels if the checkout drifted since boot, else ``None``."""
    current = _fingerprint() if _boot_fingerprint is not None else None
    if current is None or current == _boot_fingerprint:
        return None
    return _short(_boot_fingerprint), _short(current)


# ``gateway.model_switch_skew_guard``: what a model switch does when the checkout moved since boot.
#   refuse (default): the switch fails with "restart the gateway" (the original behaviour);
#   warn: the switch is performed and the reply carries a one-line "restart when convenient" note;
#   off: the switch is performed with no note.
MODEL_SWITCH_SKEW_MODES = ("refuse", "warn", "off")
DEFAULT_MODEL_SWITCH_SKEW_MODE = "refuse"


def model_switch_skew_mode(config: dict | None = None) -> str:
    """The configured ``gateway.model_switch_skew_guard``: ``refuse`` | ``warn`` | ``off``.

    Read fresh on every call (a config edit applies without a restart). Anything unreadable or unknown is
    ``refuse``, today's behaviour; YAML's unquoted ``off`` (a boolean ``False``) means ``off``. *config* is
    the effective user config; omitted, the gateway's own is read."""
    try:
        if config is None:
            from gateway.run import _load_gateway_config

            config = _load_gateway_config()
        section = config.get("gateway") if isinstance(config, dict) else None
        raw = section.get("model_switch_skew_guard") if isinstance(section, dict) else None
    except Exception:
        return DEFAULT_MODEL_SWITCH_SKEW_MODE
    if raw is False:
        return "off"
    mode = str(raw).strip().lower() if raw is not None else DEFAULT_MODEL_SWITCH_SKEW_MODE
    return mode if mode in MODEL_SWITCH_SKEW_MODES else DEFAULT_MODEL_SWITCH_SKEW_MODE


def model_switch_skew(config: dict | None = None) -> tuple[str, tuple[str, str] | None]:
    """``(mode, skew)``: the configured mode and ``detect_code_skew()``'s answer (None unless it drifted).

    The single read every model-switch caller shares (gateway ``/model``, a viewer's session pick, the TUI
    and dashboard model RPCs); a caller acts on ``mode`` only when ``skew`` is set."""
    skew = detect_code_skew()
    return (model_switch_skew_mode(config) if skew else DEFAULT_MODEL_SWITCH_SKEW_MODE), skew
