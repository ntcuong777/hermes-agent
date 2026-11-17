"""``ctx.register_core_patch``: the one PluginContext method of the core-patch registry (fork-owned).

Mixed into :class:`hermes_cli.plugins_context.PluginContext`. The registrar name starts with
``register_`` so ``ignore_registrars_after_abandoned_load`` guards it like every other registrar.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


class CorePatchCtxMixin:
    """Needs ``manifest``, ``_manager`` (``scope_key``, ``home_path``), ``plugin_id``, ``_track``, ``_staged``."""

    def register_core_patch(
        self, name: str, *, target: str, kind: str, fn: Callable[..., Any], expect_ast_sha: str,
        order: int = 0, critical: bool = False,
    ) -> Optional[Any]:
        """Wrap (``kind="wrap"``: ``fn(original) -> new callable``) or replace (``kind="replace"``:
        ``fn`` is the new callable) ``target`` (``"pkg.mod:function"`` or ``"pkg.mod:Class.method"``).

        Installed when this plugin's load succeeds, only if ``name`` is listed under
        ``core_patches:`` in plugin.yaml, ``plugins.entries.<id>.allow_core_patch`` is ``true``, and
        the target's AST fingerprint equals ``expect_ast_sha`` (``hermes plugins patches --sha``).
        Otherwise it is recorded as skipped-* and the core stays untouched. Unload undoes it.
        ``critical=True``: a drifted or broken patch refuses process startup. Invalid arguments raise.
        """
        from hermes_cli import core_patches as cp

        spec = cp.make_spec(
            plugin_id=self.plugin_id, name=name, target=target, kind=kind, fn=fn,
            expect_ast_sha=expect_ast_sha, order=order, critical=critical,
            plugin_dir=getattr(self.manifest, "path", None),
        )
        owner = str(self._manager.scope_key)
        key = cp.stage(
            spec, owner,
            granted=cp.core_patch_granted(self.plugin_id, self._manager.home_path),
            declared=name in cp.declared_patch_names(spec.plugin_dir),
        )
        registration = self._track("core_patch", key, lambda: cp.release(key, owner))
        registration.publish = lambda: cp.activate(key, owner)
        self._staged.append(registration)
        return registration
