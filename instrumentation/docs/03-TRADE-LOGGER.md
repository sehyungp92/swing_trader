# Task 3: Implement Trade Event Logger

## Goal

Wrap the bot's existing entry/exit logic to capture structured trade events with full context. Every trade must record not just what happened, but WHY — the signal, the regime, the filters, the market state.

**Critical rule:** The wrapper must be transparent. Same inputs, same outputs, same side effects as the original function. The trade must execute identically whether instrumentation is working or broken.

## Schema

```python
# instrumentation/src/trade_logger.py

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from .event_metadata import EventMetadata, create_event_metadata
from .market_snapshot import MarketSnapshot, MarketSnapshotService


@dataclass
class TradeEvent:
    """
    Complete record of a single trade from entry to exit.

    Created at entry time with exit fields as None.
    Updated at exit time to fill in exit data.
    Written to JSONL at both entry and exit (as separate events).
    """
    # Identity + timing
    trade_id: str                           # unique, from bot's existing trade ID or generated
    event_metadata: dict                    # EventMetadata.to_dict()
    entry_snapshot: dict                    # MarketSnapshot.to_dict() at entry
    exit_snapshot: Optional[dict] = None    # MarketSnapshot.to_dict() at exit

    # Trade data
    pair: str = ""
    side: str = ""                          # "LONG" or "SHORT"
    entry_time: str = ""                    # ISO 8601
    exit_time: Optional[str] = None
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    position_size: float = 0.0
    position_size_quote: float = 0.0        # size in quote currency
    pnl: Optional[float] = None             # realized, quote currency
    pnl_pct: Optional[float] = None         # as % of position
    fees_paid: Optional[float] = None       # total fees for entry + exit

    # WHY — this is the critical instrumentation
    entry_signal: str = ""                  # human-readable description
    entry_signal_id: str = ""               # machine identifier for the signal type
    entry_signal_strength: float = 0.0      # 0.0–1.0
    exit_reason: str = ""                   # SIGNAL | STOP_LOSS | TAKE_PROFIT | TRAILING | TIMEOUT | MANUAL
    market_regime: str = ""                 # from regime classifier (Task 8)

    # Filters
    active_filters: List[str] = field(default_factory=list)    # filters that were ON
    passed_filters: List[str] = field(default_factory=list)    # filters that passed
    blocked_by: Optional[str] = None                           # should be None for executed trades

    # Context at entry
    atr_at_entry: Optional[float] = None
    spread_at_entry_bps: Optional[float] = None
    volume_24h_at_entry: Optional[float] = None
    funding_rate_at_entry: Optional[float] = None
    open_interest_at_entry: Optional[float] = None

    # Strategy config snapshot
    strategy_params_at_entry: Optional[dict] = None  # TP/SL levels, indicator params, etc.

    # Execution quality
    expected_entry_price: Optional[float] = None     # price at signal time
    entry_slippage_bps: Optional[float] = None       # actual vs expected
    expected_exit_price: Optional[float] = None
    exit_slippage_bps: Optional[float] = None
    entry_latency_ms: Optional[int] = None           # signal time to fill time
    exit_latency_ms: Optional[int] = None

    # Event stage
    stage: str = "entry"                    # "entry" or "exit" — indicates which write this is

    def to_dict(self) -> dict:
        return asdict(self)
```

## Implementation

### Step 1: Create the TradeLogger class

```python
# instrumentation/src/trade_logger.py (continued)

class TradeLogger:
    """
    Captures trade events by wrapping the bot's entry/exit functions.

    Usage pattern:
        logger = TradeLogger(config, snapshot_service)

        # At entry:
        trade = logger.log_entry(
            trade_id="abc123",
            pair="BTC/USDT",
            side="LONG",
            entry_price=50000.0,
            position_size=0.1,
            signal_info={...},
            filter_info={...},
            strategy_params={...},
        )

        # At exit:
        logger.log_exit(
            trade_id="abc123",
            exit_price=51000.0,
            exit_reason="TAKE_PROFIT",
            fees_paid=12.50,
        )
    """

    def __init__(self, config: dict, snapshot_service: MarketSnapshotService):
        self.bot_id = config["bot_id"]
        self.data_dir = Path(config["data_dir"]) / "trades"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_service = snapshot_service
        self.data_source_id = config.get("data_source_id", "unknown")
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
        exchange_timestamp: Optional[datetime] = None,
        expected_entry_price: Optional[float] = None,
        entry_latency_ms: Optional[int] = None,
        market_regime: str = "",
        bar_id: Optional[str] = None,
    ) -> TradeEvent:
        """
        Call this immediately after a trade entry is confirmed (fill received).

        Args:
            trade_id: unique identifier (from exchange or generated)
            pair: trading pair, e.g. "BTC/USDT"
            side: "LONG" or "SHORT"
            entry_price: actual fill price
            position_size: size in base currency
            position_size_quote: size in quote currency
            entry_signal: human-readable signal description
            entry_signal_id: machine ID for signal type (e.g. "ema_cross_bullish")
            entry_signal_strength: 0.0–1.0 confidence
            active_filters: list of filter names that were active
            passed_filters: list of filter names that passed
            strategy_params: dict of strategy configuration at entry time
            exchange_timestamp: fill timestamp from exchange (or None for local time)
            expected_entry_price: price at signal generation time (for slippage calc)
            entry_latency_ms: time from signal to fill in ms
            market_regime: from regime classifier
            bar_id: candle alignment identifier
        """
        try:
            now = datetime.now(timezone.utc)
            exch_ts = exchange_timestamp or now

            # Capture market snapshot at entry
            entry_snapshot = self.snapshot_service.capture_now(pair)

            # Compute slippage
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
            # CRITICAL: instrumentation failure must never block trading
            self._write_error("log_entry", trade_id, e)
            # Return a minimal trade object so the caller can continue
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
        """
        Call this immediately after a trade exit is confirmed.

        Args:
            trade_id: must match a previous log_entry call
            exit_price: actual fill price
            exit_reason: one of SIGNAL | STOP_LOSS | TAKE_PROFIT | TRAILING | TIMEOUT | MANUAL
            fees_paid: total fees for entry + exit combined
            exchange_timestamp: fill timestamp from exchange
            expected_exit_price: trigger price (for slippage calc)
            exit_latency_ms: time from exit trigger to fill
        """
        try:
            trade = self._open_trades.pop(trade_id, None)
            if trade is None:
                self._write_error("log_exit", trade_id,
                    Exception(f"No open trade found for trade_id={trade_id}"))
                return None

            now = datetime.now(timezone.utc)
            exch_ts = exchange_timestamp or now

            # Capture market snapshot at exit
            exit_snapshot = self.snapshot_service.capture_now(trade.pair)

            # Compute PnL
            if trade.side == "LONG":
                pnl = (exit_price - trade.entry_price) * trade.position_size - fees_paid
                pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
            else:
                pnl = (trade.entry_price - exit_price) * trade.position_size - fees_paid
                pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100

            # Compute exit slippage
            exit_slippage_bps = None
            if expected_exit_price and expected_exit_price > 0:
                exit_slippage_bps = abs(exit_price - expected_exit_price) / expected_exit_price * 10000

            # Update the trade event
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

            # Update event metadata for exit
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
        """Return all currently open trades."""
        return dict(self._open_trades)

    def _write_event(self, trade: TradeEvent):
        """Append trade event to daily JSONL file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = self.data_dir / f"trades_{today}.jsonl"
        with open(filepath, "a") as f:
            f.write(json.dumps(trade.to_dict(), default=str) + "\n")

    def _write_error(self, method: str, trade_id: str, error: Exception):
        """Log instrumentation errors without crashing."""
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
```

### Step 2: Hook into the bot's entry logic

From your audit report, you identified the entry function(s). You need to call `logger.log_entry()` immediately after a fill is confirmed.

**Pattern A: If the bot has a clear `execute_entry()` function:**

```python
# BEFORE (existing code):
def execute_entry(self, signal):
    order = self.exchange.create_order(signal.pair, signal.side, signal.size, signal.price)
    fill = self.wait_for_fill(order)
    self.positions[fill.id] = fill
    return fill

# AFTER (with instrumentation wrapper):
def execute_entry(self, signal):
    signal_time = datetime.now(timezone.utc)
    order = self.exchange.create_order(signal.pair, signal.side, signal.size, signal.price)
    fill = self.wait_for_fill(order)
    self.positions[fill.id] = fill

    # --- INSTRUMENTATION (fault-tolerant) ---
    try:
        self.trade_logger.log_entry(
            trade_id=str(fill.id),
            pair=signal.pair,
            side=signal.side,
            entry_price=fill.price,
            position_size=fill.amount,
            position_size_quote=fill.price * fill.amount,
            entry_signal=signal.description,            # ADAPT: your signal's description
            entry_signal_id=signal.signal_type,         # ADAPT: your signal's type ID
            entry_signal_strength=signal.strength,      # ADAPT: or compute a proxy
            active_filters=self.get_active_filter_names(),  # ADAPT
            passed_filters=self.get_passed_filter_names(),  # ADAPT
            strategy_params=self.get_strategy_params(),     # ADAPT
            exchange_timestamp=fill.timestamp,
            expected_entry_price=signal.price_at_generation,
            entry_latency_ms=int((fill.timestamp - signal_time).total_seconds() * 1000),
            market_regime=self.regime_classifier.current_regime(signal.pair),  # Task 8
        )
    except Exception:
        pass  # instrumentation must never block trading
    # --- END INSTRUMENTATION ---

    return fill
```

**Pattern B: If entry logic is spread across multiple functions:**

Create a dedicated wrapper that collects data from multiple points:

```python
class InstrumentedEntryTracker:
    """Collects entry data across multiple function calls."""

    def __init__(self, trade_logger):
        self.trade_logger = trade_logger
        self._pending = {}

    def on_signal_generated(self, signal):
        """Call when a signal fires (before filters)."""
        self._pending[signal.id] = {
            "signal_time": datetime.now(timezone.utc),
            "signal": signal,
            "filters_active": [],
            "filters_passed": [],
        }

    def on_filter_checked(self, signal_id: str, filter_name: str, passed: bool):
        """Call each time a filter is evaluated."""
        if signal_id in self._pending:
            self._pending[signal_id]["filters_active"].append(filter_name)
            if passed:
                self._pending[signal_id]["filters_passed"].append(filter_name)

    def on_fill_received(self, signal_id: str, fill):
        """Call when the exchange confirms the fill."""
        pending = self._pending.pop(signal_id, None)
        if pending is None:
            return

        signal = pending["signal"]
        self.trade_logger.log_entry(
            trade_id=str(fill.id),
            pair=signal.pair,
            side=signal.side,
            entry_price=fill.price,
            position_size=fill.amount,
            position_size_quote=fill.price * fill.amount,
            entry_signal=signal.description,
            entry_signal_id=signal.signal_type,
            entry_signal_strength=getattr(signal, 'strength', 0.5),
            active_filters=pending["filters_active"],
            passed_filters=pending["filters_passed"],
            strategy_params=signal.strategy_params or {},
            exchange_timestamp=fill.timestamp,
            expected_entry_price=signal.price_at_generation,
            entry_latency_ms=int((fill.timestamp - pending["signal_time"]).total_seconds() * 1000),
        )
```

### Step 3: Hook into the bot's exit logic

Same pattern as entry. Call `logger.log_exit()` after exit fill is confirmed.

```python
# At the point where exit is confirmed:
try:
    self.trade_logger.log_exit(
        trade_id=str(position.trade_id),
        exit_price=exit_fill.price,
        exit_reason=exit_reason,   # "STOP_LOSS", "TAKE_PROFIT", etc.
        fees_paid=total_fees,
        exchange_timestamp=exit_fill.timestamp,
        expected_exit_price=trigger_price,  # the SL/TP price that triggered
        exit_latency_ms=int((exit_fill.timestamp - trigger_time).total_seconds() * 1000),
    )
except Exception:
    pass
```

### Step 4: Handle signal strength

If your bot does not currently have a signal strength concept, create a proxy:

```python
def compute_signal_strength(self, signal) -> float:
    """
    Compute a 0.0–1.0 confidence score for this signal.
    ADAPT: these rules depend entirely on your strategy.

    Example for an EMA cross strategy:
    """
    strength = 0.5  # baseline

    # Stronger if RSI confirms
    if signal.rsi is not None:
        if signal.side == "LONG" and signal.rsi < 40:
            strength += 0.15
        elif signal.side == "SHORT" and signal.rsi > 60:
            strength += 0.15

    # Stronger if volume above average
    if signal.volume_ratio is not None and signal.volume_ratio > 1.5:
        strength += 0.1

    # Stronger if trend aligns with higher timeframe
    if signal.htf_trend_aligned:
        strength += 0.15

    # Weaker if spread is wide
    if signal.spread_bps is not None and signal.spread_bps > 10:
        strength -= 0.1

    return max(0.0, min(1.0, strength))
```

### Step 5: Capture strategy params at entry time

The strategy parameters that were active when the trade was entered. This is essential for WFO analysis — you need to know WHICH param set produced WHICH results.

```python
def get_strategy_params(self) -> dict:
    """
    Return a snapshot of all configurable strategy parameters.
    ADAPT: return whatever params your bot uses.
    """
    return {
        "ema_fast": self.config.ema_fast_period,
        "ema_slow": self.config.ema_slow_period,
        "rsi_period": self.config.rsi_period,
        "rsi_oversold": self.config.rsi_oversold,
        "rsi_overbought": self.config.rsi_overbought,
        "atr_period": self.config.atr_period,
        "sl_atr_multiplier": self.config.sl_atr_mult,
        "tp_atr_multiplier": self.config.tp_atr_mult,
        "max_position_pct": self.config.max_position_pct,
        "volume_filter_threshold": self.config.volume_filter_mult,
        # ... include everything that could change during WFO
    }
```

---

## Integration Points

From your audit report:
1. **Entry function** — wrap with `log_entry()` call
2. **Exit function** — wrap with `log_exit()` call
3. **Fill confirmation** — where to get actual prices and timestamps
4. **Filter chain** — where to capture which filters are active and which passed
5. **Strategy config** — where to read current parameter values

---

## Done Criteria

- [ ] `instrumentation/src/trade_logger.py` exists with `TradeEvent` and `TradeLogger`
- [ ] Entry hook is in place — every trade entry produces a JSONL event
- [ ] Exit hook is in place — every trade exit produces a JSONL event
- [ ] Verify by running the bot briefly: check `instrumentation/data/trades/` for events
- [ ] Events contain all required fields (signal, regime, filters, snapshots)
- [ ] Strategy params are captured at entry time
- [ ] Slippage is computed (entry and exit)
- [ ] Instrumentation failure does not block trades (test by breaking the logger temporarily)
- [ ] Instrumentation errors are logged to `instrumentation/data/errors/`
