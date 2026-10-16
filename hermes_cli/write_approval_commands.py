#!/usr/bin/env python3
"""Shared handlers for the /memory and /skills write-approval subcommands."""

from __future__ import annotations

import json
from typing import List, Optional

from tools import write_approval as wa


def _fmt_state(subsystem: str) -> str:
    on = wa.write_approval_enabled(subsystem)
    return f"{subsystem}.write_approval = {'on' if on else 'off'}"


def _fmt_pending_list(subsystem: str) -> str:
    records = wa.list_pending(subsystem)
    if not records:
        return f"No pending {subsystem} writes."
    lines = [f"Pending {subsystem} writes ({len(records)}):"]
    for number, r in enumerate(records, 1):
        origin = r.get("origin", "foreground")
        tag = " [auto]" if origin == "background_review" else ""
        lines.append(f"  {number}. {r['id']}{tag}  {r.get('summary', '')}")
        if subsystem == wa.MEMORY:
            lines.extend(f"      {line}" for line in _matched_entries(r["payload"]))
    lines.append("")
    lines.append(f"Apply: /{subsystem} approve <n|id|all>   Reject: /{subsystem} reject <n|id|all>")
    if subsystem == wa.SKILLS:
        lines.append("Review full diff: /skills diff <n|id>")
    else:
        lines.append(f"Review one in full: /{subsystem} show <n|id>")
    return "\n".join(lines)


def handle_pending_subcommand(
    subsystem: str, args: list[str], *, memory_store=None, set_mode_fn=None) -> Optional[str]:
    """Dispatch a /memory or /skills write-approval subcommand.

    ``memory_store`` applies approved memory writes (CLI passes its live store; gateway a freshly
    loaded one); ``set_mode_fn`` persists the write_approval boolean. Returns text for the user,
    or None when the args are not a write-approval subcommand so the caller falls through to its
    other handling (e.g. /skills search).
    """
    if not args:
        return f"{_fmt_state(subsystem)}\n\n" + _fmt_pending_list(subsystem)
    sub, rest = args[0].lower(), args[1:]
    if sub == "pending":
        return _fmt_pending_list(subsystem)
    if sub in {"approve", "apply"}:
        return _approve(subsystem, rest, memory_store)
    if sub in {"reject", "deny", "drop"}:
        return _reject(subsystem, rest)
    if sub in {"show", "review"}:
        return _show(subsystem, rest)
    if sub == "diff" and subsystem == wa.SKILLS:
        return _show(subsystem, rest, verb="diff")
    if sub in {"approval", "mode"}:  # 'mode' kept as a back-compat alias
        return _set_approval(subsystem, rest, set_mode_fn)
    return None  # not ours — caller handles


def _which(subsystem: str, token: Optional[str], *, verb: str, allow_all: bool = True):
    """``(records, None)`` for what *token* names, else ``([], text for the user)``.

    *token* is ``all``, an exact id, or the number a row has in ``/<subsystem> pending`` (1 is the oldest). No
    token names the one pending write, and only when it is the only one: with several pending the person is
    asked which, never guessed for (an approval writes to durable memory).
    """
    records = wa.list_pending(subsystem)
    if not records:
        return [], f"No pending {subsystem} writes."
    if token is None:
        if len(records) == 1:
            return records, None
        choices = "<n|id|all>" if allow_all else "<n|id>"
        return [], (f"{len(records)} {subsystem} writes are pending: say which with /{subsystem} {verb} {choices}.\n\n"
                    + _fmt_pending_list(subsystem))
    if allow_all and token.lower() == "all":
        return list(records), None
    if rec := wa.get_pending(subsystem, token):
        return [rec], None
    if token.isdigit():
        if 1 <= int(token) <= len(records):
            return [records[int(token) - 1]], None
        return [], f"No pending {subsystem} write numbered {token}: choose 1-{len(records)}, or an id."
    return [], f"No pending {subsystem} write with id '{token}'."


def _approve(subsystem: str, rest: list[str], memory_store) -> str:
    targets, problem = _which(subsystem, rest[0] if rest else None, verb="approve")
    if problem is not None:
        return problem

    applied, failed, overwritten, patched, removed, done = 0, [], [], [], [], []
    for rec in targets:
        ok, msg, result = _apply_one(subsystem, rec, memory_store)
        if ok:
            wa.discard_pending(subsystem, rec["id"])
            applied += 1
            done.append(rec)
            overwritten.extend(f"  {rec['id']}: {text}" for text in _changed_entries(result, "replaced"))
            patched.extend(f"  {rec['id']}: {text}" for text in _changed_entries(result, "patched"))
            removed.extend(f"  {rec['id']}: {text}" for text in _changed_entries(result, "removed"))
        else:
            failed.append(f"{rec['id']}: {msg}")

    out = [f"Approved {applied} {subsystem} write(s)."]
    out.extend(f"  {rec['id']}  {rec.get('summary', '')}" for rec in done)
    if overwritten:
        # A memory 'replace' overwrites the WHOLE matched entry (#117952); the approver
        # is the last person who can notice a clause went missing, so show what was lost.
        out.append("Overwrote entire entry (re-add anything you still need):")
        out.extend(overwritten)
    if patched:
        # A patch keeps the rest of the entry, so show it as it was, not as lost text.
        out.append("Patched entry (as it was before the edit):")
        out.extend(patched)
    if removed:
        out.append("Removed entry (re-add anything you still need):")
        out.extend(removed)
    if failed:
        out.append("Failed:")
        out.extend(f"  {f}" for f in failed)
    return "\n".join(out)


def _changed_entries(result: dict, kind: str) -> list[str]:
    """Full text of every entry a memory write changed, single-op or batch shape."""
    single = result.get(f"{kind}_entry")
    if kind == "patched" and single is None:
        single = result.get("patched_entry_before")
    batch = result.get(f"{kind}_entries") or {}
    return ([single] if single else []) + [batch[k] for k in sorted(batch, key=int)]


def _matched_entries(payload) -> list[str]:
    """The full entry each staged memory replace/remove is pinned to: the summary shows only
    the old_text search string, and approval applies to this entry, not to that search."""
    from tools.memory_tool import destructive_ops
    return [f"{op['action']}s entry: {op['matched_entry']}" if op.get("matched_entry")
            else f"{op['action']}: unpinned legacy target \u2014 reject and recreate before approving"
            for op in destructive_ops(payload)]


def _apply_one(subsystem: str, rec, memory_store):
    """``(ok, error, result)`` — *result* is the applier's full payload (empty on exceptions)."""
    payload = rec.get("payload", {})
    try:
        if subsystem == wa.MEMORY:
            if memory_store is None:
                return False, "memory store unavailable", {}
            from tools.memory_tool import apply_memory_pending
            result = apply_memory_pending(payload, memory_store)
        else:
            from tools.skill_manager_tool import apply_skill_pending
            result = json.loads(apply_skill_pending(payload))
        return bool(result.get("success")), result.get("error", ""), result
    except Exception as e:
        return False, str(e), {}


def _reject(subsystem: str, rest: list[str]) -> str:
    targets, problem = _which(subsystem, rest[0] if rest else None, verb="reject")
    if problem is not None:
        return problem
    n = sum(1 for rec in targets if wa.discard_pending(subsystem, rec["id"]))
    if rest and rest[0].lower() == "all":
        return f"Rejected {n} pending {subsystem} write(s)."
    rec = targets[0]
    if not n:
        return f"No pending {subsystem} write with id '{rec['id']}'."
    summary = rec.get("summary")
    return f"Rejected pending {subsystem} write '{rec['id']}'." + (f"\n  {summary}" if summary else "")


def _show(subsystem: str, rest: list[str], verb: str = "show") -> str:
    """One pending write in full: the list only carries a one-line summary."""
    targets, problem = _which(subsystem, rest[0] if rest else None, verb=verb, allow_all=False)
    if problem is not None:
        return problem
    rec = targets[0]
    if subsystem == wa.SKILLS:
        return f"# Pending skill write {rec['id']}: {rec.get('summary', '')}\n\n" + wa.skill_pending_diff(rec)
    return _memory_review(rec)


def _memory_review(rec) -> str:
    """Everything a staged memory write would do: new text, what it finds, the entry it is pinned to."""
    from tools.memory_tool import destructive_ops
    payload = rec.get("payload") or {}
    ops = (payload.get("operations") or []) if payload.get("action") == "batch" else [payload]
    destructive = destructive_ops(payload)
    tag = " [auto]" if rec.get("origin") == "background_review" else ""
    lines = [f"# Pending memory write {rec['id']}{tag}", f"summary: {rec.get('summary', '')}",
             f"target: {payload.get('target')}"]
    for number, op in enumerate(ops, 1):
        lines.append(f"{number}. {op.get('action')}")
        if op.get("old_text"):
            lines.append(f"   finds: {op['old_text']}")
        if op.get("content"):
            lines.append(f"   new text: {op['content']}")
        if any(op is d for d in destructive):
            lines.append(f"   entry it applies to: {op['matched_entry']}" if op.get("matched_entry") else
                         "   entry it applies to: none pinned (a legacy write): reject and recreate before approving")
    lines.append("")
    lines.append(f"Apply: /memory approve {rec['id']}   Reject: /memory reject {rec['id']}")
    return "\n".join(lines)


_APPROVAL_VALUES = {
    **dict.fromkeys(("on", "true", "yes", "1", "enable", "enabled"), True),
    **dict.fromkeys(("off", "false", "no", "0", "disable", "disabled"), False)}


def _set_approval(subsystem: str, rest: list[str], set_mode_fn) -> str:
    """Turn the approval gate on/off for a subsystem."""
    if not rest:
        return (f"{_fmt_state(subsystem)}\n"
                f"Set with: /{subsystem} approval <on|off>")
    arg = rest[0].strip().lower()
    enabled = _APPROVAL_VALUES.get(arg)
    if enabled is None:
        return f"Invalid value '{arg}'. Use: on or off."
    if set_mode_fn is None:
        val = "true" if enabled else "false"
        return (f"To change the {subsystem} approval gate, run:\n"
                f"  hermes config set {subsystem}.write_approval {val}")
    try:
        set_mode_fn(enabled)
    except Exception as e:
        return f"Failed to set {subsystem}.write_approval: {e}"
    return f"{subsystem}.write_approval set to '{'on' if enabled else 'off'}'."
