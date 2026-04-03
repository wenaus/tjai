#!/bin/bash
# Pull latest for all tracked repos every 10 minutes.
# For swf-* repos: checkout and pull the highest infra/baseline-vNN branch.
# For others: pull current branch.

set -uo pipefail

REPOS=(
    /home/admin/github/tjrepo
    /home/admin/github/BNLNPPS.github.io
    /home/admin/github/lxr-mcp-server
    /home/admin/github/rucio-eic-mcp-server
    /home/admin/github/xrootd-mcp-server
)

SWF_REPOS=(
    /home/admin/github/swf-testbed
    /home/admin/github/swf-monitor
    /home/admin/github/swf-common-lib
    /home/admin/github/swf-remote
)

for repo in "${REPOS[@]}"; do
    if [ -d "$repo/.git" ]; then
        git -C "$repo" pull --ff-only --quiet 2>&1 || echo "WARN: pull failed for $repo"
    fi
done

for repo in "${SWF_REPOS[@]}"; do
    if [ ! -d "$repo/.git" ]; then
        continue
    fi
    # Fetch all branches
    git -C "$repo" fetch --quiet 2>&1 || { echo "WARN: fetch failed for $repo"; continue; }

    # Find highest infra/baseline-vNN
    highest=$(git -C "$repo" branch -r 2>/dev/null \
        | grep -oP 'origin/infra/baseline-v\K\d+' \
        | sort -n | tail -1)

    if [ -z "$highest" ]; then
        # No versioned branch — just pull current branch
        git -C "$repo" pull --ff-only --quiet 2>&1 || echo "WARN: pull failed for $repo"
        continue
    fi

    target="infra/baseline-v${highest}"
    current=$(git -C "$repo" branch --show-current 2>/dev/null)

    if [ "$current" != "$target" ]; then
        git -C "$repo" checkout "$target" --quiet 2>&1 || { echo "WARN: checkout $target failed for $repo"; continue; }
    fi

    git -C "$repo" pull --ff-only --quiet 2>&1 || echo "WARN: pull failed for $repo ($target)"
done
