#!/usr/bin/env bash
# Start the prediction market terminal (Python server + Electron app)
# Usage: ./start.sh   or   bash start.sh

set -e

# Always run from the repo root, regardless of where the script is called from
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$REPO/.server.log"

echo "==> Starting Python analytics server (port 8081)..."
echo "    Logs: $LOG"

cd "$REPO"
python3 api_server.py > "$LOG" 2>&1 &
PYTHON_PID=$!

# Shut down the Python server when this script exits (Ctrl+C, error, or normal exit)
cleanup() {
  echo ""
  echo "==> Shutting down Python server (PID $PYTHON_PID)..."
  kill "$PYTHON_PID" 2>/dev/null
  wait "$PYTHON_PID" 2>/dev/null
  echo "==> Done."
}
trap cleanup SIGINT SIGTERM EXIT

# Wait briefly for the server to be ready
sleep 1.5

echo "==> Starting Electron terminal..."
echo "    (Press Ctrl+C here to stop both)"
echo ""

cd "$REPO/terminal"
npm run dev
