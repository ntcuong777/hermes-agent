#!/usr/bin/env bash
# The latest NousResearch/hermes-agent commit this fork has merged. NEVER fails: prints `unknown`
# (and exits 0) when it cannot tell, so a provenance record or a CI step never breaks on it.
#
#   upstream-ref.sh [--json] [--stamp] [REPO_DIR]
#
#   (default)  the 40-hex sha, or `unknown`
#   --json     {"commit": "...", "release": "...", "date": "YYYY-MM-DD", "via": "..."} on one line
#              release = the content of hermes-upstream-release, date = the commit's date
#   --stamp    also write the sha to REPO_DIR/hermes-upstream-commit (the stamp file a Nix store build
#              reads, because it has no git history); upstream-sync.yml runs it after each merge,
#              run it yourself after a hand merge and commit the file
#
# How the answer is found, newest evidence wins:
#   A. `git merge-base HEAD <tip>` for the first of upstream/main, origin/main, main that exists
#      (in this fork `main` mirrors upstream; the sync workflow fetches `upstream`).
#   B. the second parent of the latest merge on HEAD's first-parent line whose subject is one of the
#      fork's conventions: "Merge upstream main into personal" (upstream-sync.yml),
#      "Merge branch 'main' into personal", "Merge branch 'NousResearch:main' into personal"
#      (GitHub sync / hand merges), "Merge remote-tracking branch 'origin/main'", "Merge origin/main ...",
#      "Merge upstream/main ..."; and "Merge pull request #N from <owner>/sync/upstream" (the sync PR:
#      its branch tip is followed back to the "Merge upstream main into personal" commit).
#   If both exist the descendant wins. C. the hermes-upstream-commit stamp file. D. `unknown`.
set -u

mode=plain
stamp=0
repo=""
for arg in "$@"; do
  case "$arg" in
    --json) mode=json ;;
    --stamp) stamp=1 ;;
    --*) ;;
    *) repo="$arg" ;;
  esac
done
[ -n "$repo" ] || repo="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

g() { git -C "$repo" "$@" 2>/dev/null; }
is_sha() { [[ "${1:-}" =~ ^[0-9a-f]{40}$ ]]; }

a=""
a_via=""
for ref in upstream/main origin/main main; do
  if g rev-parse --verify --quiet "refs/remotes/$ref^{commit}" >/dev/null \
     || g rev-parse --verify --quiet "refs/heads/$ref^{commit}" >/dev/null; then
    mb="$(g merge-base HEAD "$ref")"
    if is_sha "$mb"; then a="$mb"; a_via="merge-base:$ref"; break; fi
  fi
done

b=""
while IFS=$'\t' read -r parents subject; do
  [ -n "$parents" ] || continue
  # shellcheck disable=SC2086
  set -- $parents
  second="${3:-}"
  is_sha "$second" || continue
  case "$subject" in
    "Merge upstream main into personal"*|"Merge branch 'main'"*|"Merge branch 'NousResearch:main'"*|\
    "Merge remote-tracking branch 'origin/main'"*|"Merge remote-tracking branch 'upstream/main'"*|\
    "Merge origin/main"*|"Merge upstream/main"*)
      b="$second"; break ;;
    "Merge pull request #"*"/sync/upstream"*)
      sync_merge="$(g rev-list --first-parent --merges --grep='^Merge upstream main into personal' -n 1 "$second")"
      if [ -n "$sync_merge" ]; then
        candidate="$(g rev-parse "$sync_merge^2")"
        if is_sha "$candidate"; then b="$candidate"; break; fi
      fi ;;
  esac
done < <(g log --first-parent --merges --format='%H %P%x09%s' HEAD)

commit=""
via="unknown"
if [ -n "$a" ] && [ -n "$b" ]; then
  if g merge-base --is-ancestor "$b" "$a"; then commit="$a"; via="$a_via"; else commit="$b"; via="merge-subject"; fi
elif [ -n "$b" ]; then commit="$b"; via="merge-subject"
elif [ -n "$a" ]; then commit="$a"; via="$a_via"
fi
if ! is_sha "$commit"; then
  stamped="$(cat "$repo/hermes-upstream-commit" 2>/dev/null | tr -d '[:space:]' || true)"
  if is_sha "$stamped"; then commit="$stamped"; via="stamp-file"; else commit="unknown"; via="unknown"; fi
fi

release="$(cat "$repo/hermes-upstream-release" 2>/dev/null | tr -d '[:space:]' || true)"
[ -n "$release" ] || release="unknown"
date="unknown"
if [ "$commit" != "unknown" ]; then
  date="$(g show -s --format=%cs "$commit")"
  [ -n "$date" ] || date="unknown"
fi

if [ "$stamp" = 1 ] && [ "$commit" != "unknown" ]; then
  printf '%s\n' "$commit" > "$repo/hermes-upstream-commit" 2>/dev/null \
    || echo "upstream-ref: could not write $repo/hermes-upstream-commit" >&2
fi

if [ "$mode" = json ]; then
  printf '{"commit": "%s", "release": "%s", "date": "%s", "via": "%s"}\n' "$commit" "$release" "$date" "$via"
else
  printf '%s\n' "$commit"
fi
exit 0
