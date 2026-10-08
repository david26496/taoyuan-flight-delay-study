#!/usr/bin/env bash
# Case 2 has its own queue to avoid evicting pending Arrival runs. Rebase handles all writers.
set -euo pipefail
source_name="${1:?collector name required}"
case "$source_name" in tdx) ;; *) exit 2 ;; esac
: "${TARGET_BRANCH:?default branch required}"

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'

# An existing raw file or log must never be changed/deleted by this job.
git diff --exit-code -- "raw/case2/$source_name/departure" "logs/case2/$source_name"
git diff --cached --exit-code
while IFS= read -r -d '' new_file; do
  git add -- "$new_file"
done < <(git ls-files --others --exclude-standard -z -- "raw/case2/$source_name/departure/" "logs/case2/$source_name/")

if git diff --cached --quiet; then
  echo 'No new snapshots or logs to commit.'
  exit 0
fi

git commit -m "data: archive Case 2 $source_name departure collection [skip ci]"
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
  # Next attempt fetches/rebases again. Never force-push.
done
