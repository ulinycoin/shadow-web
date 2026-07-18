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

echo "→ Writing .cursor/mcp.json..."
./venv/bin/python - "$ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
config_path = root / ".cursor" / "mcp.json"
config_path.parent.mkdir(parents=True, exist_ok=True)

if config_path.exists():
    config = json.loads(config_path.read_text(encoding="utf-8"))
else:
    config = {}

servers = config.setdefault("mcpServers", {})
servers["shadow-web"] = {
    "command": str(root / "venv" / "bin" / "shadow-web-mcp"),
}
config_path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"Configured {config_path}")
PY

echo ""
echo "Done. Restart Cursor — MCP server 'shadow-web' loads from .cursor/mcp.json"
echo "Verify: bash scripts/smoke_install.sh"
echo "Optional: pip install -e '.[server]' && shadow-web-server"
