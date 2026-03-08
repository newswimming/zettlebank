#!/usr/bin/env bash
# setup.sh — ZettleBank one-time environment setup
#
# Run once on any new device before starting the server.
# Safe to re-run: all steps are idempotent.
#
# Usage:
#   bash setup.sh
#
# Requires (install manually before running):
#   - Python 3.11–3.13   https://www.python.org/downloads/
#   - Ollama             https://ollama.com/download
#   - Node.js (optional) https://nodejs.org  — only needed to rebuild plugin from source

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR/vault/choracle-remote-00/.obsidian/plugins/zettlebank"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  !${NC} $*"; }
fail() { echo -e "${RED}  ✗ $*${NC}"; exit 1; }
step() { echo -e "\n${CYAN}──▶${NC} $*"; }

echo -e "${CYAN}"
echo "  ███████╗████████╗ ██████╗ ██████╗ ██╗   ██╗██████╗  █████╗ ███╗   ██╗██╗  ██╗"
echo "  ██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗╚██╗ ██╔╝██╔══██╗██╔══██╗████╗  ██║██║ ██╔╝"
echo "  ███████╗   ██║   ██║   ██║██████╔╝ ╚████╔╝ ██████╔╝███████║██╔██╗ ██║█████╔╝ "
echo "  ╚════██║   ██║   ██║   ██║██╔══██╗  ╚██╔╝  ██╔══██╗██╔══██║██║╚██╗██║██╔═██╗ "
echo "  ███████║   ██║   ╚██████╔╝██║  ██║   ██║   ██████╔╝██║  ██║██║ ╚████║██║  ██╗"
echo "  ╚══════╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝"
echo -e "${NC}"
echo "  Environment setup — run once per device"
echo ""

# ─── 1. Detect Python 3.11–3.13 ──────────────────────────────────────────────
step "Detecting Python (3.11–3.13 required; 3.14+ breaks spaCy)"

PY=""
# Check in preference order: py launcher (Windows), then explicit versions, then fallback
for cmd in "py -3.13" "py -3.12" "py -3.11" "python3.13" "python3.12" "python3.11" "python3" "python"; do
    version=$($cmd --version 2>/dev/null | grep -oE "3\.(1[123])\.[0-9]+" || true)
    if [[ -n "$version" ]]; then
        PY="$cmd"
        ok "Found: $cmd → Python $version"
        break
    fi
done

[[ -z "$PY" ]] && fail "Python 3.11–3.13 not found.
     Download from: https://www.python.org/downloads/
     Note: Python 3.14+ breaks spaCy (pydantic.v1 removed)."

# ─── 2. pip dependencies ──────────────────────────────────────────────────────
step "Installing Python dependencies (requirements.txt)"

$PY -m pip install --upgrade pip --quiet
$PY -m pip install -r "$SCRIPT_DIR/requirements.txt"
ok "All Python packages installed"

# ─── 3. spaCy model ───────────────────────────────────────────────────────────
step "Checking spaCy model (en_core_web_sm)"

if $PY -c "import en_core_web_sm" 2>/dev/null; then
    ok "en_core_web_sm already installed"
else
    echo "  Downloading en_core_web_sm..."
    $PY -m spacy download en_core_web_sm
    ok "en_core_web_sm downloaded"
fi

# ─── 4. Ollama + llama3.2 ─────────────────────────────────────────────────────
step "Checking Ollama"

if ! command -v ollama &>/dev/null; then
    warn "Ollama binary not found in PATH."
    warn "Install from: https://ollama.com/download"
    warn "Then run:     ollama pull llama3.2"
    warn "Without Ollama, Stage B (topic labels) and Stage D (affect/code) will fall back to 'mu'."
else
    ok "Ollama found: $(ollama --version 2>/dev/null | head -1 || echo 'installed')"

    step "Pulling llama3.2 model (skip if already downloaded)"
    ollama pull llama3.2
    ok "llama3.2 ready"
fi

# ─── 5. Plugin — verify or rebuild ────────────────────────────────────────────
step "Checking plugin build"

if [[ -f "$PLUGIN_DIR/main.js" ]]; then
    ok "main.js already present — no rebuild needed"
    echo "  (To rebuild from source: cd '$PLUGIN_DIR' && npm install && npm run build)"
elif command -v node &>/dev/null; then
    echo "  main.js missing — building from source..."
    cd "$PLUGIN_DIR"
    npm install
    node esbuild.config.mjs production
    ok "Plugin built successfully"
else
    warn "Node.js not found and main.js is missing."
    warn "Install Node.js from https://nodejs.org then re-run this script,"
    warn "or copy a pre-built main.js from another ZettleBank installation."
fi

# ─── 6. Verify .env ───────────────────────────────────────────────────────────
step "Checking .env config"

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    if [[ -f "$SCRIPT_DIR/.env.example" ]]; then
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        warn ".env not found — copied from .env.example. Edit VAULT_DIR if needed."
    else
        cat > "$SCRIPT_DIR/.env" <<'EOF'
VAULT_DIR=vault/choracle-remote-00
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
EMBED_MODEL_KEY=TaylorAI/bge-micro-v2
SPACY_MODEL=en_core_web_sm
EOF
        warn ".env not found — created with defaults. Edit VAULT_DIR if your vault path differs."
    fi
else
    ok ".env present"
    echo "  Current VAULT_DIR: $(grep VAULT_DIR "$SCRIPT_DIR/.env" | cut -d= -f2)"
fi

# ─── 7. Quick import smoke-test ───────────────────────────────────────────────
step "Running import smoke-test"

$PY -c "
import fastapi, uvicorn, networkx, spacy, bertopic, leidenalg, igraph
import sklearn, numpy, httpx, pydantic, dotenv
print('  All imports OK')
" && ok "Smoke-test passed"

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Setup complete.${NC}"
echo ""
echo "  Start the backend:"
echo -e "    ${CYAN}bash start.sh${NC}"
echo ""
echo "  Then open Obsidian and load the vault:"
echo -e "    ${CYAN}vault/choracle-remote-00${NC}"
echo ""
echo "  Health check (after starting):"
echo -e "    ${CYAN}curl http://127.0.0.1:8000/health${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
