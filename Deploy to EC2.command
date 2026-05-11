#!/bin/bash

# ── Move to the project root (handles spaces in path) ──────────────────────────
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$SCRIPT_DIR"

clear
echo ""
echo "╔════════════════════════════════════════════════╗"
echo "║   🚀  Orbit Notes — Deploy to EC2              ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Upload Python backend to EC2 ──────────────────────────────────────
echo "  📡  Uploading backend files..."
rsync -az --delete \
  --exclude='.env' \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='data/' \
  --exclude='schedule_config.json' \
  --exclude='push_schedule_config.json' \
  -e "ssh -o StrictHostKeyChecking=no" \
  . \
  gravitee-operations-lms:~/orbit-notes/

if [ $? -ne 0 ]; then
  echo ""
  echo "  ❌  Upload failed. Check your internet connection and VPN."
  echo ""
  read -p "  Press Enter to close..."
  exit 1
fi

echo "  ✅  Upload complete"
echo ""

# ── Step 2: Install deps + restart service ────────────────────────────────────
echo "  🔁  Restarting service on server..."
ssh -o StrictHostKeyChecking=no gravitee-operations-lms \
  "cd ~/orbit-notes && \
   ~/.local/bin/uv sync --quiet && \
   sudo systemctl restart orbit-notes"

if [ $? -ne 0 ]; then
  echo ""
  echo "  ❌  Restart failed."
  echo ""
  read -p "  Press Enter to close..."
  exit 1
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo "  ✅  Restarted"
echo ""
echo "  ─────────────────────────────────────────────"
echo "  🌐  Orbit Notes API running on port 8001"
echo "  ─────────────────────────────────────────────"
echo ""
echo "  Changes are now live."
echo ""
read -p "  Press Enter to close..."
