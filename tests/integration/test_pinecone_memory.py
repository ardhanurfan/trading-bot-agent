"""Tests for tradingagents.integrations.pinecone_memory."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load pinecone_memory directly to bypass integrations/__init__ chain
# ---------------------------------------------------------------------------

_PM_PATH = (
    Path(__file__).parents[2] / "tradingagents" / "integrations" / "pinecone_memory.py"
)
_pm_spec = importlib.util.spec_from_file_location(
    "tradingagents.integrations.pinecone_memory", _PM_PATH
)
_pm_mod = importlib.util.module_from_spec(_pm_spec)
sys.modules["tradingagents.integrations.pinecone_memory"] = _pm_mod
_pm_spec.loader.exec_module(_pm_mod)

OllamaEmbeddings = _pm_mod.OllamaEmbeddings
PineconeMemory = _pm_mod.PineconeMemory
NS_DECISIONS = _pm_mod.NS_DECISIONS
NS_RULES = _pm_mod.NS_RULES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_requests(embeddings_response=None):
    """Return a patched _requests module with a mocked POST."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = embeddings_response or {"embeddings": [[0.1, 0.2, 0.3]]}

    mock_req = MagicMock()
    mock_req.post.return_value = mock_resp
    return mock_req


def _make_pinecone_memory(index_names=None):
    """Build a PineconeMemory with all external deps mocked."""
    from types import SimpleNamespace

    mock_pc = MagicMock()
    mock_index = MagicMock()
    # SimpleNamespace so `.name` attribute returns the actual string
    mock_pc.list_indexes.return_value = [
        SimpleNamespace(name=n) for n in (index_names or [])
    ]
    mock_pc.Index.return_value = mock_index

    mock_pinecone_cls = MagicMock(return_value=mock_pc)
    mock_serverless_spec = MagicMock(return_value=MagicMock())
    mock_req = _mock_requests()

    with patch.object(_pm_mod, "Pinecone", mock_pinecone_cls), \
         patch.object(_pm_mod, "ServerlessSpec", mock_serverless_spec), \
         patch.object(_pm_mod, "_requests", mock_req):
        mem = PineconeMemory(api_key="test-key", index_name="test-index")

    mem._pc = mock_pc
    mem._mock_index = mock_index
    # Reset call history so tests don't see init-time calls
    mock_req.post.reset_mock()
    return mem, mock_pc, mock_index, mock_req


# ---------------------------------------------------------------------------
# TestOllamaEmbeddings
# ---------------------------------------------------------------------------

class TestOllamaEmbeddings:
    def test_embed_query_returns_list(self):
        emb = OllamaEmbeddings()
        mock_req = _mock_requests({"embeddings": [[0.1, 0.2, 0.3]]})
        with patch.object(_pm_mod, "_requests", mock_req):
            result = emb.embed_query("test text")
        assert result == [0.1, 0.2, 0.3]

    def test_embed_query_fallback_old_format(self):
        """Older Ollama uses 'embedding' (singular) key."""
        emb = OllamaEmbeddings()
        mock_req = _mock_requests({"embeddings": [], "embedding": [0.5, 0.6]})
        with patch.object(_pm_mod, "_requests", mock_req):
            result = emb.embed_query("test")
        assert result == [0.5, 0.6]

    def test_embed_query_raises_when_requests_none(self):
        emb = OllamaEmbeddings()
        with patch.object(_pm_mod, "_requests", None):
            with pytest.raises(RuntimeError, match="requests library"):
                emb.embed_query("hello")

    def test_embed_query_posts_to_correct_url(self):
        emb = OllamaEmbeddings(model="nomic-embed-text", base_url="http://localhost:11434")
        mock_req = _mock_requests()
        with patch.object(_pm_mod, "_requests", mock_req):
            emb.embed_query("query text")
        call_args = mock_req.post.call_args
        assert "api/embed" in call_args[0][0]
        assert call_args[1]["json"]["model"] == "nomic-embed-text"

    def test_embed_documents_calls_embed_query_for_each(self):
        emb = OllamaEmbeddings()
        mock_req = _mock_requests()
        with patch.object(_pm_mod, "_requests", mock_req):
            results = emb.embed_documents(["text1", "text2", "text3"])
        assert len(results) == 3
        assert mock_req.post.call_count == 3

    def test_dimension_lazily_detected(self):
        emb = OllamaEmbeddings()
        assert emb._dim is None
        mock_req = _mock_requests({"embeddings": [[0.1, 0.2, 0.3, 0.4]]})
        with patch.object(_pm_mod, "_requests", mock_req):
            dim = emb.dimension
        assert dim == 4
        assert emb._dim == 4

    def test_dimension_cached_after_first_call(self):
        emb = OllamaEmbeddings()
        mock_req = _mock_requests({"embeddings": [[0.1, 0.2]]})
        with patch.object(_pm_mod, "_requests", mock_req):
            _ = emb.dimension
            _ = emb.dimension  # second access must not re-call API
        assert mock_req.post.call_count == 1


# ---------------------------------------------------------------------------
# TestPineconeMemoryInit
# ---------------------------------------------------------------------------

class TestPineconeMemoryInit:
    def test_raises_when_pinecone_none(self):
        with patch.object(_pm_mod, "Pinecone", None):
            with pytest.raises(ImportError, match="pinecone"):
                PineconeMemory(api_key="key")

    def test_creates_index_when_missing(self):
        mock_pc = MagicMock()
        mock_pc.list_indexes.return_value = []  # no existing indexes
        mock_pc.Index.return_value = MagicMock()
        mock_req = _mock_requests({"embeddings": [[0.1] * 768]})

        with patch.object(_pm_mod, "Pinecone", MagicMock(return_value=mock_pc)), \
             patch.object(_pm_mod, "ServerlessSpec", MagicMock()), \
             patch.object(_pm_mod, "_requests", mock_req):
            PineconeMemory(api_key="key", index_name="new-index")

        mock_pc.create_index.assert_called_once()
        call_kwargs = mock_pc.create_index.call_args[1]
        assert call_kwargs["name"] == "new-index"

    def test_skips_index_creation_when_exists(self):
        from types import SimpleNamespace
        mock_pc = MagicMock()
        mock_pc.list_indexes.return_value = [SimpleNamespace(name="existing")]
        mock_pc.Index.return_value = MagicMock()
        mock_req = _mock_requests()

        with patch.object(_pm_mod, "Pinecone", MagicMock(return_value=mock_pc)), \
             patch.object(_pm_mod, "ServerlessSpec", MagicMock()), \
             patch.object(_pm_mod, "_requests", mock_req):
            PineconeMemory(api_key="key", index_name="existing")

        mock_pc.create_index.assert_not_called()


# ---------------------------------------------------------------------------
# TestStoreDecision
# ---------------------------------------------------------------------------

class TestStoreDecision:
    def test_upserts_to_decisions_namespace(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()

        mock_req.post.return_value.json.return_value = {"embeddings": [[0.1, 0.2, 0.3]]}
        with patch.object(_pm_mod, "_requests", mock_req):
            mem.store_decision("BTCUSDT", "2024-01-01", "Strong bull thesis", "Buy")

        mock_index.upsert.assert_called_once()
        call_kw = mock_index.upsert.call_args[1]
        assert call_kw["namespace"] == NS_DECISIONS
        vec = call_kw["vectors"][0]
        assert vec["metadata"]["ticker"] == "BTCUSDT"
        assert vec["metadata"]["rating"] == "Buy"

    def test_includes_reflection_when_provided(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()

        mock_req.post.return_value.json.return_value = {"embeddings": [[0.1]]}
        with patch.object(_pm_mod, "_requests", mock_req):
            mem.store_decision("ETHUSDT", "2024-01-02", "Bearish", "Sell",
                               reflection="RSI showed divergence")

        text = mock_index.upsert.call_args[1]["vectors"][0]["metadata"]["text"]
        assert "Reflection" in text

    def test_exception_does_not_raise(self):
        mem, _, mock_index, _ = _make_pinecone_memory()
        mock_index.upsert.side_effect = RuntimeError("Pinecone down")
        mock_req = _mock_requests()

        with patch.object(_pm_mod, "_requests", mock_req):
            mem.store_decision("SOLUSDT", "2024-01-01", "decision", "Hold")  # must not raise


# ---------------------------------------------------------------------------
# TestStoreRule
# ---------------------------------------------------------------------------

class TestStoreRule:
    def test_upserts_to_rules_namespace(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        mock_req.post.return_value.json.return_value = {"embeddings": [[0.5]]}

        with patch.object(_pm_mod, "_requests", mock_req):
            mem.store_rule("rule-001", "Never trade against the trend", tags=["risk"])

        call_kw = mock_index.upsert.call_args[1]
        assert call_kw["namespace"] == NS_RULES
        assert call_kw["vectors"][0]["id"] == "rule-001"

    def test_exception_does_not_raise(self):
        mem, _, mock_index, _ = _make_pinecone_memory()
        mock_index.upsert.side_effect = RuntimeError("error")
        mock_req = _mock_requests()
        with patch.object(_pm_mod, "_requests", mock_req):
            mem.store_rule("r1", "some rule")  # must not raise


# ---------------------------------------------------------------------------
# TestRetrieveSimilarDecisions
# ---------------------------------------------------------------------------

class TestRetrieveSimilarDecisions:
    def _setup_query(self, mem, mock_index, mock_req, matches):
        mock_index.query.return_value = {"matches": [{"metadata": m} for m in matches]}
        mock_req.post.return_value.json.return_value = {"embeddings": [[0.1]]}

    def test_returns_metadata_list(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        self._setup_query(mem, mock_index, mock_req, [
            {"ticker": "BTCUSDT", "date": "2024-01-01", "rating": "Buy", "text": "thesis"},
        ])
        with patch.object(_pm_mod, "_requests", mock_req):
            results = mem.retrieve_similar_decisions("bullish BTC")
        assert len(results) == 1
        assert results[0]["ticker"] == "BTCUSDT"

    def test_ticker_filter_passed(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        self._setup_query(mem, mock_index, mock_req, [])
        with patch.object(_pm_mod, "_requests", mock_req):
            mem.retrieve_similar_decisions("query", ticker="ETHUSDT")
        call_kw = mock_index.query.call_args[1]
        assert call_kw["filter"] == {"ticker": {"$eq": "ETHUSDT"}}

    def test_no_ticker_filter_is_none(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        self._setup_query(mem, mock_index, mock_req, [])
        with patch.object(_pm_mod, "_requests", mock_req):
            mem.retrieve_similar_decisions("query", ticker=None)
        call_kw = mock_index.query.call_args[1]
        assert call_kw["filter"] is None

    def test_exception_returns_empty_list(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        mock_index.query.side_effect = RuntimeError("error")
        with patch.object(_pm_mod, "_requests", mock_req):
            result = mem.retrieve_similar_decisions("query")
        assert result == []


# ---------------------------------------------------------------------------
# TestRetrieveRules
# ---------------------------------------------------------------------------

class TestRetrieveRules:
    def test_returns_rule_texts(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        mock_index.query.return_value = {"matches": [
            {"metadata": {"text": "Rule 1"}},
            {"metadata": {"text": "Rule 2"}},
        ]}
        mock_req.post.return_value.json.return_value = {"embeddings": [[0.1]]}

        with patch.object(_pm_mod, "_requests", mock_req):
            rules = mem.retrieve_rules("momentum strategy")

        assert rules == ["Rule 1", "Rule 2"]

    def test_exception_returns_empty_list(self):
        mem, _, mock_index, _ = _make_pinecone_memory()
        mock_index.query.side_effect = RuntimeError("error")
        mock_req = _mock_requests()
        with patch.object(_pm_mod, "_requests", mock_req):
            result = mem.retrieve_rules("query")
        assert result == []


# ---------------------------------------------------------------------------
# TestGetContextForAgent
# ---------------------------------------------------------------------------

class TestGetContextForAgent:
    def test_empty_when_no_results(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        mock_index.query.return_value = {"matches": []}
        mock_req.post.return_value.json.return_value = {"embeddings": [[0.1]]}

        with patch.object(_pm_mod, "_requests", mock_req):
            ctx = mem.get_context_for_agent("BTCUSDT")

        assert ctx == ""

    def test_contains_decision_section(self):
        mem, _, mock_index, mock_req = _make_pinecone_memory()

        def _query_side_effect(**kw):
            ns = kw.get("namespace", "")
            if ns == NS_DECISIONS:
                return {"matches": [
                    {"metadata": {"ticker": "BTCUSDT", "date": "2024-01-01",
                                  "rating": "Buy", "text": "Strong thesis"}}
                ]}
            return {"matches": []}

        mock_index.query.side_effect = _query_side_effect
        mock_req.post.return_value.json.return_value = {"embeddings": [[0.1]]}

        with patch.object(_pm_mod, "_requests", mock_req):
            ctx = mem.get_context_for_agent("BTCUSDT")

        assert "Past Decisions" in ctx

    def test_cross_ticker_excluded_from_cross_section(self):
        """Same-ticker results should be filtered out of cross-ticker section."""
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        mock_req.post.return_value.json.return_value = {"embeddings": [[0.1]]}

        # Return same ticker for both calls (cross results include same ticker)
        mock_index.query.return_value = {"matches": [
            {"metadata": {"ticker": "BTCUSDT", "date": "2024-01-01",
                          "rating": "Buy", "text": "...", "text": "text"}},
        ]}

        with patch.object(_pm_mod, "_requests", mock_req):
            ctx = mem.get_context_for_agent("BTCUSDT")

        # BTCUSDT should be in decisions section but NOT in cross-ticker section
        if "Cross-Ticker" in ctx:
            cross_section = ctx.split("Cross-Ticker")[1]
            assert "BTCUSDT" not in cross_section

    def test_default_query_includes_ticker(self):
        """When query is empty, a default query mentioning the ticker is used."""
        mem, _, mock_index, mock_req = _make_pinecone_memory()
        mock_index.query.return_value = {"matches": []}
        mock_req.post.return_value.json.return_value = {"embeddings": [[0.1]]}

        with patch.object(_pm_mod, "_requests", mock_req):
            mem.get_context_for_agent("ETHUSDT", query="")

        # Any of the embed_query calls should contain "ETHUSDT"
        all_inputs = [
            c[1]["json"]["input"]
            for c in mock_req.post.call_args_list
        ]
        assert any("ETHUSDT" in inp for inp in all_inputs)
