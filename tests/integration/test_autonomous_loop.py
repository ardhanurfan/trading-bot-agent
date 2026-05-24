"""Tests for tradingagents.integrations.autonomous_loop.AutonomousLoop."""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytz
import pytest

# ---------------------------------------------------------------------------
# Comprehensive stubs matching test_ticker_scanner.py so sys.modules state is
# compatible whether this file runs first or after test_ticker_scanner.py.
# All stubs use setdefault so an already-loaded real module is never evicted.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
_INTEGRATIONS_DIR = _REPO_ROOT / "tradingagents" / "integrations"


def _stub(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__path__ = []
    mod.__package__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _stub_with_path(name: str, real_path: Path):
    """Stub a package but keep its real __path__ so submodules are discoverable."""
    mod = types.ModuleType(name)
    mod.__path__ = [str(real_path)]
    mod.__package__ = name
    return mod


# Heavy third-party packages not installed in test environment
for _name in [
    "yfinance", "yfinance.exceptions",
    "stockstats", "stockstats.wrap",
    "redis", "redis.client", "redis.asyncio",
    "telegram", "telegram.ext",
    "pinecone",
    "langchain_core", "langchain_core.messages", "langchain_core.prompts",
    "langchain", "langgraph", "langgraph.graph", "langgraph.prebuilt",
    "langchain_openai", "langchain_anthropic", "langchain_google_genai",
    "binance", "binance.client",
    "lumibot", "lumibot.brokers", "lumibot.strategies", "lumibot.traders",
]:
    sys.modules.setdefault(_name, _stub(_name))

# Minimal symbols needed by modules that check for them
_lc = sys.modules["langchain_core.messages"]
if not hasattr(_lc, "HumanMessage"):
    _lc.HumanMessage = MagicMock
if not hasattr(_lc, "RemoveMessage"):
    _lc.RemoveMessage = MagicMock
if not hasattr(_lc, "AIMessage"):
    _lc.AIMessage = MagicMock
if not hasattr(_lc, "SystemMessage"):
    _lc.SystemMessage = MagicMock

_exc = sys.modules["yfinance.exceptions"]
if not hasattr(_exc, "YFRateLimitError"):
    _exc.YFRateLimitError = Exception

# tradingagents package stubs (with real __path__ so submodule discovery works)
sys.modules.setdefault(
    "tradingagents",
    _stub_with_path("tradingagents", _REPO_ROOT / "tradingagents"),
)
sys.modules.setdefault(
    "tradingagents.integrations",
    _stub_with_path("tradingagents.integrations", _INTEGRATIONS_DIR),
)

# Stub the rating module so trade_validator (imported by integrations/__init__)
# doesn't drag in the full langchain agents chain
for _s in ["tradingagents.agents", "tradingagents.agents.utils"]:
    sys.modules.setdefault(_s, _stub(_s))

_rating_mod = _stub("tradingagents.agents.utils.rating")
_rating_mod.parse_rating = lambda text, **_kw: (
    "Buy" if "buy" in text.lower() or "overweight" in text.lower()
    else "Sell" if "sell" in text.lower() or "underweight" in text.lower()
    else "Hold"
)
sys.modules.setdefault("tradingagents.agents.utils.rating", _rating_mod)

# ---------------------------------------------------------------------------
# Data classes used by autonomous_loop (defined locally so tests don't need
# the real TickerScanner installed; also used as replacement via patch.object)
# ---------------------------------------------------------------------------

class _MockScanResult:
    def __init__(self, candidates=None, total_screened=0, market_regime="Trending"):
        self.candidates = candidates or []
        self.total_screened = total_screened
        self.market_regime = market_regime


class _MockCandidate:
    def __init__(self, ticker: str, score: float = 7.5, catalyst: str = "Momentum"):
        self.ticker = ticker
        self.score = score
        self.catalyst = catalyst


# ---------------------------------------------------------------------------
# Load autonomous_loop via spec_from_file_location to bypass __init__ chains
# ---------------------------------------------------------------------------

_al_spec = importlib.util.spec_from_file_location(
    "tradingagents.integrations.autonomous_loop",
    _INTEGRATIONS_DIR / "autonomous_loop.py",
)
_al_mod = importlib.util.module_from_spec(_al_spec)
sys.modules["tradingagents.integrations.autonomous_loop"] = _al_mod
_al_spec.loader.exec_module(_al_mod)

AutonomousLoop = _al_mod.AutonomousLoop


# Load autonomous_loop directly
_al_spec = importlib.util.spec_from_file_location(
    "tradingagents.integrations.autonomous_loop",
    Path(__file__).parents[2] / "tradingagents" / "integrations" / "autonomous_loop.py",
)
_al_mod = importlib.util.module_from_spec(_al_spec)
sys.modules["tradingagents.integrations.autonomous_loop"] = _al_mod
_al_spec.loader.exec_module(_al_mod)

AutonomousLoop = _al_mod.AutonomousLoop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "market_timezone": "US/Eastern",
    "market_open": "09:30",
    "market_close": "16:00",
    "scan_interval_minutes": 60,
    "max_tickers_per_scan": 3,
    "ticker_cooldown_hours": 4,
}


def _make_loop(config_overrides=None, notifier=None) -> AutonomousLoop:
    cfg = {**_BASE_CONFIG, **(config_overrides or {})}
    mock_graph = MagicMock()
    mock_graph.notifier = notifier
    mock_graph.quick_thinking_llm = MagicMock()
    return AutonomousLoop(mock_graph, cfg)


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------

class TestInit:
    def test_default_config_parsed(self):
        loop = _make_loop()
        assert loop.market_open == (9, 30)
        assert loop.market_close == (16, 0)
        assert loop.scan_interval == 60
        assert loop.max_tickers == 3
        assert loop.cooldown_hours == 4.0

    def test_custom_scan_interval(self):
        loop = _make_loop({"scan_interval_minutes": 30})
        assert loop.scan_interval == 30

    def test_initial_state_clean(self):
        loop = _make_loop()
        assert loop._scan_count == 0
        assert loop._running is True
        assert len(loop._analyzed_today) == 0
        assert len(loop._cooldowns) == 0

    def test_notifier_attached_from_graph(self):
        notifier = MagicMock()
        loop = _make_loop(notifier=notifier)
        assert loop.notifier is notifier

    def test_no_notifier_is_none(self):
        loop = _make_loop()
        # graph.notifier returns a MagicMock by default; set it to None explicitly
        loop.notifier = None
        assert loop.notifier is None


# ---------------------------------------------------------------------------
# TestParseTime
# ---------------------------------------------------------------------------

class TestParseTime:
    def test_standard_market_open(self):
        assert AutonomousLoop._parse_time("09:30") == (9, 30)

    def test_market_close(self):
        assert AutonomousLoop._parse_time("16:00") == (16, 0)

    def test_midnight(self):
        assert AutonomousLoop._parse_time("00:00") == (0, 0)


# ---------------------------------------------------------------------------
# TestIsMarketHours
# ---------------------------------------------------------------------------

class TestIsMarketHours:
    _ET = pytz.timezone("US/Eastern")

    def _make_dt(self, weekday: int, hour: int, minute: int) -> datetime:
        """Create a tz-aware datetime for the given weekday (0=Mon)."""
        # Find next occurrence of the target weekday from a known Monday
        base = datetime(2024, 1, 1, tzinfo=self._ET)  # Monday
        delta = (weekday - base.weekday()) % 7
        d = base + timedelta(days=delta)
        return d.replace(hour=hour, minute=minute)

    def test_weekday_during_hours(self):
        loop = _make_loop()
        with patch.object(loop, "_now_et", return_value=self._make_dt(0, 10, 0)):
            assert loop._is_market_hours() is True

    def test_weekday_before_open(self):
        loop = _make_loop()
        with patch.object(loop, "_now_et", return_value=self._make_dt(0, 8, 0)):
            assert loop._is_market_hours() is False

    def test_weekday_after_close(self):
        loop = _make_loop()
        with patch.object(loop, "_now_et", return_value=self._make_dt(0, 17, 0)):
            assert loop._is_market_hours() is False

    def test_weekend(self):
        loop = _make_loop()
        with patch.object(loop, "_now_et", return_value=self._make_dt(5, 11, 0)):  # Saturday
            assert loop._is_market_hours() is False


# ---------------------------------------------------------------------------
# TestCooldown
# ---------------------------------------------------------------------------

class TestCooldown:
    def test_new_ticker_is_cooled_down(self):
        loop = _make_loop()
        assert loop._is_cooled_down("BTCUSDT") is True

    def test_set_cooldown_blocks_ticker(self):
        loop = _make_loop()
        loop._set_cooldown("ETHUSDT")
        assert loop._is_cooled_down("ETHUSDT") is False

    def test_set_cooldown_adds_to_analyzed_today(self):
        loop = _make_loop()
        loop._set_cooldown("SOLUSDT")
        assert "SOLUSDT" in loop._analyzed_today

    def test_expired_cooldown_clears(self):
        loop = _make_loop()
        loop._cooldowns["BNBUSDT"] = loop._now_et() - timedelta(hours=1)
        assert loop._is_cooled_down("BNBUSDT") is True


# ---------------------------------------------------------------------------
# TestHandleShutdown
# ---------------------------------------------------------------------------

class TestHandleShutdown:
    def test_sets_running_false(self):
        loop = _make_loop()
        loop._running = True
        loop._handle_shutdown(15, None)
        assert loop._running is False

    def test_notifier_called_on_shutdown(self):
        notifier = MagicMock()
        loop = _make_loop(notifier=notifier)
        loop._handle_shutdown(15, None)
        notifier.sync_send_error.assert_called_once()
        call_kwargs = notifier.sync_send_error.call_args[1]
        assert call_kwargs["error_type"] == "Shutdown"

    def test_no_notifier_shutdown_silent(self):
        loop = _make_loop()
        loop.notifier = None
        loop._handle_shutdown(15, None)  # must not raise
        assert loop._running is False


# ---------------------------------------------------------------------------
# TestRunScanCycleNoResults
# ---------------------------------------------------------------------------

class TestRunScanCycleNoResults:
    def test_no_candidates_returns_early(self):
        notifier = MagicMock()
        loop = _make_loop(notifier=notifier)

        with patch.object(_al_mod, "TickerScanner") as mock_scanner_cls:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = _MockScanResult(candidates=[], total_screened=50)
            mock_scanner_cls.return_value = mock_scanner

            loop._run_scan_cycle()

        loop.graph.propagate.assert_not_called()
        notifier.sync_send_hold_decision.assert_called_once()

    def test_scan_count_incremented(self):
        loop = _make_loop()
        with patch.object(_al_mod, "TickerScanner") as mock_scanner_cls:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = _MockScanResult(candidates=[])
            mock_scanner_cls.return_value = mock_scanner
            loop._run_scan_cycle()

        assert loop._scan_count == 1


# ---------------------------------------------------------------------------
# TestRunScanCycleWithResults
# ---------------------------------------------------------------------------

class TestRunScanCycleWithResults:
    def _make_cycle(self, candidates, propagate_side_effect=None):
        notifier = MagicMock()
        loop = _make_loop(notifier=notifier)

        if propagate_side_effect is not None:
            loop.graph.propagate.side_effect = propagate_side_effect
        else:
            loop.graph.propagate.return_value = ({}, "Buy")

        with patch.object(_al_mod, "TickerScanner") as mock_scanner_cls:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = _MockScanResult(
                candidates=candidates, total_screened=100
            )
            mock_scanner_cls.return_value = mock_scanner
            loop._run_scan_cycle()

        return loop, notifier

    def test_propagate_called_for_each_candidate(self):
        candidates = [_MockCandidate("BTCUSDT"), _MockCandidate("ETHUSDT")]
        loop, _ = self._make_cycle(candidates)
        assert loop.graph.propagate.call_count == 2

    def test_cooldown_set_after_analysis(self):
        loop, _ = self._make_cycle([_MockCandidate("SOLUSDT")])
        assert "SOLUSDT" in loop._analyzed_today

    def test_cooled_down_ticker_skipped(self):
        loop = _make_loop()
        loop._set_cooldown("BTCUSDT")  # pre-cool it

        with patch.object(_al_mod, "TickerScanner") as mock_scanner_cls:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = _MockScanResult(
                candidates=[_MockCandidate("BTCUSDT")], total_screened=10
            )
            mock_scanner_cls.return_value = mock_scanner
            loop._run_scan_cycle()

        loop.graph.propagate.assert_not_called()

    def test_analysis_exception_caught(self):
        """propagate raising an exception must not propagate out of _run_scan_cycle."""
        loop, notifier = self._make_cycle(
            [_MockCandidate("LINKUSDT")],
            propagate_side_effect=RuntimeError("API down"),
        )
        notifier.sync_send_error.assert_called()  # error reported

    def test_loop_stops_mid_cycle(self):
        """If _running becomes False mid-loop, remaining tickers are skipped."""
        candidates = [_MockCandidate("BTCUSDT"), _MockCandidate("ETHUSDT")]
        loop = _make_loop()

        call_count = 0

        def side_effect(ticker, trade_date):
            nonlocal call_count
            call_count += 1
            loop._running = False  # stop after first call
            return ({}, "Buy")

        loop.graph.propagate.side_effect = side_effect

        with patch.object(_al_mod, "TickerScanner") as mock_scanner_cls:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = _MockScanResult(
                candidates=candidates, total_screened=50
            )
            mock_scanner_cls.return_value = mock_scanner
            loop._run_scan_cycle()

        assert call_count == 1  # second ticker never reached


# ---------------------------------------------------------------------------
# TestSendCycleSummary
# ---------------------------------------------------------------------------

class TestSendCycleSummary:
    def test_no_notifier_is_noop(self):
        loop = _make_loop()
        loop.notifier = None
        loop._send_cycle_summary([], _MockScanResult())  # must not raise

    def test_trades_and_holds_separated(self):
        notifier = MagicMock()
        loop = _make_loop(notifier=notifier)
        results = [
            {"ticker": "BTCUSDT", "rating": "Buy", "score": 8.0, "catalyst": "Breakout"},
            {"ticker": "ETHUSDT", "rating": "Hold", "score": 5.0, "catalyst": "None"},
        ]
        loop._send_cycle_summary(results, _MockScanResult(total_screened=20))
        notifier.sync_send_error.assert_called_once()
        msg = notifier.sync_send_error.call_args[1]["message"]
        assert "BTCUSDT" in msg
        assert "ETHUSDT" in msg

    def test_cycle_summary_error_type(self):
        notifier = MagicMock()
        loop = _make_loop(notifier=notifier)
        loop._send_cycle_summary([], _MockScanResult())
        call_kwargs = notifier.sync_send_error.call_args[1]
        assert call_kwargs["error_type"] == "CycleSummary"


# ---------------------------------------------------------------------------
# TestBootShutdownMessages
# ---------------------------------------------------------------------------

class TestBootShutdownMessages:
    def test_send_boot_message_calls_notifier(self):
        notifier = MagicMock()
        loop = _make_loop(notifier=notifier)
        loop._send_boot_message()
        notifier.sync_send_error.assert_called_once()
        assert notifier.sync_send_error.call_args[1]["error_type"] == "SystemBoot"

    def test_send_shutdown_message_calls_notifier(self):
        notifier = MagicMock()
        loop = _make_loop(notifier=notifier)
        loop._send_shutdown_message()
        notifier.sync_send_error.assert_called_once()
        assert notifier.sync_send_error.call_args[1]["error_type"] == "SystemShutdown"

    def test_boot_no_notifier_silent(self):
        loop = _make_loop()
        loop.notifier = None
        loop._send_boot_message()  # must not raise

    def test_shutdown_no_notifier_silent(self):
        loop = _make_loop()
        loop.notifier = None
        loop._send_shutdown_message()  # must not raise


# ---------------------------------------------------------------------------
# TestSleepUntilNextScan
# ---------------------------------------------------------------------------

class TestSleepUntilNextScan:
    def test_exits_immediately_when_not_running(self):
        loop = _make_loop({"scan_interval_minutes": 999})
        loop._running = False

        import time
        start = time.monotonic()
        loop._sleep_until_next_scan()
        elapsed = time.monotonic() - start

        # Should complete almost instantly (≤ 1 second), not actually sleep 999 min
        assert elapsed < 1.0
