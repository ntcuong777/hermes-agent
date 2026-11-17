#!/usr/bin/env bash
# Markdown report of the core patches against this checkout, for a PR or issue body. NEVER fails: it
# always exits 0 and says what it could not do. Use it after a hand merge of upstream, and from
# upstream-sync.yml (which puts the output in the sync PR or the alert issue).
#
#   sync-report.sh [--plugins-root DIR] [--pins FILE] [REPO_DIR]
#
#   --plugins-root DIR  report from the plugin locks under DIR (nix-darwin: <repo>/hermes/plugins): table,
#                       and for each drifted patch the function-level diff recorded -> current
#   --pins FILE         report from the fork-side pins (default REPO_DIR/.github/core-patch-pins.json):
#                       table, and for each drifted pin the upstream commits that touched the file
#                       since the recorded one (the fork's CI cannot see the plugins)
#
# Uses $HERMES_PYTHON, else python3 (only the standard library is needed).
set -u

plugins_root=""
pins=""
repo=""
while [ $# -gt 0 ]; do
  case "$1" in
    --plugins-root) plugins_root="${2:-}"; shift ;;
    --pins) pins="${2:-}"; shift ;;
    --*) ;;
    *) repo="$1" ;;
  esac
  shift
done
[ -n "$repo" ] || repo="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
py="${HERMES_PYTHON:-python3}"
ast_py="$repo/hermes_cli/core_patches_ast.py"
[ -n "$pins" ] || pins="$repo/.github/core-patch-pins.json"

echo "# Core patch report"
echo
ref="$(bash "$repo/scripts/fork/upstream-ref.sh" --json "$repo" 2>/dev/null || true)"
echo "Upstream merged into this checkout: \`${ref:-unknown}\`"
echo
stamp="$(cat "$repo/hermes-upstream-commit" 2>/dev/null | tr -d '[:space:]' || true)"
commit="$(bash "$repo/scripts/fork/upstream-ref.sh" "$repo" 2>/dev/null || true)"
if [ -n "$stamp" ] && [ -n "$commit" ] && [ "$commit" != "unknown" ] && [ "$stamp" != "$commit" ]; then
  echo "> hermes-upstream-commit is stale (\`$stamp\`, expected \`$commit\`): run \`scripts/fork/upstream-ref.sh --stamp\` and commit it."
  echo
fi

if [ ! -f "$ast_py" ]; then
  echo "This checkout has no hermes_cli/core_patches_ast.py: no core-patch registry, nothing to report."
  exit 0
fi

if [ -n "$plugins_root" ]; then
  "$py" -I "$ast_py" --report-locks "$plugins_root" --source-root "$repo" 2>&1 \
    || echo "(the lock report failed: $?)"
else
  if [ -f "$pins" ]; then
    "$py" -I "$ast_py" --report-pins "$pins" --source-root "$repo" 2>&1 || echo "(the pins report failed: $?)"
  else
    echo "No pins file at \`$pins\`; pass --plugins-root to report from the plugin locks."
  fi
fi
exit 0
