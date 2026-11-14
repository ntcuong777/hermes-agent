"""Model-id helpers split out of ``hermes_cli.model_switch``: the effective-model precedence rule
shared by the gateway config loaders and the API server, and the version-suffix-aware sort key the
``/model`` picker uses to order candidates best-guess-first."""

from __future__ import annotations

from typing import Any


def _effective_model_candidate(value: Any) -> str:
    """Extract a model-name candidate from a str / dict / attr-object."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        from hermes_cli.model_switch import _clean
        return _clean(value.get("model"))
    model_attr = getattr(value, "model", None)
    return _clean(model_attr) if model_attr is not None else ""


def resolve_effective_model(
    session_overrides: Any = None, channel_config: Any = None, global_config: Any = "") -> str:
    """Resolve the effective model: session override > channel > global.

    Single owner of the precedence rule gateway/run.py and api_server.py each used to encode.
    Each argument may be a model string, a dict with a ``"model"`` key, or an object with a
    ``.model`` attribute; empty entries fall through to the next tier."""
    for tier in (session_overrides, channel_config, global_config):
        candidate = _effective_model_candidate(tier)
        if candidate:
            return candidate
    return ""


def _model_sort_key(model_id: str, prefix: str) -> tuple:
    """Sort key preferring higher versions after the family prefix, then ranked suffix tokens.

    With prefix ``"mimo"``: ``mimo-v2.5-pro`` -> (-2.5, 0, 'pro'), ``mimo-v2.5`` -> (-2.5, 1, ''),
    ``mimo-v2-omni`` -> (-2.0, 1, 'omni')."""
    # Strip the prefix (and optional "/" separator for aggregator slugs)
    rest = model_id[len(prefix):].removeprefix("/").lstrip("-").strip()
    nums, suffix_buf = _split_version_suffix(rest)
    suffix = suffix_buf.lower().strip("-_.").strip()

    # YYYYMMDD date stamps (claude-opus-4-20250514) are snapshot markers, not version components,
    # and would dwarf real point versions; keep them as a trailing tiebreaker so bare IDs sort
    # before their dated snapshots and newer snapshots before older. The 19_000_101 threshold
    # reclassifies only 8-digit stamps (mistral-large-2411, gpt-4-0613 keep sorting as versions).
    version_key = tuple(-n for n in nums if n < 19_000_101)  # negate: higher sorts first
    date_stamp = max((n for n in nums if n >= 19_000_101), default=0.0)
    date_key = (0.0, 0.0) if date_stamp == 0.0 else (1.0, -date_stamp)

    # Suffix quality: pro/max/plus/turbo (0) > no suffix / omni / flash / mini (1). "sol" is the
    # flagship tier of the GPT-5.6 series (sol > terra > luna); without it `/model gpt` would
    # tiebreak alphabetically onto luna, the cheapest. GPT-6 put "astra" above "sol": both rank 0 and
    # the alphabetical tiebreak lands on astra, so `/model gpt` still resolves to the flagship.
    suffix_rank = 0 if suffix in ("pro", "max", "plus", "turbo", "sol", "astra") else 1
    return version_key + (suffix_rank, suffix) + date_key


def _split_version_suffix(rest: str) -> tuple[list[float], str]:
    """``"v2.5-pro"`` -> ``([2.5], "pro")``; ``"-omni"`` -> ``([], "omni")``.

    Version tokens are ``v``-optional digit/dot runs separated by ``-``/``_``; a second dot inside
    a run starts a new component; the first character that is neither starts the suffix."""
    nums: list[float] = []
    run, pos = "", 0

    def _flush() -> None:
        nonlocal run
        try:
            nums.append(float(run.rstrip(".")))
        except ValueError:
            pass
        run = ""

    while pos < len(rest):
        ch = rest[pos]
        if ch in "-_.":
            pos += 1
            continue
        if not (ch in "vV" or ch.isdigit()):
            break
        if ch in "vV":
            pos += 1
        while pos < len(rest) and (rest[pos].isdigit() or rest[pos] == "."):
            if rest[pos] == "." and "." in run:
                _flush()
            else:
                run += rest[pos]
            pos += 1
        _flush()
        if pos < len(rest) and rest[pos] not in "-_":
            break
    return nums, rest[pos:]
