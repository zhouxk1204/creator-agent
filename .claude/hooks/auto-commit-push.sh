#!/usr/bin/env bash
# Stop hook: when a Claude Code turn ends, commit any leftover working-tree
# changes and push, so the remote always reflects the latest state.
# Never blocks the session: always exits 0.
set -u

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

dirty=0
git diff --quiet 2>/dev/null || dirty=1
git diff --cached --quiet 2>/dev/null || dirty=1
[ -n "$(git ls-files --others --exclude-standard 2>/dev/null)" ] && dirty=1

if [ "$dirty" -eq 1 ]; then
  git add -A 2>/dev/null || exit 0
  git commit -q -m "chore: auto-commit uncommitted changes on session stop

Co-Authored-By: Claude Code <noreply@anthropic.com>" 2>/dev/null || exit 0
  echo "[auto-commit-push] committed leftover changes" >&2
fi

# Push if the local branch is ahead of its upstream (covers manual commits too).
if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
  ahead=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)
  if [ "${ahead:-0}" -gt 0 ]; then
    if git push -q 2>/dev/null; then
      echo "[auto-commit-push] pushed $ahead commit(s)" >&2
    fi
  fi
fi

exit 0
