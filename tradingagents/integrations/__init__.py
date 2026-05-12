"""Integration modules for the Agentic AI Trading System.

This package provides pluggable integrations that extend TradingAgents
from an analysis-only framework into a full end-to-end trading system:

- trade_validator: Pydantic-based validation of LLM trade decisions
- telegram_notifier: Real-time alerts via Telegram Bot API
- redis_queue: Redis Streams order queue with file fallback
- pinecone_memory: Semantic RAG memory via Pinecone + Ollama embeddings
- lumibot_executor: Alpaca paper/live trade execution
"""

from tradingagents.integrations.trade_validator import (
    ExecutableTradeOrder,
    OrderSide,
    parse_decision_to_order,
)
from tradingagents.integrations.telegram_notifier import TelegramNotifier
from tradingagents.integrations.redis_queue import RedisOrderQueue

__all__ = [
    "ExecutableTradeOrder",
    "OrderSide",
    "parse_decision_to_order",
    "TelegramNotifier",
    "RedisOrderQueue",
]
