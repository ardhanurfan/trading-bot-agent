################################################################################
# Dockerfile — TradingAgents Agentic AI Trading System
#
# Two-stage build:
#   1. Builder: installs Python deps in a venv
#   2. Runtime: slim image with venv + app code
#
# Includes integration dependencies: httpx, pinecone, redis, alpaca-py
################################################################################

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY tradingagents/ tradingagents/
COPY cli/ cli/
COPY main.py run_autonomous.py ./

# Install the package + integration extras
RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir httpx pinecone python-dotenv

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser && \
    mkdir -p /home/appuser/.tradingagents && \
    chown -R appuser:appuser /home/appuser/.tradingagents

USER appuser
WORKDIR /home/appuser/app

COPY --from=builder --chown=appuser:appuser /build .

# Default: run the analysis entry point
CMD ["python", "main.py"]
