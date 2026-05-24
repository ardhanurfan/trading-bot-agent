"""Autonomous continuous trading loop.

Runs the full TradingAgents pipeline in a daemon-like loop.
Crypto markets trade 24/7 — no market-hours gate.

Each cycle:

    1. Scan market for candidates (TickerScanner)
    2. Run deep analysis on top N (TradingAgentsGraph.propagate)
    3. Validate + queue orders (Redis → Executor)
    4. Report via Telegram
    5. Sleep until next interval
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Set

import pytz

from tradingagents.integrations.ticker_scanner import TickerScanner, ScanResult

logger = logging.getLogger(__name__)


class AutonomousLoop:
    """Market-hours-aware continuous trading loop.

    Parameters
    ----------
    graph:
        Initialized ``TradingAgentsGraph`` instance.
    config:
        Full system configuration dict.
    """

    def __init__(self, graph, config: Dict[str, Any]):
        self.graph = graph
        self.config = config

        # Market hours
        tz_name = config.get("market_timezone", "US/Eastern")
        self.tz = pytz.timezone(tz_name)
        self.market_open = self._parse_time(config.get("market_open", "09:30"))
        self.market_close = self._parse_time(config.get("market_close", "16:00"))

        # Scan settings
        self.scan_interval = int(config.get("scan_interval_minutes", 60))
        self.max_tickers = int(config.get("max_tickers_per_scan", 3))
        self.cooldown_hours = float(config.get("ticker_cooldown_hours", 4))

        # State
        self._analyzed_today: Set[str] = set()
        self._cooldowns: Dict[str, datetime] = {}  # ticker → cooldown_until
        self._scan_count = 0
        self._running = True

        # Graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        # Notifier shortcut
        self.notifier = getattr(graph, "notifier", None)

    @staticmethod
    def _parse_time(t: str) -> tuple:
        """Parse 'HH:MM' into (hour, minute) tuple."""
        parts = t.split(":")
        return (int(parts[0]), int(parts[1]))

    def _now_et(self) -> datetime:
        """Current time in market timezone."""
        return datetime.now(self.tz)

    def _is_market_hours(self) -> bool:
        """Check if current time is within market hours (weekday only)."""
        now = self._now_et()
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        current = (now.hour, now.minute)
        return self.market_open <= current < self.market_close

    def _is_weekday(self) -> bool:
        return self._now_et().weekday() < 5

    def _is_cooled_down(self, ticker: str) -> bool:
        """Check if a ticker has passed its cooldown period."""
        if ticker not in self._cooldowns:
            return True
        return self._now_et() >= self._cooldowns[ticker]

    def _set_cooldown(self, ticker: str) -> None:
        """Set cooldown for a ticker."""
        self._cooldowns[ticker] = self._now_et() + timedelta(hours=self.cooldown_hours)
        self._analyzed_today.add(ticker)

    def _handle_shutdown(self, signum, frame):
        """Graceful shutdown handler."""
        logger.info("Shutdown signal received (%s). Finishing current cycle...", signum)
        self._running = False
        if self.notifier:
            self.notifier.sync_send_error(
                error_type="Shutdown",
                message="Autonomous trading loop stopped gracefully.",
                component="AutonomousLoop",
            )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main daemon loop — runs continuously until shutdown.

        Crypto markets operate 24/7 so there is no market-hours gate.
        Every scan interval a full scan → analyze → report cycle is executed.
        """
        logger.info("Autonomous trading loop started (24/7 crypto mode)")
        self._send_boot_message()

        while self._running:
            try:
                self._run_scan_cycle()
                self._sleep_until_next_scan()
            except KeyboardInterrupt:
                break
            except Exception:
                logger.exception("Error in autonomous loop — recovering...")
                if self.notifier:
                    self.notifier.sync_send_error(
                        error_type="LoopError",
                        message="Autonomous loop error — recovering in 60s",
                        component="AutonomousLoop",
                    )
                time.sleep(60)

        self._send_shutdown_message()

    # ------------------------------------------------------------------
    # Scan cycle
    # ------------------------------------------------------------------

    def _run_scan_cycle(self) -> None:
        """Execute one full scan → analyze → report cycle."""
        self._scan_count += 1
        now = self._now_et()
        logger.info("=== Scan cycle #%d at %s ===", self._scan_count, now.strftime("%H:%M:%S"))

        # Notify scan start
        if self.notifier:
            self.notifier.sync_send_pipeline_start(
                ticker=f"Scan #{self._scan_count}",
                trade_date=now.strftime("%Y-%m-%d %H:%M"),
            )

        # Phase 1: Scan for candidates
        scanner = TickerScanner(
            llm=self.graph.quick_thinking_llm,
            config=self.config,
            analyzed_today=self._analyzed_today,
        )
        scan_result = scanner.scan()

        if not scan_result.candidates:
            logger.info("No candidates found in scan #%d", self._scan_count)
            if self.notifier:
                self.notifier.sync_send_hold_decision(
                    ticker="Market Scan",
                    rating=f"No opportunities found (screened {scan_result.total_screened})",
                )
            return

        # Notify scan results
        candidates_str = ", ".join([
            f"{c.ticker}({c.score:.1f})" for c in scan_result.candidates
        ])
        logger.info("Scan found %d candidates: %s", len(scan_result.candidates), candidates_str)

        if self.notifier:
            self.notifier.sync_send_pipeline_complete(
                ticker=f"Scan #{self._scan_count}",
                rating=f"Found: {candidates_str}",
                duration_seconds=0,
            )

        # Phase 2: Deep analysis for each candidate
        trade_date = now.strftime("%Y-%m-%d")
        results = []

        for candidate in scan_result.candidates:
            if not self._running:
                break

            if not self._is_cooled_down(candidate.ticker):
                logger.info("Skipping %s (cooldown active)", candidate.ticker)
                continue

            logger.info(
                "Deep analysis: %s (score=%.1f, catalyst=%s)",
                candidate.ticker, candidate.score, candidate.catalyst,
            )

            try:
                final_state, rating = self.graph.propagate(
                    candidate.ticker, trade_date
                )
                results.append({
                    "ticker": candidate.ticker,
                    "rating": rating,
                    "score": candidate.score,
                    "catalyst": candidate.catalyst,
                })
                self._set_cooldown(candidate.ticker)

            except Exception:
                logger.exception("Analysis failed for %s", candidate.ticker)
                if self.notifier:
                    self.notifier.sync_send_error(
                        error_type="AnalysisError",
                        message=f"Analysis failed for {candidate.ticker}",
                        component="AutonomousLoop",
                    )

        # Phase 3: Cycle summary via Telegram
        self._send_cycle_summary(results, scan_result)

    def _send_cycle_summary(self, results: list, scan_result: ScanResult) -> None:
        """Send a summary of the analysis cycle via Telegram."""
        if not self.notifier:
            return

        trades = [r for r in results if r["rating"] not in ("Hold",)]
        holds = [r for r in results if r["rating"] in ("Hold",)]

        now = self._now_et()
        next_scan = now + timedelta(minutes=self.scan_interval)

        summary_lines = [
            f"📋 **Cycle #{self._scan_count} Summary**",
            f"Screened: {scan_result.total_screened} stocks",
            f"Regime: {scan_result.market_regime}",
            "",
        ]

        if trades:
            summary_lines.append(f"📈 Trades: {len(trades)}")
            for t in trades:
                summary_lines.append(f"  • {t['ticker']} → {t['rating']} (scan score: {t['score']:.1f})")

        if holds:
            summary_lines.append(f"⏸️ Holds: {len(holds)}")
            for h in holds:
                summary_lines.append(f"  • {h['ticker']} → Hold")

        summary_lines.append(f"\n⏰ Next scan: {next_scan.strftime('%I:%M %p ET')}")

        self.notifier.sync_send_error(
            error_type="CycleSummary",
            message="\n".join(summary_lines),
            component="AutonomousLoop",
        )

    # ------------------------------------------------------------------
    # Wait helpers
    # ------------------------------------------------------------------

    def _sleep_until_next_scan(self) -> None:
        """Sleep for scan_interval_minutes, checking for shutdown."""
        logger.info("Next scan in %d minutes", self.scan_interval)
        end_time = time.time() + (self.scan_interval * 60)
        while time.time() < end_time and self._running:
            time.sleep(10)  # Check every 10s for shutdown

    def _wait_for_market_open(self) -> None:
        """Wait until market opens (checks every 60s)."""
        now = self._now_et()
        logger.info("Market closed. Waiting for open at %02d:%02d ET...",
                     self.market_open[0], self.market_open[1])

        # Reset daily state at midnight
        if now.hour == 0 and now.minute < 2:
            self._analyzed_today.clear()
            self._cooldowns.clear()

        while not self._is_market_hours() and self._running and self._is_weekday():
            time.sleep(60)

    def _wait_for_weekday(self) -> None:
        """Wait until next weekday (checks every 5 min)."""
        now = self._now_et()
        logger.info("Weekend. Next market day: Monday. Current: %s", now.strftime("%A"))
        while not self._is_weekday() and self._running:
            time.sleep(300)

    # ------------------------------------------------------------------
    # Telegram lifecycle
    # ------------------------------------------------------------------

    def _send_boot_message(self) -> None:
        """Send system online notification."""
        if not self.notifier:
            return
        mode = self.config.get("execution_mode", "paper").upper()
        self.notifier.sync_send_error(
            error_type="SystemBoot",
            message=(
                f"🤖 TradingAgents ONLINE\n"
                f"Mode: {mode}\n"
                f"Scan interval: {self.scan_interval} min\n"
                f"Max tickers/scan: {self.max_tickers}\n"
                f"Market: {self.market_open[0]:02d}:{self.market_open[1]:02d} - "
                f"{self.market_close[0]:02d}:{self.market_close[1]:02d} ET"
            ),
            component="AutonomousLoop",
        )

    def _send_shutdown_message(self) -> None:
        """Send system offline notification."""
        if not self.notifier:
            return
        self.notifier.sync_send_error(
            error_type="SystemShutdown",
            message=(
                f"🛑 TradingAgents OFFLINE\n"
                f"Scans completed: {self._scan_count}\n"
                f"Tickers analyzed today: {len(self._analyzed_today)}"
            ),
            component="AutonomousLoop",
        )
