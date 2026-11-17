"""``ctx.register_core_patch``: the one PluginContext method of the core-patch registry (fork-owned).

Mixed into :class:`hermes_cli.plugins_context.PluginContext`. The registrar name starts with
``register_`` so ``ignore_registrars_after_abandoned_load`` guards it like every other registrar.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger("hermes_cli.core_patches")


class CorePatchCtxMixin:
    """Needs ``manifest``, ``_manager`` (``scope_key``, ``home_path``), ``plugin_id``, ``_track``, ``_staged``."""

    def register_core_patch(
        self, name: str, *, target: str, kind: str, fn: Callable[..., Any], expect_ast_sha: str,
        order: int = 0, critical: bool = False, propagates: Iterable[type] = (),
    ) -> Optional[Any]:
        """Wrap (``kind="wrap"``: ``fn(original) -> new callable``) or replace (``kind="replace"``:
        ``fn`` is the new callable) ``target`` (``"pkg.mod:function"`` or ``"pkg.mod:Class.method"``).

        Installed when this plugin's load succeeds, only if ``name`` is listed under
        ``core_patches:`` in plugin.yaml, ``plugins.entries.<id>.allow_core_patch`` is ``true``, and
        the target's AST fingerprint equals ``expect_ast_sha`` (``hermes plugins patches --sha``).
        Otherwise it is recorded as skipped-* and the core stays untouched. A targeted unload undoes
        it; a forced reload keeps it installed until the reloaded plugin's registration replaces it.
        ``critical=True`` (or ``{name, critical: true}`` in plugin.yaml): a patch that is not active is
        reported loudly (ERROR log, notice, doctor finding); it refuses startup only with
        ``core_patches.refuse_on_critical: true``. ``propagates``: exception types the patch raises on
        purpose (the safety shell lets them through instead of treating them as a patch fault).
        Fail-safe: invalid arguments never raise out of ``register()``; the patch is recorded as
        ``skipped-error`` (the plugin's other registrations are unaffected) and None is returned.
        Nothing is imported here: the target is resolved when the load is published (or by
        ``apply_core_patches``).
        """
        from hermes_cli import core_patches as cp

        try:
            return self._register_core_patch(cp, name, target, kind, fn, expect_ast_sha, order, critical, propagates)
        except Exception as exc:  # health: allow BLE001 -- a bad patch registration must not fail the plugin
            logger.warning("core patch %s:%s was not registered: %s", self.plugin_id, name, exc)
            cp.record_error(self.plugin_id, name, target, kind, f"not registered: {exc}", critical=critical is True)
            return None

    def _register_core_patch(self, cp: Any, name: Any, target: Any, kind: Any, fn: Any, expect_ast_sha: Any,
                             order: Any, critical: Any, propagates: Any) -> Optional[Any]:
        spec = cp.make_spec(
            plugin_id=self.plugin_id, name=name, target=target, kind=kind, fn=fn,
            expect_ast_sha=expect_ast_sha, order=order, critical=critical,
            plugin_dir=getattr(self.manifest, "path", None), propagates=propagates,
        )
        manager, plugin_key, owner, seq = self._manager, self.plugin_id, str(self._manager.scope_key), spec.seq
        key = cp.stage(
            spec, owner,
            granted=cp.core_patch_granted(self.plugin_id, manager.home_path),
            declared=name in cp.declared_patch_names(spec.plugin_dir),
            declared_critical=name in cp.declared_critical_names(spec.plugin_dir),
            manager=manager,
        )
        # Persistent: an unload-all (force re-discovery) parks it, so the installed chain stays live
        # while the plugin reloads; undone by a targeted unload or when the plugin does not come back.
        registration = self._track(
            "core_patch", key, lambda: cp.release(key, owner, seq, failed=_load_error(manager, plugin_key)),
            persistent=True)
        registration.publish = lambda: cp.activate(key, owner, seq)
        # Ordinary: disposed by a failed or timed-out load before publish (the tombstone signal).
        self._track("core_patch_load", key, lambda: cp.abort(key, owner, seq, _load_error(manager, plugin_key)))
        self._staged.append(registration)
        return registration


def _load_error(manager: Any, plugin_key: str) -> Optional[str]:
    """The plugin's load error when it was really loaded and failed (not disabled in config)."""
    from hermes_cli import core_patches as cp

    loaded = getattr(manager, "_plugins", {}).get(plugin_key)
    error = getattr(loaded, "error", None)
    return error if error and cp.load_attempted(manager, loaded) else None
