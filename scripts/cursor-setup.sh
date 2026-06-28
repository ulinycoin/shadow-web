#!/usr/bin/env bash
# One-time setup: Shadow Web MCP for Cursor
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "→ Installing shadow-web[mcp] in venv..."
python3 -m venv venv 2>/dev/null || true
./venv/bin/pip install -q -e ".[mcp]"

echo "→ Installing Playwright Chromium..."
./venv/bin/playwright install chromium

echo "→ Verifying MCP entrypoint..."
./venv/bin/shadow-web-mcp --help 2>/dev/null || ./venv/bin/python -c "from shadow_web.mcp.server import create_mcp_server; create_mcp_server(); print('MCP server OK')"

echo ""
echo "Done. Restart Cursor — MCP server 'shadow-web' loads from .cursor/mcp.json"
echo "Verify: bash scripts/smoke_install.sh"
echo "Optional: start heal API → python3 -m uvicorn server.main:app --port 8000"
