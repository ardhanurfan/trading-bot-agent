#!/bin/bash
###############################################################################
# TradingAgents — End-to-End Test Script
#
# Runs a full analysis pipeline using local Ollama models.
# Requires: Docker Compose services running (ollama + redis)
#
# Usage:
#   chmod +x scripts/run_test.sh
#   ./scripts/run_test.sh [TICKER] [DATE]
#
# Example:
#   ./scripts/run_test.sh NVDA 2026-05-10
###############################################################################

set -euo pipefail

TICKER="${1:-NVDA}"
TRADE_DATE="${2:-2026-05-10}"

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  TradingAgents — End-to-End Test                         ║"
echo "║  Ticker: ${TICKER}                                       ║"
echo "║  Date:   ${TRADE_DATE}                                   ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# ── Pre-flight checks ─────────────────────────────────────────────────────
echo "🔍 Pre-flight checks..."

# Check Ollama
if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "   ✅ Ollama is running"
else
    echo "   ❌ Ollama not reachable at localhost:11434"
    echo "      Start with: docker compose up ollama"
    exit 1
fi

# Check Redis
if redis-cli ping > /dev/null 2>&1; then
    echo "   ✅ Redis is running"
else
    echo "   ⚠️  Redis not reachable (will use file-based fallback)"
fi

# Check models
echo "   📋 Available models:"
ollama list 2>/dev/null | head -5 || echo "      (could not list models)"

echo ""
echo "🚀 Starting analysis..."
echo "─────────────────────────────────────────────────────────────"

# ── Run ────────────────────────────────────────────────────────────────────
python main.py <<EOF
${TICKER}
${TRADE_DATE}
EOF

echo ""
echo "─────────────────────────────────────────────────────────────"
echo "✅ Analysis complete!"
echo ""
echo "📂 Results: ~/.tradingagents/logs/${TICKER}/"
echo "📋 Memory:  ~/.tradingagents/memory/trading_memory.md"

# Check Redis queue
if redis-cli ping > /dev/null 2>&1; then
    PENDING=$(redis-cli XLEN tradingagents:orders 2>/dev/null || echo "0")
    echo "📬 Orders in queue: ${PENDING}"
fi
