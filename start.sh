#!/usr/bin/env bash
# start.sh — Start the ZettleBank backend server
#
# Usage:
#   bash start.sh            # foreground (Ctrl-C to stop)
#   bash start.sh --bg       # background (writes to server.log)
#
# On Windows (Git Bash / MSYS2) the server is launched via cmd.exe to avoid
# a numba/LLVM crash that occurs when uvicorn is spawned directly from bash.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BG=false
[[ "${1:-}" == "--bg" ]] && BG=true

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  !${NC} $*"; }
fail() { echo -e "${RED}  ✗ $*${NC}"; exit 1; }

# ─── Detect Python ────────────────────────────────────────────────────────────
PY=""
for cmd in "py -3.13" "py -3.12" "py -3.11" "python3.13" "python3.12" "python3.11" "python3" "python"; do
    version=$($cmd --version 2>/dev/null | grep -oE "3\.(1[123])\.[0-9]+" || true)
    if [[ -n "$version" ]]; then
        PY="$cmd"
        break
    fi
done
[[ -z "$PY" ]] && fail "Python 3.11–3.13 not found. Run setup.sh first."

# ─── Check port 8000 ──────────────────────────────────────────────────────────
port_in_use() {
    # Works on both Windows (via netstat) and Unix (via lsof/ss)
    if [[ -n "${WINDIR:-}" ]] || [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        cmd //c "netstat -ano 2>nul | findstr :8000 | findstr LISTENING" 2>/dev/null | grep -q "LISTENING" && return 0 || return 1
    else
        command -v lsof &>/dev/null && lsof -i :8000 -sTCP:LISTEN -t &>/dev/null && return 0
        command -v ss   &>/dev/null && ss -ltn | grep -q ':8000 ' && return 0
        return 1
    fi
}

if port_in_use; then
    warn "Port 8000 already in use — a server may already be running."
    warn "Health check: curl http://127.0.0.1:8000/health"
    exit 0
fi

# ─── Start server ─────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}  ZettleBank backend${NC}"
echo -e "  URL:  http://127.0.0.1:8000"
echo -e "  Docs: http://127.0.0.1:8000/docs"
echo ""

IS_WINDOWS=false
if [[ -n "${WINDIR:-}" ]] || [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    IS_WINDOWS=true
fi

WIN_DIR="$(cygpath -w "$SCRIPT_DIR" 2>/dev/null || echo "$SCRIPT_DIR")"

if $BG; then
    LOG="$SCRIPT_DIR/server.log"
    echo "  Logging to: $LOG"
    echo ""
    if $IS_WINDOWS; then
        # On Windows background tasks in bash trigger the LLVM/numba crash; use cmd start
        cmd //c "cd /d $WIN_DIR && start /B py -3.13 -m uvicorn server:app --host 127.0.0.1 --port 8000 > server.log 2>&1"
    else
        cd "$SCRIPT_DIR"
        nohup $PY -m uvicorn server:app --host 127.0.0.1 --port 8000 > "$LOG" 2>&1 &
        echo $! > "$SCRIPT_DIR/server.pid"
        ok "Started in background (PID $!). Stop with: bash stop.sh"
    fi

    # Wait for server to become ready (up to 20s)
    echo -n "  Waiting for server"
    for i in $(seq 1 20); do
        sleep 1
        echo -n "."
        if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
            echo ""
            ok "Server is up"
            curl -s http://127.0.0.1:8000/health | $PY -m json.tool 2>/dev/null || true
            exit 0
        fi
    done
    echo ""
    warn "Server did not respond within 20s. Check $LOG for errors."

else
    # Foreground — Ctrl-C to stop
    ok "Starting in foreground (Ctrl-C to stop)..."
    echo ""
    if $IS_WINDOWS; then
        cmd //c "cd /d $WIN_DIR && py -3.13 -m uvicorn server:app --host 127.0.0.1 --port 8000"
    else
        cd "$SCRIPT_DIR"
        exec $PY -m uvicorn server:app --host 127.0.0.1 --port 8000
    fi
fi
