#!/usr/bin/env bash
# Fork-only merge setup. Run once per clone, and by upstream-sync.yml, before
# `git merge upstream/main`.
#
#   rerere        remembers how you resolved a conflict and replays it the next
#                 time the same conflict appears (a long-lived fork hits the same
#                 hunks on every sync).
#   fork-regen    a merge driver for the two generated gateway-contract files.
#                 They are a pure function of tui_gateway/contracts, so a textual
#                 merge of them is meaningless: the driver keeps this side's copy
#                 and succeeds, and scripts/fork/sync-finish.sh regenerates them
#                 from the merged Python after the merge. (Upstream changes them
#                 in most syncs; before this they conflicted in most of ours.)
#
# Local-only state: driver config lives in .git/config and the attribute lines in
# .git/info/attributes, so nothing here touches upstream's .gitattributes.
# Idempotent.
set -euo pipefail

git config rerere.enabled true
git config merge.fork-regen.name "keep ours, regenerate after the merge"
git config merge.fork-regen.driver true

attrs="$(git rev-parse --git-path info/attributes)"
mkdir -p "$(dirname "$attrs")"
touch "$attrs"
for path in \
  apps/shared/src/gateway-contract.generated.ts \
  apps/shared/src/gateway-contract.openrpc.json; do
  line="$path merge=fork-regen"
  grep -qxF "$line" "$attrs" || printf '%s\n' "$line" >> "$attrs"
done
