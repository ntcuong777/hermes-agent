"""Which session-lane features this fork has removed from upstream-owned core files (fork-owned).

The `lane-mirror` plugin (nix-darwin repo, hermes/plugins/lane-mirror) carries the code of these lane
features and enables each one only when its id is listed here. A core that still has a feature's code in
core lists nothing (or lacks this module), so the plugin never applies a feature twice. Every stage of the
lane-mirror project that removes a feature's core code adds its id in the SAME commit as the removal.

Ids: "repaint" (stage B1), "rescue" and "gates" (B3), "forward" (B4), "pills" (B5), "model" (B6),
"wake" (B7).
"""

REMOVED_FROM_CORE: frozenset = frozenset({"repaint"})
