#!/bin/bash
# ─────────────────────────────────────────────────────────────
# WebCite — Deploy Step by Step
# ─────────────────────────────────────────────────────────────
# Run each command one by one. Paste your token when prompted.
# ─────────────────────────────────────────────────────────────

TOKEN="$1"
USERNAME="ezmenu123"
REPO="webcite"

if [ -z "$TOKEN" ]; then
  echo "Usage: bash deploy_fixed.sh YOUR_GITHUB_TOKEN"
  exit 1
fi

echo "1. Creating GitHub repo..."
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/user/repos \
  -d "{\"name\":\"$REPO\",\"private\":false}" && echo " ✅" || echo " ❌"

echo ""
echo "2. Setting up git..."
cd ~/archive-clone
git branch -m main 2>/dev/null
git remote remove origin 2>/dev/null
git remote add origin "https://$USERNAME:$TOKEN@github.com/$USERNAME/$REPO.git"

echo ""
echo "3. Committing and pushing..."
git add -A
git commit -m "Initial commit — WebCite archiving service" 2>/dev/null || echo "   (nothing new)"
git push -u origin main

echo ""
echo "✅ Done! Now deploy on Render:"
echo "   https://dashboard.render.com/select/repo?repoName=$REPO"
