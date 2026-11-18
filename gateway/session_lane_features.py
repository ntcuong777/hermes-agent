"""Which session-lane features this fork has removed from upstream-owned core files (fork-owned).

The `lane-mirror` plugin (nix-darwin repo, hermes/plugins/lane-mirror) carries the code of these lane
features and enables each one only when its id is listed here. A core that still has a feature's code in
core lists nothing (or lacks this module), so the plugin never applies a feature twice. Every stage of the
lane-mirror project that removes a feature's core code adds its id in the SAME commit as the removal.

Ids: "repaint" (stage B1), "rescue" and "gates" (B3), "forward" (B4), "pills" (B5), "model" (B6),
"wake" (B7).

Spec 08b (the personal features that are not lanes): "workspaces" (B9), "workspace-seed" (B10), "workspace-slash" (B11),
"settle" (B12), "wake" (B13, replaces the id of spec 08 B7), "residue" (B14), "p27" and "diagnostics" (B15).
"""

REMOVED_FROM_CORE: frozenset = frozenset({"diagnostics", "forward", "gates", "model", "p27", "pills", "repaint", "rescue", "residue", "settle", "wake", "workspace-seed", "workspace-slash", "workspaces"})
