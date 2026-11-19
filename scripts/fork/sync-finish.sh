#!/usr/bin/env bash
# Run after a clean `git merge upstream/main` (sync-setup.sh set the merge up):
# regenerate the gateway-contract files from the merged tui_gateway/contracts and
# commit them if the merge left them stale. Does nothing when they are current.
#
# Uses $HERMES_PYTHON, else .venv/bin/python, else python3.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
generated=(
  apps/shared/src/gateway-contract.generated.ts
  apps/shared/src/gateway-contract.openrpc.json
)

py="${HERMES_PYTHON:-}"
if [ -z "$py" ]; then
  if [ -x .venv/bin/python ]; then py=.venv/bin/python; else py=python3; fi
fi

"$py" scripts/gen_gateway_contracts.py

if git diff --quiet -- "${generated[@]}"; then
  echo "gateway contract already current"
else
  git add -- "${generated[@]}"
  git commit -q --no-verify -m "chore: regenerate gateway contract after upstream merge"
  echo "gateway contract regenerated and committed"
fi
