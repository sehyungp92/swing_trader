"""Trade Event Logger — structured trade events with full context.

Wraps the bot's existing entry/exit logic to capture WHY a trade was taken
and what the market looked like.  The wrapper is transparent: same inputs,
same outputs, same side effects.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from .event_metadata import EventMetadata, create_event_metadata
from .market_snapshot import MarketSnapshot, MarketSnapshotService

logger = logging.getLogger("instrumentation.trade_logger")


@dataclass
class TradeEvent:
    """Complete record of a single trade from entry to exit.

    Created at entry time with exit fields as None.
    Updated at exit time to fill in exit data.
    Written to JSONL at both entry and exit (as separate events).
    """
    # Identity + timing
    trade_id: str
    event_metadata: dict
    entry_snapshot: dict
    exit_snapshot: Optional[dict] = None

    # Trade data
    pair: str = ""
    side: str = ""                          # "LONG" or "SHORT"
    strategy_id: str = ""
    entry_time: str = ""
    exit_time: Optional[str] = None
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    position_size: float = 0.0
    position_size_quote: float = 0.0
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    fees_paid: Optional[float] = None

    # WHY — critical instrumentation
    entry_signal: str = ""
    entry_signal_id: str = ""
    entry_signal_strength: float = 0.0      # 0.0-1.0
    exit_reason: str = ""                   # SIGNAL | STOP_LOSS | TAKE_PROFIT | TRAILING | TIMEOUT | MANUAL | STALL | CATASTROPHIC | BIAS_FLIP
    market_regime: str = ""

    # Filters
    active_filters: List[str] = field(default_factory=list)
    passed_filters: List[str] = field(default_factory=list)
    blocked_by: Optional[str] = None

    # Context at entry
    atr_at_entry: Optional[float] = None
    spread_at_entry_bps: Optional[float] = None
    volume_24h_at_entry: Optional[float] = None
    funding_rate_at_entry: Optional[float] = None
    open_interest_at_entry: Optional[float] = None

    # Strategy config snapshot
    strategy_params_at_entry: Optional[dict] = None

    # Execution quality
    expected_entry_price: Optional[float] = None
    entry_slippage_bps: Optional[float] = None
    expected_exit_price: Optional[float] = None
    exit_slippage_bps: Optional[float] = None
    entry_latency_ms: Optional[int] = None
    exit_latency_ms: Optional[int] = None

    # Event stage
    stage: str = "entry"                    # "entry" or "exit"

    def to_dict(self) -> dict:
        return asdict(self)


class TradeLogger:
    """Captures trade events by wrapping the bot's entry/exit functions.

    Usage::

        logger = TradeLogger(config, snapshot_service)
        trade = logger.log_entry(trade_id="abc", pair="QQQ", ...)
        logger.log_exit(trade_id="abc", exit_price=510.0, ...)
    """

    def __init__(self, config: dict, snapshot_service: MarketSnapshotService):
        self.bot_id = config["bot_id"]
        self.data_dir = Path(config["data_dir"]) / "trades"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_service = snapshot_service
        self.data_source_id = config.get("data_source_id", "ibkr_execution")
        self._open_trades: Dict[str, TradeEvent] = {}

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
        strategy_id: str = "",
        exchange_timestamp: Optional[datetime] = None,
        expected_entry_price: Optional[float] = None,
        entry_latency_ms: Optional[int] = None,
        market_regime: str = "",
        bar_id: Optional[str] = None,
    ) -> TradeEvent:
        """Call immediately after a trade entry is confirmed (fill received)."""
        try:
            now = datetime.now(timezone.utc)
            exch_ts = exchange_timestamp or now

            entry_snapshot = self.snapshot_service.capture_now(pair)

            entry_slippage_bps = None
            if expected_entry_price and expected_entry_price > 0:
                entry_slippage_bps = abs(entry_price - expected_entry_price) / expected_entry_price * 10000

            metadata = create_event_metadata(
                bot_id=self.bot_id,
                event_type="trade",
                payload_key=f"{trade_id}_entry",
                exchange_timestamp=exch_ts,
                data_source_id=self.data_source_id,
                bar_id=bar_id,
            )

            trade = TradeEvent(
                trade_id=trade_id,
                event_metadata=metadata.to_dict(),
                entry_snapshot=entry_snapshot.to_dict(),
                pair=pair,
                side=side,
                strategy_id=strategy_id,
                entry_time=exch_ts.isoformat(),
                entry_price=entry_price,
                position_size=position_size,
                position_size_quote=position_size_quote,
                entry_signal=entry_signal,
                entry_signal_id=entry_signal_id,
                entry_signal_strength=entry_signal_strength,
                market_regime=market_regime,
                active_filters=active_filters,
                passed_filters=passed_filters,
                atr_at_entry=entry_snapshot.atr_14,
                spread_at_entry_bps=entry_snapshot.spread_bps,
                volume_24h_at_entry=entry_snapshot.volume_24h,
                funding_rate_at_entry=entry_snapshot.funding_rate,
                open_interest_at_entry=entry_snapshot.open_interest,
                strategy_params_at_entry=strategy_params,
                expected_entry_price=expected_entry_price,
                entry_slippage_bps=round(entry_slippage_bps, 2) if entry_slippage_bps else None,
                entry_latency_ms=entry_latency_ms,
                stage="entry",
            )

            self._open_trades[trade_id] = trade
            self._write_event(trade)
            return trade

        except Exception as e:
            self._write_error("log_entry", trade_id, e)
            return TradeEvent(trade_id=trade_id, event_metadata={}, entry_snapshot={})

    def log_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        fees_paid: float = 0.0,
        exchange_timestamp: Optional[datetime] = None,
        expected_exit_price: Optional[float] = None,
        exit_latency_ms: Optional[int] = None,
    ) -> Optional[TradeEvent]:
        """Call immediately after a trade exit is confirmed."""
        try:
            trade = self._open_trades.pop(trade_id, None)
            if trade is None:
                self._write_error("log_exit", trade_id,
                    Exception(f"No open trade found for trade_id={trade_id}"))
                return None

            now = datetime.now(timezone.utc)
            exch_ts = exchange_timestamp or now

            exit_snapshot = self.snapshot_service.capture_now(trade.pair)

            if trade.side == "LONG":
                pnl = (exit_price - trade.entry_price) * trade.position_size - fees_paid
                pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100 if trade.entry_price else 0
            else:
                pnl = (trade.entry_price - exit_price) * trade.position_size - fees_paid
                pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100 if trade.entry_price else 0

            exit_slippage_bps = None
            if expected_exit_price and expected_exit_price > 0:
                exit_slippage_bps = abs(exit_price - expected_exit_price) / expected_exit_price * 10000

            trade.exit_snapshot = exit_snapshot.to_dict()
            trade.exit_time = exch_ts.isoformat()
            trade.exit_price = exit_price
            trade.exit_reason = exit_reason
            trade.pnl = round(pnl, 4)
            trade.pnl_pct = round(pnl_pct, 4)
            trade.fees_paid = fees_paid
            trade.expected_exit_price = expected_exit_price
            trade.exit_slippage_bps = round(exit_slippage_bps, 2) if exit_slippage_bps else None
            trade.exit_latency_ms = exit_latency_ms
            trade.stage = "exit"

            trade.event_metadata = create_event_metadata(
                bot_id=self.bot_id,
                event_type="trade",
                payload_key=f"{trade_id}_exit",
                exchange_timestamp=exch_ts,
                data_source_id=self.data_source_id,
            ).to_dict()

            self._write_event(trade)
            return trade

        except Exception as e:
            self._write_error("log_exit", trade_id, e)
            return None

    def get_open_trades(self) -> Dict[str, TradeEvent]:
        return dict(self._open_trades)

    def _write_event(self, trade: TradeEvent) -> None:
        """Append trade event to daily JSONL file."""
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            filepath = self.data_dir / f"trades_{today}.jsonl"
            with open(filepath, "a") as f:
                f.write(json.dumps(trade.to_dict(), default=str) + "\n")
        except Exception as e:
            logger.warning("Failed to write trade event: %s", e)

    def _write_error(self, method: str, trade_id: str, error: Exception) -> None:
        """Log instrumentation errors without crashing."""
        try:
            error_dir = Path(self.data_dir).parent / "errors"
            error_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            filepath = error_dir / f"instrumentation_errors_{today}.jsonl"
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "component": "trade_logger",
                "method": method,
                "trade_id": trade_id,
                "error": str(error),
                "error_type": type(error).__name__,
            }
            with open(filepath, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
