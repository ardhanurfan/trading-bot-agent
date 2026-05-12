"""Semantic memory layer using Pinecone for RAG-enhanced decision making.

Uses Ollama's ``nomic-embed-text`` for local, free embeddings — no external
API cost. The embedding model runs on the same Ollama instance as the LLM
agents.

Provides two modes:
- **Semantic retrieval:** Find past decisions similar to the current context
- **Dual-write:** Every new decision is stored in both the markdown log
  (human-readable audit trail) and Pinecone (semantic search).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from pinecone import Pinecone, ServerlessSpec
except ImportError:
    Pinecone = None  # type: ignore[assignment,misc]
    ServerlessSpec = None  # type: ignore[assignment,misc]

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Local embedding via Ollama (nomic-embed-text)
# ---------------------------------------------------------------------------


class OllamaEmbeddings:
    """Embedding client using Ollama's REST API (local, free).

    Uses ``nomic-embed-text`` (768 dimensions) by default.
    """

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._dim: Optional[int] = None

    @property
    def dimension(self) -> int:
        """Return the embedding dimension (lazily detected)."""
        if self._dim is None:
            test = self.embed_query("test")
            self._dim = len(test)
        return self._dim

    def embed_query(self, text: str) -> List[float]:
        """Embed a single text string."""
        if _requests is None:
            raise RuntimeError("requests library required for OllamaEmbeddings")

        resp = _requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": text},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama returns {"embeddings": [[...]]} for /api/embed
        embeddings = data.get("embeddings", [])
        if embeddings:
            return embeddings[0]
        # Fallback for older Ollama versions
        return data.get("embedding", [])

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts."""
        return [self.embed_query(t) for t in texts]


# ---------------------------------------------------------------------------
# Pinecone Memory
# ---------------------------------------------------------------------------

# Namespace constants
NS_DECISIONS = "trading-decisions"
NS_RULES = "trading-rules"
NS_REGIMES = "market-regimes"


class PineconeMemory:
    """Wraps Pinecone for storing and retrieving past trading decisions.

    Designed to work alongside the existing ``TradingMemoryLog`` — not
    replace it.  The markdown log stays as the human-readable audit trail;
    Pinecone adds semantic search capabilities.
    """

    def __init__(
        self,
        api_key: str,
        index_name: str = "trading-memory",
        embedding_model: str = "nomic-embed-text",
        ollama_base_url: str = "http://localhost:11434",
        cloud: str = "aws",
        region: str = "us-east-1",
    ):
        if Pinecone is None:
            raise ImportError(
                "pinecone package required. Install with: pip install pinecone"
            )

        self.pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        self.embeddings = OllamaEmbeddings(
            model=embedding_model,
            base_url=ollama_base_url,
        )

        # Ensure index exists (serverless free tier)
        self._ensure_index(cloud, region)
        self.index = self.pc.Index(index_name)

    def _ensure_index(self, cloud: str, region: str) -> None:
        """Create the Pinecone index if it doesn't exist."""
        existing = [idx.name for idx in self.pc.list_indexes()]
        if self.index_name not in existing:
            dim = self.embeddings.dimension
            logger.info(
                "Creating Pinecone index '%s' (dim=%d, cloud=%s, region=%s)",
                self.index_name, dim, cloud, region,
            )
            self.pc.create_index(
                name=self.index_name,
                dimension=dim,
                metric="cosine",
                spec=ServerlessSpec(cloud=cloud, region=region),
            )

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def store_decision(
        self,
        ticker: str,
        date: str,
        decision: str,
        rating: str,
        reflection: str = "",
    ) -> None:
        """Store a trading decision with metadata for filtered retrieval."""
        doc_id = hashlib.md5(f"{ticker}:{date}".encode()).hexdigest()
        text = f"[{date}] {ticker} — Rating: {rating}\n{decision}"
        if reflection:
            text += f"\nReflection: {reflection}"

        try:
            vector = self.embeddings.embed_query(text)
            self.index.upsert(
                vectors=[{
                    "id": doc_id,
                    "values": vector,
                    "metadata": {
                        "ticker": ticker,
                        "date": date,
                        "rating": rating,
                        "text": text[:4000],  # Pinecone metadata size limit
                    },
                }],
                namespace=NS_DECISIONS,
            )
            logger.info("Stored decision in Pinecone: %s %s (%s)", ticker, date, rating)
        except Exception:
            logger.exception("Failed to store decision in Pinecone for %s", ticker)

    def store_rule(self, rule_id: str, rule_text: str, tags: List[str] = None) -> None:
        """Store a trading rule for future RAG retrieval."""
        try:
            vector = self.embeddings.embed_query(rule_text)
            self.index.upsert(
                vectors=[{
                    "id": rule_id,
                    "values": vector,
                    "metadata": {
                        "text": rule_text[:4000],
                        "tags": tags or [],
                    },
                }],
                namespace=NS_RULES,
            )
        except Exception:
            logger.exception("Failed to store rule in Pinecone: %s", rule_id)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def retrieve_similar_decisions(
        self,
        query: str,
        ticker: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Semantic search for relevant past decisions.

        Parameters
        ----------
        query:
            Natural language query (e.g., "NVDA analysis with RSI divergence").
        ticker:
            Optional ticker filter — only returns decisions for this ticker.
        top_k:
            Number of results to return.

        Returns
        -------
        List of metadata dicts with keys: ticker, date, rating, text.
        """
        try:
            vector = self.embeddings.embed_query(query)
            filter_dict = {"ticker": {"$eq": ticker}} if ticker else None

            results = self.index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                namespace=NS_DECISIONS,
                filter=filter_dict,
            )
            return [match["metadata"] for match in results.get("matches", [])]
        except Exception:
            logger.exception("Pinecone retrieval failed for query: %s", query[:100])
            return []

    def retrieve_rules(self, query: str, top_k: int = 3) -> List[str]:
        """Retrieve trading rules relevant to the current analysis context."""
        try:
            vector = self.embeddings.embed_query(query)
            results = self.index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                namespace=NS_RULES,
            )
            return [m["metadata"]["text"] for m in results.get("matches", [])]
        except Exception:
            logger.exception("Pinecone rule retrieval failed")
            return []

    def get_context_for_agent(
        self,
        ticker: str,
        query: str = "",
        n_decisions: int = 5,
        n_rules: int = 3,
    ) -> str:
        """Build a formatted context string for agent prompt injection.

        Combines semantic retrieval from decisions and rules into a single
        block that can be appended to the agent's ``past_context``.
        """
        if not query:
            query = f"Trading analysis and decision for {ticker}"

        parts: List[str] = []

        # Similar past decisions
        decisions = self.retrieve_similar_decisions(query, ticker=ticker, top_k=n_decisions)
        if decisions:
            parts.append("📊 **Semantically Similar Past Decisions:**")
            for d in decisions:
                parts.append(f"  [{d.get('date', '?')}] {d.get('rating', '?')} — {d.get('text', '')[:300]}")

        # Cross-ticker decisions (without ticker filter)
        cross = self.retrieve_similar_decisions(query, ticker=None, top_k=3)
        cross = [c for c in cross if c.get("ticker") != ticker]
        if cross:
            parts.append("\n🔀 **Cross-Ticker Relevant Decisions:**")
            for c in cross[:2]:
                parts.append(f"  [{c.get('date', '?')}] {c.get('ticker', '?')} {c.get('rating', '?')} — {c.get('text', '')[:200]}")

        # Trading rules
        rules = self.retrieve_rules(query, top_k=n_rules)
        if rules:
            parts.append("\n📜 **Relevant Trading Rules:**")
            for r in rules:
                parts.append(f"  • {r[:200]}")

        return "\n".join(parts) if parts else ""
