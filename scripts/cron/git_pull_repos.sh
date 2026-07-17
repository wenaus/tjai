#!/bin/bash
# Pull latest for every repo in ~/github, every 30 minutes.
# With arg "swf-only": pull only the swf-* repos (cron runs this every 5
# minutes — this machine collaborates with live swf development and must
# not operate on stale swf software).
# swf-* repos: checkout and pull the highest infra/baseline-vNN branch.
# All other repos: pull the current branch.
# All pulls are --ff-only (never merges or clobbers); a 3s sleep spaces them
# so a run does not fire ~25 fetches at GitHub at once. Cron wraps both
# entries in flock so the two cadences never run concurrently.

set -uo pipefail

GITHUB_DIR=/home/admin/github
SWF_ONLY=0
[ "${1:-}" = "swf-only" ] && SWF_ONLY=1

SWF_REPOS=(
    "$GITHUB_DIR/swf-testbed"
    "$GITHUB_DIR/swf-monitor"
    "$GITHUB_DIR/swf-common-lib"
    "$GITHUB_DIR/swf-remote"
    "$GITHUB_DIR/swf-epicprod"
    "$GITHUB_DIR/snapper-ai"
)

is_swf() {
    local r="$1" s
    for s in "${SWF_REPOS[@]}"; do
        [ "$r" = "$s" ] && return 0
    done
    return 1
}

# Every non-swf git repo under ~/github: pull current branch.
[ "$SWF_ONLY" = 1 ] || for dir in "$GITHUB_DIR"/*/; do
    repo="${dir%/}"
    [ -d "$repo/.git" ] || continue
    is_swf "$repo" && continue
    git -C "$repo" pull --ff-only --quiet 2>&1 || echo "WARN: pull failed for $repo"
    sleep 3
done

# swf-* repos: track the highest infra/baseline-vNN branch.
for repo in "${SWF_REPOS[@]}"; do
    [ -d "$repo/.git" ] || continue
    git -C "$repo" fetch --quiet 2>&1 || { echo "WARN: fetch failed for $repo"; continue; }

    highest=$(git -C "$repo" branch -r 2>/dev/null \
        | grep -oP 'origin/infra/baseline-v\K\d+' \
        | sort -n | tail -1)

    if [ -z "$highest" ]; then
        git -C "$repo" pull --ff-only --quiet 2>&1 || echo "WARN: pull failed for $repo"
        sleep 3
        continue
    fi

    target="infra/baseline-v${highest}"
    current=$(git -C "$repo" branch --show-current 2>/dev/null)

    if [ "$current" != "$target" ]; then
        git -C "$repo" checkout "$target" --quiet 2>&1 || { echo "WARN: checkout $target failed for $repo"; continue; }
    fi

    git -C "$repo" pull --ff-only --quiet 2>&1 || echo "WARN: pull failed for $repo ($target)"
    sleep 3
done
