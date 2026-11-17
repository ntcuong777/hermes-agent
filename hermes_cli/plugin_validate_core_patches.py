"""Admission lint: a catalog plugin may not core-patch (fork-owned).

``ctx.register_core_patch`` exists for the person's own plugins (loaded from ~/.hermes/plugins and
never admitted through the catalog). A catalog plugin that declares ``core_patches:`` in plugin.yaml
or calls ``register_core_patch`` anywhere in its runtime Python fails admission.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Mapping

from hermes_cli.plugin_validate_core_override import _python_files


def _calls_register_core_patch(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError, ValueError):
        return False
    return any(isinstance(node, ast.Attribute) and node.attr == "register_core_patch" for node in ast.walk(tree))


def check_core_patches(report: Any, manifest: Mapping[str, Any], plugin_dir: Path) -> None:
    if "core_patches" in manifest:
        report.add("core patches", False,
                   "plugin.yaml declares core_patches: core patching is for personal plugins, not the catalog")
        return
    hits = [str(rel) for path, rel in _python_files(Path(plugin_dir)) if _calls_register_core_patch(path)]
    if hits:
        report.add("core patches", False,
                   f"calls register_core_patch in {', '.join(hits)}: not allowed in catalog plugins")
        return
    report.add("core patches", True, "no core patches")
