#!/bin/bash
# Update the GitHub token in the webcite repo remote URL.
# Usage: bash scripts/update-github-token.sh YOUR_NEW_TOKEN
#
# Generate a new token at https://github.com/settings/tokens (classic PAT, repo scope)

TOKEN=$1
if [ -z "$TOKEN" ]; then
    echo "Usage: $0 YOUR_GITHUB_TOKEN"
    echo ""
    echo "Steps:"
    echo "  1. Go to https://github.com/settings/tokens"
    echo "  2. Generate a new classic token with 'repo' scope"
    echo "  3. Run: $0 ghp_xxxxxxxxxxxxxxxxxxxx"
    exit 1
fi

cd "$(dirname "$0")/.."
git remote set-url origin "https://taikonaut2k:${TOKEN}@github.com/taikonaut2k/webcite.git"

echo "✅ Remote URL updated. Now run: git push origin main"
echo "   Render will auto-deploy from the main branch."
