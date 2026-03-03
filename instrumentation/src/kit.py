"""InstrumentationKit — facade for clean instrumentation API.

Wraps InstrumentationContext with a simple 3-method interface:
- log_entry(...) — capture entry with enriched fields
- log_exit(...) — capture exit and auto-score
- log_missed(...) — capture blocked signal
- classify_regime(...) — get market regime
- capture_snapshot(...) — capture market data

All methods swallow exceptions using safe_instrument pattern.
Never crashes trading.
"""
from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from .context import InstrumentationContext
from .hooks import safe_instrument

logger = logging.getLogger("instrumentation.kit")


class InstrumentationKit:
    """Central facade for all instrumentation operations.

    Usage::

        ctx = InstrumentationContext(...)
        kit = InstrumentationKit(ctx, strategy_id="ATRSS")

        trade = kit.log_entry(
            trade_id="t1",
            pair="BTC/USDT",
            side="LONG",
            entry_price=50000,
            position_size=1.0,
            position_size_quote=50000,
            entry_signal="EMA cross",
            entry_signal_id="ema_123",
            entry_signal_strength=0.8,
            active_filters=["volume"],
            passed_filters=["volume"],
            strategy_params={"ema_fast": 12},
            signal_factors=[{"factor": "momentum", "value": 0.75}],
            filter_decisions=[{"filter": "volume", "current": 1000000, "threshold": 500000}],
            sizing_inputs={"risk_pct": 1.0, "atr": 500},
            portfolio_state_at_entry={"total_exposure": 0.5, "positions": 3},
        )

        kit.log_exit(
            trade_id="t1",
            exit_price=51000,
            exit_reason="TAKE_PROFIT",
            fees_paid=50,
        )
    """

    def __init__(self, ctx: InstrumentationContext, strategy_id: str):
        """Initialize the kit with context and strategy ID.

        Args:
            ctx: InstrumentationContext with all services
            strategy_id: Strategy identifier (e.g. "ATRSS", "AKC", "SWING_BREAKOUT_V3")
        """
        self.ctx = ctx
        self.strategy_id = strategy_id

    def log_entry(
        self,
        trade_id: str,
        pair: str,
        side: str,
        entry_price: float,
        position_size: float,
        position_size_quote: float,
        entry_signal: str,
        entry_signal_id: str,
        entry_signal_strength: float,
        active_filters: List[str],
        passed_filters: List[str],
        strategy_params: dict,
        signal_factors: Optional[List[dict]] = None,
        filter_decisions: Optional[List[dict]] = None,
        sizing_inputs: Optional[dict] = None,
        portfolio_state_at_entry: Optional[dict] = None,
        exchange_timestamp: Optional[datetime] = None,
        expected_entry_price: Optional[float] = None,
        entry_latency_ms: Optional[int] = None,
        bar_id: Optional[str] = None,
    ) -> Any:
        """Log a trade entry with full instrumentation and enriched data.

        Automatically:
        - Calls regime_classifier.current_regime(pair) to tag market condition
        - Captures market snapshot
        - Stores enriched fields (signal_factors, filter_decisions, sizing_inputs, portfolio_state)
        - Writes to JSONL

        Never raises. Returns TradeEvent on success, empty dict on failure.

        Args:
            trade_id: Unique trade identifier
            pair: Trading pair (e.g. "BTC/USDT")
            side: "LONG" or "SHORT"
            entry_price: Actual fill price
            position_size: Size in base asset
            position_size_quote: Size in quote asset
            entry_signal: Signal name (e.g. "EMA cross")
            entry_signal_id: Signal instance ID
            entry_signal_strength: 0.0-1.0 signal confidence
            active_filters: List of filters that ran
            passed_filters: List of filters that passed
            strategy_params: Strategy config snapshot
            signal_factors: List of dicts describing what drove the signal
            filter_decisions: List of dicts with filter decision details
            sizing_inputs: Dict with inputs to position sizing (risk, ATR, etc)
            portfolio_state_at_entry: Dict with portfolio exposure and positions
            exchange_timestamp: Exchange order timestamp
            expected_entry_price: Expected vs actual slippage
            entry_latency_ms: Time from signal to fill
            bar_id: Bar/candle identifier for reproducibility

        Returns:
            TradeEvent dict on success, empty dict on failure (never raises)
        """

        def _log_entry_impl():
            if self.ctx is None or self.ctx.trade_logger is None:
                return {}

            # Get current market regime
            regime = "unknown"
            if self.ctx.regime_classifier is not None:
                regime = self.ctx.regime_classifier.current_regime(pair) or "unknown"

            # Log the entry with all parameters including enriched ones
            trade_event = self.ctx.trade_logger.log_entry(
                trade_id=trade_id,
                pair=pair,
                side=side,
                entry_price=entry_price,
                position_size=position_size,
                position_size_quote=position_size_quote,
                entry_signal=entry_signal,
                entry_signal_id=entry_signal_id,
                entry_signal_strength=entry_signal_strength,
                active_filters=active_filters,
                passed_filters=passed_filters,
                strategy_params=strategy_params,
                strategy_id=self.strategy_id,
                exchange_timestamp=exchange_timestamp,
                expected_entry_price=expected_entry_price,
                entry_latency_ms=entry_latency_ms,
                market_regime=regime,
                bar_id=bar_id,
                # Enriched fields passed as kwargs (Task 3 will add them to signature)
                signal_factors=signal_factors or [],
                filter_decisions=filter_decisions or [],
                sizing_inputs=sizing_inputs,
                portfolio_state_at_entry=portfolio_state_at_entry,
            )

            return trade_event.to_dict() if hasattr(trade_event, 'to_dict') else {}

        return safe_instrument(_log_entry_impl) or {}

    def log_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        fees_paid: float = 0.0,
        exchange_timestamp: Optional[datetime] = None,
        expected_exit_price: Optional[float] = None,
        exit_latency_ms: Optional[int] = None,
    ) -> Any:
        """Log a trade exit and auto-score the process quality.

        Automatically:
        - Logs the exit to TradeLogger
        - Calls ProcessScorer.score_and_write to tag root causes
        - Writes both trade and score to JSONL

        Never raises. Returns the scored TradeEvent on success, empty dict on failure.

        Args:
            trade_id: Unique trade identifier (must match entry)
            exit_price: Actual exit fill price
            exit_reason: Exit category (SIGNAL, STOP_LOSS, TAKE_PROFIT, TRAILING, TIMEOUT, MANUAL, etc)
            fees_paid: Fees charged for the exit
            exchange_timestamp: Exchange order timestamp
            expected_exit_price: Expected vs actual slippage
            exit_latency_ms: Time from exit signal to fill

        Returns:
            TradeEvent dict with process_score on success, empty dict on failure (never raises)
        """

        def _log_exit_impl():
            if self.ctx is None or self.ctx.trade_logger is None:
                return {}

            # Log the exit
            trade_event = self.ctx.trade_logger.log_exit(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_reason=exit_reason,
                fees_paid=fees_paid,
                exchange_timestamp=exchange_timestamp,
                expected_exit_price=expected_exit_price,
                exit_latency_ms=exit_latency_ms,
            )

            if trade_event is None:
                return {}

            # Auto-score the trade
            if self.ctx.process_scorer is not None:
                trade_dict = trade_event.to_dict() if hasattr(trade_event, 'to_dict') else trade_event
                self.ctx.process_scorer.score_and_write(
                    trade=trade_dict,
                    strategy_type=self.strategy_id,
                    data_dir=self.ctx.data_dir,
                )

            return trade_event.to_dict() if hasattr(trade_event, 'to_dict') else {}

        return safe_instrument(_log_exit_impl) or {}

    def log_missed(
        self,
        pair: str,
        side: str,
        signal: str,
        signal_id: str,
        signal_strength: float,
        blocked_by: str,
        block_reason: str = "",
        strategy_params: Optional[dict] = None,
        market_regime: str = "",
        exchange_timestamp: Optional[datetime] = None,
        bar_id: Optional[str] = None,
    ) -> Any:
        """Log a signal that fired but was blocked by a filter or risk limit.

        Automatically:
        - Captures market snapshot
        - Computes hypothetical entry price
        - Schedules outcome backfill
        - Writes to missed opportunity JSONL

        Never raises. Returns MissedOpportunityEvent on success, empty dict on failure.

        Args:
            pair: Trading pair
            side: "LONG" or "SHORT"
            signal: Signal name
            signal_id: Signal instance ID
            signal_strength: 0.0-1.0 signal confidence
            blocked_by: What blocked the signal (e.g. "max_open_trades")
            block_reason: More detailed reason
            strategy_params: Strategy config at time of signal
            market_regime: Market regime classification
            exchange_timestamp: Signal timestamp
            bar_id: Bar/candle identifier

        Returns:
            MissedOpportunityEvent dict on success, empty dict on failure (never raises)
        """

        def _log_missed_impl():
            if self.ctx is None or self.ctx.missed_logger is None:
                return {}

            event = self.ctx.missed_logger.log_missed(
                pair=pair,
                side=side,
                signal=signal,
                signal_id=signal_id,
                signal_strength=signal_strength,
                blocked_by=blocked_by,
                block_reason=block_reason,
                strategy_params=strategy_params,
                strategy_type=self.strategy_id,
                strategy_id=self.strategy_id,
                market_regime=market_regime,
                exchange_timestamp=exchange_timestamp,
                bar_id=bar_id,
            )

            return event.to_dict() if hasattr(event, 'to_dict') else {}

        return safe_instrument(_log_missed_impl) or {}

    def classify_regime(self, symbol: str) -> str:
        """Get the current market regime for a symbol.

        Returns one of:
        - "trending_up"
        - "trending_down"
        - "ranging"
        - "volatile"
        - "unknown" (on error or no classifier)

        Never raises.

        Args:
            symbol: Trading symbol

        Returns:
            Regime string, always valid (never raises)
        """

        def _classify_impl():
            if self.ctx is None or self.ctx.regime_classifier is None:
                return "unknown"

            regime = self.ctx.regime_classifier.classify(symbol)
            return regime if regime in {"trending_up", "trending_down", "ranging", "volatile", "unknown"} else "unknown"

        return safe_instrument(_classify_impl) or "unknown"

    def capture_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Capture a market snapshot for a symbol.

        Never raises. Returns dict on success, None on failure.

        Args:
            symbol: Trading symbol

        Returns:
            Market snapshot dict with bid/ask/mid/atr/volume/etc, or None on error
        """

        def _capture_impl():
            if self.ctx is None or self.ctx.snapshot_service is None:
                return None

            snapshot = self.ctx.snapshot_service.capture_now(symbol)
            return snapshot.to_dict() if hasattr(snapshot, 'to_dict') else None

        return safe_instrument(_capture_impl)
