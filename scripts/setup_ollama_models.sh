#!/bin/bash
###############################################################################
# TradingAgents — Ollama Model Setup for M3 Pro 18GB
#
# Downloads and verifies the required models for local inference.
#
# Usage:
#   chmod +x scripts/setup_ollama_models.sh
#   ./scripts/setup_ollama_models.sh
#
# Or via Docker:
#   docker compose exec ollama bash -c "$(cat scripts/setup_ollama_models.sh)"
###############################################################################

set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  TradingAgents — Ollama Model Setup (M3 Pro 18GB)        ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Quick-think model: Qwen3 8B (~5.5GB) ───────────────────────────────
echo "📦 [1/3] Pulling qwen3:8b (quick-think agent — ~5.5GB)..."
ollama pull qwen3:8b
echo "   ✅ qwen3:8b ready"
echo ""

# ── 2. Deep-think model: Gemma4 12B (~8.5GB) ──────────────────────────────
echo "📦 [2/3] Pulling gemma4 (deep-think agent — ~8.5GB)..."
echo "   ⚠️  This model + qwen3 will use ~14GB combined."
echo "   If you prefer a lighter setup, skip this and use qwen3 for both."
ollama pull gemma4
echo "   ✅ gemma4 ready"
echo ""

# ── 3. Embedding model: nomic-embed-text (~274MB) ─────────────────────────
echo "📦 [3/3] Pulling nomic-embed-text (Pinecone embeddings — ~274MB)..."
ollama pull nomic-embed-text
echo "   ✅ nomic-embed-text ready"
echo ""

# ── Verify ─────────────────────────────────────────────────────────────────
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  Installed Models:                                        ║"
echo "╚═══════════════════════════════════════════════════════════╝"
ollama list
echo ""
echo "🚀 Setup complete! You can now run TradingAgents."
echo ""
echo "Quick test:  ollama run qwen3:8b 'What is RSI in trading?'"
echo "Full system: docker compose up"
