#!/usr/bin/env bash
# Turn the committed tree at HEAD into a fresh single-commit repository in a
# separate folder: the public copy of this project. Working history stays here.
#
#   scripts/make_snapshot.sh [--allow-dirty] [--out <parent folder>]
#
# Only what `git archive HEAD` exports is copied: untracked and ignored files
# (local data, secrets, case material) cannot reach the snapshot. Paths listed
# in EXCLUDE below are left out as well. Nothing is pushed and no remote is
# created; the last line printed is the folder to push by hand.

set -euo pipefail
cd "$(dirname "$0")/.."

# Tracked paths that stay out of the public copy: the team's working
# instructions, plan, status and decision log, the submission form text, an
# editor setting and a development harness page. None is read by the app or by
# the checks. One pathspec per line.
EXCLUDE=(
  CLAUDE.md
  DECISIONS.md
  PLAN.md
  STATUS.md
  .claude
  docs/SUBMISSION.md
  web/v2/js/graph/dev.html
)

ALLOW_DIRTY=0
OUT_PARENT="${SNAPSHOT_PARENT:-${TMPDIR:-/tmp}}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --out) OUT_PARENT="${2:?--out needs a folder}"; shift ;;
    *) echo "usage: $0 [--allow-dirty] [--out <parent folder>]" >&2; exit 2 ;;
  esac
  shift
done

dirty="$(git status --porcelain --untracked-files=no)"
if [ -n "$dirty" ]; then
  echo "Tracked files with uncommitted changes (the snapshot is taken from HEAD, so these would be left out):" >&2
  echo "$dirty" >&2
  if [ "$ALLOW_DIRTY" -eq 0 ]; then
    echo "Commit them, or pass --allow-dirty to snapshot HEAD as it is." >&2
    exit 1
  fi
fi

head="$(git rev-parse HEAD)"
repo="$(pwd -P)"
mkdir -p "$OUT_PARENT"
out="$(cd "$OUT_PARENT" && pwd -P)/snapshot-$(date +%Y%m%dT%H%M%S)"
case "$out/" in
  "$repo"/*) echo "The snapshot folder must be outside this repository: $out" >&2; exit 2 ;;
esac
if [ -e "$out" ]; then
  echo "Already exists: $out" >&2
  exit 2
fi
mkdir "$out"

pathspec=(.)
for path in "${EXCLUDE[@]}"; do pathspec+=(":(exclude)$path"); done
git archive --format=tar "$head" -- "${pathspec[@]}" | tar -x -C "$out"

# Last line of defence, by pattern: the same policy as tests/test_repo_hygiene.py.
leaks="$(cd "$out" && find . -mindepth 1 \( \
  -iname '*case materials*' -o -name '*-clio-data.json' -o -name 'HANDOFF.md' -o -path './mockup/*' \
  -o -path './data/*' -o -path './.conduct/*' -o \( -name '.env*' ! -name '.env.example' \) \
  -o -name '*.db' -o -name '*.db-*' -o -name '*.sqlite*' -o -name '*.zip' -o -name '*.pdf' \
  -o -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' -o -name '*.pem' -o -name '*.key' \
  -o -name 'firm_passcode' -o -name '*.cookies' -o -iname '*token*.txt' \) -print)"
if [ -n "$leaks" ]; then
  echo "Refusing: the snapshot holds paths that must not be published:" >&2
  echo "$leaks" >&2
  echo "Folder left for inspection, without a commit: $out" >&2
  exit 3
fi

git -C "$out" init --quiet --initial-branch=main
git -C "$out" add --all
git -C "$out" commit --quiet -m "Corro: submission snapshot" \
  -m "Single-commit copy of the working repository at ${head:0:12}." \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

echo "HEAD used:      $head"
echo "Excluded:       ${#EXCLUDE[@]} tracked path(s)"
echo "Files:          $(git -C "$out" ls-files | wc -l)"
echo "Size:           $(du -sh --exclude=.git "$out" | cut -f1)"
echo "Commit:         $(git -C "$out" log --format='%h %an <%ae> %s' -1)"
echo "Snapshot:       $out"
