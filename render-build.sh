#!/bin/bash
# Render build script — installs Python deps + Chromium
set -e

echo "=== Installing system packages ==="
apt-get update -qq && apt-get install -y -qq --no-install-recommends \
  chromium-browser chromium-browser-l10n chromium-codecs-ffmpeg \
  libgbm1 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
  libgbm-dev libcups2 libasound2 2>&1 | tail -3

echo "=== Installing Python dependencies ==="
pip install -r requirements.txt -q

echo "=== Setting up Chromium for Playwright ==="
# Find Chromium binary location
CHROME_PATH=$(which chromium-browser || which chromium || echo "/usr/bin/chromium-browser")
echo "Chromium at: $CHROME_PATH"

# Tell Playwright where to find it
echo "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=$CHROME_PATH" >> /etc/environment

echo "=== Build complete ==="
