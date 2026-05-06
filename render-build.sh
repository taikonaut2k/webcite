#!/bin/bash
# Render build script — installs Python deps + Playwright browsers
set -e

echo "=== Installing Python dependencies ==="
pip install -r requirements.txt

echo "=== Installing Playwright Chromium ==="
# Install Chromium browser binary
python3 -m playwright install chromium 2>&1 || echo "playwright install had warnings (continuing)"

# Try installing system deps (may fail on free tier, that's ok)
python3 -m playwright install-deps chromium 2>/dev/null || echo "System deps install skipped (non-fatal)"

# Also try downloading to a writable location
PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers python3 -m playwright install chromium 2>/dev/null || true

echo "=== Build complete ==="
