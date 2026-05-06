#!/bin/bash
# Render build script — lightweight, no browser dependencies
set -e

echo "=== Installing Python dependencies ==="
pip install -r requirements.txt -q

echo "=== Build complete ==="
