#!/usr/bin/env bash
# Smoke test: install shadow-web[mcp], unit tests, golden path (--quick).
# Usage: bash scripts/smoke_install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "→ Creating venv (if missing)..."
python3 -m venv venv 2>/dev/null || true

echo "→ Installing shadow-web[mcp]..."
./venv/bin/pip install -q -U pip
./venv/bin/pip install -q -e ".[mcp,dev]"

echo "→ Installing Playwright Chromium..."
./venv/bin/playwright install chromium

echo "→ Verifying MCP entrypoint..."
./venv/bin/python -c "from shadow_web.mcp.server import create_mcp_server; create_mcp_server(); print('MCP server OK')"

echo "→ Unit tests (schema_snap)..."
./venv/bin/python -m pytest tests/test_schema_snap.py -q

echo "→ Golden path demo (--quick, 1 live site)..."
./venv/bin/python examples/golden_path/demo.py --quick

echo ""
echo "✓ Smoke test passed. Add to Cursor: bash scripts/cursor-setup.sh"
