#!/bin/bash
# Scheduled (launchd) refresh: pull the GitHub Action's latest scans/ledger, measure outcomes, mirror into Obsidian.
DESK="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DESK" || exit 1
echo "== $(date)"
GIT_TERMINAL_PROMPT=0 git pull --ff-only --quiet || echo "git pull skipped"
"$DESK/.venv/bin/python" "$DESK/desk.py" learn   # scores outcomes, then syncs Obsidian
