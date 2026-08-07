#!/bin/bash
# Render build script — installs deps + Chromium for full-page media capture
set -e

echo "=== Installing system deps for Chromium (full-page capture) ==="
apt-get update -qq || true
apt-get install -y -qq --no-install-recommends \
    libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libxshmfence1 libx11-xcb1 \
    fonts-liberation 2>/dev/null || echo "apt install skipped (non-root), continuing..."

echo "=== Installing Python dependencies ==="
pip install -r requirements.txt -q

echo "=== Installing Playwright Chromium (for JS-rendered pages) ==="
python -m playwright install chromium --with-deps 2>/dev/null || \
python -m playwright install chromium || \
echo "Playwright browser install skipped"

echo "=== Build complete ==="
