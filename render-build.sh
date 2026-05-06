#!/bin/bash
# Render build script — installs Python deps + Playwright browsers
set -e

echo "=== Installing Python dependencies ==="
pip install -r requirements.txt

echo "=== Installing Playwright Chromium ==="
python3 -m playwright install chromium 2>/dev/null || \
  PLAYWRIGHT_BROWSERS_PATH=0 python3 -m playwright install chromium 2>/dev/null || \
  echo "⚠ Playwright browser install skipped (non-fatal)"
