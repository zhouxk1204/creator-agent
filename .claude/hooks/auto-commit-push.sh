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

  # Build a per-commit message from the actual changes: file count, touched
  # top-level areas, and a (capped) status listing in the body.
  st="$(git status --short 2>/dev/null)"
  nfiles=$(printf '%s\n' "$st" | grep -c . || true)
  areas=$(printf '%s\n' "$st" | sed 's/^...//; s/.* -> //; s/^"//; s/"$//' \
    | cut -d/ -f1 | sort -u | head -5 | paste -sd, - | sed 's/,/, /g')
  [ -n "$areas" ] || areas="working tree"
  body=$(printf '%s\n' "$st" | head -20)

  git commit -q -m "chore: auto-commit session leftovers ($nfiles file(s): $areas)

$body

Co-Authored-By: Claude Code <noreply@anthropic.com>" 2>/dev/null || exit 0
  echo "[auto-commit-push] committed $nfiles leftover file(s): $areas" >&2
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
