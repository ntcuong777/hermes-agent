"""config.yaml readers for the Discord adapter: DM role-auth guild opt-in, button-view timeout and
exec-approval admin gate.

Re-exported from ``plugins.platforms.discord.adapter`` (callers and tests import them there).
"""
from typing import Any, Optional

from agent.deadline import MAX_SAFE_TIMEOUT_S


def _read_dm_role_auth_guild() -> Optional[int]:
    """Return the guild ID opted-in for DM role-based auth, or None (secure default). Read from
    config.yaml ``discord.dm_role_auth_guild`` only (behavioral, not a secret); int or numeric string."""
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config() or {}
        discord_cfg = cfg.get("discord", {}) or {}
        raw = discord_cfg.get("dm_role_auth_guild")
    except Exception:
        return None
    if raw is None or raw == "":
        return None
    try:
        guild_id = int(raw)
    except (TypeError, ValueError):
        return None
    return guild_id if guild_id > 0 else None


# All Discord prompt views share this setting; command approval waits have their own timeout.
_DISCORD_PROMPT_TIMEOUT_DEFAULT = 1800
_DISCORD_PROMPT_TIMEOUT_MIN = 30
_DISCORD_PROMPT_TIMEOUT_MAX = int(MAX_SAFE_TIMEOUT_S)


def _read_discord_prompt_timeout() -> int:
    """Timeout (seconds) for Discord button views from ``approvals.discord_prompt_timeout``
    (default 1800), clamped to [MIN, MAX] to keep their UI lifetime bounded."""
    raw: Any = None
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config() or {}
        approvals_cfg = cfg.get("approvals", {}) or {}
        raw = approvals_cfg.get("discord_prompt_timeout")
    except Exception:
        return _DISCORD_PROMPT_TIMEOUT_DEFAULT
    if raw is None or raw == "":
        return _DISCORD_PROMPT_TIMEOUT_DEFAULT
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return _DISCORD_PROMPT_TIMEOUT_DEFAULT
    if seconds < _DISCORD_PROMPT_TIMEOUT_MIN:
        return _DISCORD_PROMPT_TIMEOUT_MIN
    if seconds > _DISCORD_PROMPT_TIMEOUT_MAX:
        return _DISCORD_PROMPT_TIMEOUT_MAX
    return seconds


def _read_exec_approval_view_timeout() -> int:
    """Lifetime (seconds) of an exec-approval card's buttons: the larger of the prompt-view timeout and
    ``approvals.timeout``. The card answers a wait that lasts ``approvals.timeout``; a view that died
    earlier left an expired-looking card (and a click on stale buttons that nothing answers) while the
    agent was still blocked. The result is clamped to [MIN, MAX] like the prompt timeout."""
    try:
        from tools.approval_context import _get_approval_timeout
        approval_s = int(_get_approval_timeout())
    except Exception:
        approval_s = 0
    return min(max(_read_discord_prompt_timeout(), approval_s), _DISCORD_PROMPT_TIMEOUT_MAX)


def _resolve_exec_approval_admin_gate(config_extra: Optional[dict]) -> tuple[bool, set]:
    """Resolve the exec-approval admin gate from ``extra``; returns ``(require_admin, admin_user_ids)``.
    Default OFF (user-scope buttons). When ``require_admin_for_exec_approval`` is true only
    ``allow_admin_from`` ids may click; on with no admins -> ``(True, set())`` (fail closed, log once).
    """
    extra = config_extra if isinstance(config_extra, dict) else {}
    raw_toggle = extra.get("require_admin_for_exec_approval", False)
    require_admin = str(raw_toggle).strip().lower() in {"true", "1", "yes"}
    if not require_admin:
        return (False, set())
    try:
        from gateway.slash_access import _coerce_id_list
        admin_ids = set(_coerce_id_list(extra.get("allow_admin_from")))
    except Exception:
        admin_ids = set()
    return (True, admin_ids)
