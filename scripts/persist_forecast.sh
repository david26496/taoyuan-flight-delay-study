#!/usr/bin/env bash
# Scoped forecast-only persistence; safely rebase concurrent data writers.
set -euo pipefail
source_name="${1:?collector name required}"
case "$source_name" in taoyuan_forecast) ;; *) exit 2 ;; esac
: "${TARGET_BRANCH:?default branch required}"

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'

# An existing raw file or log must never be changed/deleted by this job.
git diff --exit-code -- "raw/$source_name" "logs/$source_name"
git diff --cached --exit-code
while IFS= read -r -d '' new_file; do
  git add -- "$new_file"
done < <(git ls-files --others --exclude-standard -z -- "raw/$source_name/" "logs/$source_name/")

if git diff --cached --quiet; then
  echo 'No new snapshots or logs to commit.'
  exit 0
fi

git commit -m "data: archive $source_name collection [skip ci]"
for attempt in 1 2 3 4; do
  git fetch origin "$TARGET_BRANCH"
  git rebase "origin/$TARGET_BRANCH"
  if git push origin "HEAD:refs/heads/$TARGET_BRANCH"; then
    exit 0
  fi
  if [ "$attempt" -eq 4 ]; then
    echo 'Push failed after 4 attempts; workflow artifact preserves the files.' >&2
    exit 1
  fi
  sleep "$((2 ** attempt))"
  git fetch origin "$TARGET_BRANCH"
  # Never force-push. Conflicts fail visibly and leave the artifact for recovery.
  git rebase "origin/$TARGET_BRANCH"
done
