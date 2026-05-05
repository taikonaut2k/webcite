#!/bin/bash
# ─────────────────────────────────────────────────────────────
# WebCite — GitHub Push & Deploy Script
# ─────────────────────────────────────────────────────────────
# Usage: 
#   1. Get a GitHub personal access token (classic) with repo scope
#   2. Run: bash deploy.sh YOUR_GITHUB_TOKEN
# ─────────────────────────────────────────────────────────────
set -e

TOKEN="$1"
USERNAME="ezmenu123"
REPO="webcite"
DOMAIN="pagecite.com"

if [ -z "$TOKEN" ]; then
  echo "Usage: bash deploy.sh YOUR_GITHUB_TOKEN"
  echo ""
  echo "Need a token? Go to: https://github.com/settings/tokens"
  echo "Create a 'classic' token with 'repo' scope."
  exit 1
fi

echo "╔═══════════════════════════════════════════════════╗"
echo "║   🚀 WebCite — Deploy to GitHub + Render         ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Create GitHub repo ──
echo "1. Creating GitHub repository '$REPO'..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/$USERNAME/$REPO)

if [ "$HTTP_CODE" = "200" ]; then
  echo "   ✅ Repo already exists"
elif [ "$HTTP_CODE" = "404" ]; then
  curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    https://api.github.com/user/repos \
    -d "{\"name\":\"$REPO\",\"private\":false,\"description\":\"WebCite — Modern web archiving service\"}" > /dev/null
  echo "   ✅ Repo created"
else
  echo "   ❌ Failed to create repo (HTTP $HTTP_CODE). Check your token."
  exit 1
fi

# ── Step 2: Push code ──
echo "2. Pushing code to GitHub..."
cd ~/archive-clone

# Rename branch to main
git branch -M main 2>/dev/null || true

# Add remote
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$USERNAME/$REPO.git"

# Commit and push
git add -A
git commit -m "Initial commit — WebCite archiving service" 2>/dev/null || echo "   Nothing new to commit"
git push -u origin main 2>&1

echo "   ✅ Code pushed to: https://github.com/$USERNAME/$REPO"
echo ""

# ── Step 3: Render deploy instructions ──
echo "3. ✅ GitHub push complete!"
echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║   NEXT: Deploy on Render.com                      ║"
echo "╠═══════════════════════════════════════════════════╣"
echo "║                                                   ║"
echo "║   1. Go to https://dashboard.render.com           ║"
echo "║                                                   ║"
echo "║   2. Click 'New +' → 'Web Service'               ║"
echo "║                                                   ║"
echo "║   3. Connect your GitHub account                  ║"
echo "║      Select: $USERNAME/$REPO            ║"
echo "║                                                   ║"
echo "║   4. Configure:                                   ║"
echo "║      Name: webcite                                ║"
echo "║      Region: Oregon (or closest)                  ║"
echo "║      Branch: main                                 ║"
echo "║      Runtime: Python                              ║"
echo "║      Build: pip install -r requirements.txt       ║"
echo "║      Start: gunicorn app:app --bind 0.0.0.0:\$PORT ║"
echo "║      Plan: Free                                   ║"
echo "║                                                   ║"
echo "║   5. Click 'Create Web Service'                   ║"
echo "║                                                   ║"
echo "║   6. Go to Settings → Domains                     ║"
echo "║      Add: $DOMAIN                      ║"
echo "║                                                   ║"
echo "║   7. In GoDaddy DNS settings:                     ║"
echo "║      Add CNAME record:                            ║"
echo "║        Host: @ or www                             ║"
echo "║        Points to: webcite.onrender.com            ║"
echo "║                                                   ║"
echo "║   8. Wait 5 min for SSL cert to provision         ║"
echo "║                                                   ║"
echo "║   🎉 Your site: https://$DOMAIN            ║"
echo "╚═══════════════════════════════════════════════════╝"
