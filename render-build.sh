#!/bin/bash
# Render build script — installs Python deps + Playwright's bundled Chromium
set -e

echo "=== Installing Python dependencies ==="
pip install -r requirements.txt -q

echo "=== Installing Playwright + bundled Chromium ==="
# Install Playwright browsers to a writable location  
python3 -m playwright install chromium 2>&1

# Also try with explicit path
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 -m playwright install chromium 2>/dev/null || true

# Verify
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    path = p.chromium.executable_path
    print(f'Chromium installed at: {path}')
    import os
    print(f'Exists: {os.path.exists(path)}')
" 2>&1 || echo "⚠ Chromium verification failed"

echo "=== Build complete ==="
