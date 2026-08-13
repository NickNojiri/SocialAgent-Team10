#!/usr/bin/env bash
# Spin ColdReach out into its own standalone GitHub repo.
#
# Run this from inside the coldreach/ directory:
#     bash bootstrap_repo.sh [repo-name] [--public]
#
# Requires the GitHub CLI (https://cli.github.com) and `gh auth login`.

set -euo pipefail

REPO_NAME="${1:-coldreach}"
VISIBILITY="--private"
[[ "${2:-}" == "--public" ]] && VISIBILITY="--public"

if ! command -v gh >/dev/null 2>&1; then
    echo "gh is not installed. See https://cli.github.com" >&2
    echo "Without it: create the repo in the GitHub UI, then run the git" >&2
    echo "commands at the bottom of this script by hand." >&2
    exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Copy the project without any local state (.env, *.db, invites/, caches).
for f in coldreach.py cli.py test_coldreach.py requirements.txt \
         README.md .env.example .gitignore contacts.sample.csv; do
    cp "$SRC/$f" "$STAGE/$f"
done

cd "$STAGE"
git init -qb main
git add .
git commit -qm "initial commit: local-first cold-email agent"

gh repo create "$REPO_NAME" $VISIBILITY --source=. --remote=origin --push

echo
echo "Done. Your new repo:"
gh repo view "$REPO_NAME" --json url --jq .url

# Equivalent by hand, if you skip gh:
#   git init -b main && git add . && git commit -m "initial commit"
#   git remote add origin git@github.com:<you>/coldreach.git
#   git push -u origin main
