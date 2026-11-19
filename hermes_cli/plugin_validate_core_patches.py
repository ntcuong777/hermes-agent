"""Admission lint: a catalog plugin may not core-patch (fork-owned).

``ctx.register_core_patch`` exists for the person's own plugins (loaded from ~/.hermes/plugins and
never admitted through the catalog). A catalog plugin that declares ``core_patches:`` in plugin.yaml
or calls ``register_core_patch`` anywhere in its runtime Python fails admission.

``hermes plugins validate`` is the catalog gate and has no admission flag of its own (the sibling
core-override rule is not gated at all). The signal that a plugin is personal is the one the
registry already trusts: the current home grants it (``plugins.entries.<id>.allow_core_patch:
true``). A granted plugin passes this rule with a note; the catalog CI runs in a throwaway home with
no grant, so a catalog candidate still fails.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable, Mapping

_SKIP_DIRS = frozenset({"tests", "test", "node_modules", ".git", "__pycache__", ".venv", "venv", "site-packages"})


def _python_files(plugin_dir: Path) -> Iterable[tuple[Path, Path]]:
    try:
        from hermes_cli.plugin_validate_core_override import _python_files as upstream

        return list(upstream(plugin_dir))
    except Exception:  # health: allow BLE001 -- an upstream rename must not break validation
        return [(p, p.relative_to(plugin_dir)) for p in sorted(plugin_dir.rglob("*.py"))
                if not _SKIP_DIRS.intersection(p.relative_to(plugin_dir).parts[:-1])
                and not p.name.startswith("test_") and p.name != "conftest.py"]


def _calls_register_core_patch(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError, ValueError):
        return False
    return any(isinstance(node, ast.Attribute) and node.attr == "register_core_patch" for node in ast.walk(tree))


def _plugin_key(manifest: Mapping[str, Any], plugin_dir: Path) -> str:
    return str(manifest.get("key") or manifest.get("name") or Path(plugin_dir).name)


def _granted(manifest: Mapping[str, Any], plugin_dir: Path) -> bool:
    try:
        from hermes_cli.core_patches import core_patch_granted

        names = {_plugin_key(manifest, plugin_dir), str(manifest.get("name") or ""), Path(plugin_dir).name}
        return any(core_patch_granted(name) for name in names if name)
    except Exception:  # health: allow BLE001 -- fail closed: not granted
        return False


def check_core_patches(report: Any, manifest: Mapping[str, Any], plugin_dir: Path) -> None:
    try:
        declared = "core_patches" in manifest
        hits = [str(rel) for path, rel in _python_files(Path(plugin_dir)) if _calls_register_core_patch(path)]
    except Exception as exc:  # health: allow BLE001
        report.add("core patches", False, f"could not inspect the plugin ({exc!r})")
        return
    if not declared and not hits:
        report.add("core patches", True, "no core patches")
        return
    if _granted(manifest, plugin_dir):
        report.add("core patches", True,
                   "uses core patches: allowed for this personal plugin (granted by plugins.entries.<id>."
                   "allow_core_patch in this home); a catalog plugin may not core-patch")
        return
    if declared:
        report.add("core patches", False,
                   "plugin.yaml declares core_patches: core patching is for personal plugins, not the catalog")
        return
    report.add("core patches", False,
               f"calls register_core_patch in {', '.join(hits)}: not allowed in catalog plugins")
