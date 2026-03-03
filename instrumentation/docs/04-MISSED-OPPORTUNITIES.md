# Task 4: Implement Missed Opportunity Logger

## Goal

Log every signal that fired but was blocked by a filter or risk limit. Then asynchronously backfill what would have happened if the trade had been taken, using explicit simulation assumptions per strategy.

This is often more valuable than trade data — it tells you whether your filters are helping or hurting.

## Schema

```python
# instrumentation/src/missed_opportunity.py

import json
import hashlib
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict

from .event_metadata import EventMetadata, create_event_metadata
from .market_snapshot import MarketSnapshot, MarketSnapshotService


@dataclass
class SimulationPolicy:
    """
    Defines assumptions for hypothetical outcome calculation.
    Loaded from instrumentation/config/simulation_policies.yaml.
    Must be defined per strategy — different strategies have different TP/SL logic.
    """
    entry_fill_model: str = "mid"         # "mid" | "bid_ask" | "next_trade"
    slippage_model: str = "fixed_bps"     # "fixed_bps" | "spread_proportional" | "empirical"
    slippage_bps: float = 5.0             # used if model is fixed_bps
    fees_included: bool = True
    fee_bps: float = 7.0                  # maker + taker average
    tp_sl_logic: str = "atr_based"        # "fixed_pct" | "atr_based" | "trailing"
    tp_value: float = 2.0                 # multiplier (ATR) or percentage, depends on logic
    sl_value: float = 1.0                 # multiplier (ATR) or percentage
    max_hold_bars: int = 100              # timeout for simulation

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MissedOpportunityEvent:
    """A signal that fired but was not executed."""
    event_metadata: dict
    market_snapshot: dict              # snapshot at signal time

    bot_id: str = ""
    pair: str = ""
    side: str = ""                     # LONG | SHORT
    signal: str = ""                   # human-readable signal description
    signal_id: str = ""                # machine identifier
    signal_strength: float = 0.0
    signal_time: str = ""              # when the signal fired
    blocked_by: str = ""               # which filter or limit blocked it
    block_reason: str = ""             # additional context on why

    hypothetical_entry_price: float = 0.0  # price used for simulation

    # Backfilled outcomes (null until computed)
    outcome_1h: Optional[float] = None     # price 1h after signal
    outcome_4h: Optional[float] = None
    outcome_24h: Optional[float] = None
    outcome_pnl_1h: Optional[float] = None   # hypothetical PnL after 1h
    outcome_pnl_4h: Optional[float] = None
    outcome_pnl_24h: Optional[float] = None
    would_have_hit_tp: Optional[bool] = None
    would_have_hit_sl: Optional[bool] = None
    bars_to_tp: Optional[int] = None       # how many bars until TP hit
    bars_to_sl: Optional[int] = None
    first_hit: Optional[str] = None        # "TP" | "SL" | "TIMEOUT" | "PENDING"

    # Simulation transparency
    simulation_policy: Optional[dict] = None   # which assumptions were used
    simulation_confidence: float = 0.0         # 0–1, how reliable is this
    assumption_tags: List[str] = field(default_factory=list)  # e.g. ["mid_fill", "7bps_fees"]
    backfill_status: str = "pending"           # "pending" | "partial" | "complete" | "failed"

    # Strategy context
    strategy_params_at_signal: Optional[dict] = None
    market_regime: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
```

## Implementation

### Step 1: Create the MissedOpportunityLogger

```python
# instrumentation/src/missed_opportunity.py (continued)

class MissedOpportunityLogger:
    """
    Logs missed opportunities and manages outcome backfill.

    Usage:
        logger = MissedOpportunityLogger(config, snapshot_service)

        # When a signal is blocked:
        logger.log_missed(
            pair="BTC/USDT",
            side="LONG",
            signal="EMA cross bullish",
            signal_id="ema_cross_bull",
            signal_strength=0.75,
            blocked_by="volume_filter",
            block_reason="24h volume 1.2x avg, threshold is 2.0x",
            strategy_params={...},
        )

        # Periodically (or on a timer):
        logger.run_backfill()
    """

    def __init__(self, config: dict, snapshot_service: MarketSnapshotService):
        self.bot_id = config["bot_id"]
        self.data_dir = Path(config["data_dir"]) / "missed"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_service = snapshot_service
        self.data_source_id = config.get("data_source_id", "unknown")

        # Load simulation policies
        self.simulation_policies = self._load_simulation_policies(config)

        # Pending backfills
        self._pending_backfills: List[Dict] = []
        self._backfill_lock = threading.Lock()

    def _load_simulation_policies(self, config: dict) -> Dict[str, SimulationPolicy]:
        """Load per-strategy simulation policies from config."""
        policies = {}
        policy_file = Path("instrumentation/config/simulation_policies.yaml")
        if policy_file.exists():
            import yaml
            with open(policy_file) as f:
                raw = yaml.safe_load(f)
            for name, params in raw.get("simulation_policies", {}).items():
                policies[name] = SimulationPolicy(**params)

        # Default policy if none configured
        if not policies:
            policies["default"] = SimulationPolicy()

        return policies

    def _get_policy(self, strategy_type: str = None) -> SimulationPolicy:
        """Get simulation policy for this strategy, fall back to default."""
        if strategy_type and strategy_type in self.simulation_policies:
            return self.simulation_policies[strategy_type]
        return self.simulation_policies.get("default", SimulationPolicy())

    def _compute_hypothetical_entry(
        self, snapshot: MarketSnapshot, side: str, policy: SimulationPolicy
    ) -> float:
        """Compute the hypothetical entry price based on simulation policy."""
        if policy.entry_fill_model == "mid":
            base_price = snapshot.mid
        elif policy.entry_fill_model == "bid_ask":
            base_price = snapshot.ask if side == "LONG" else snapshot.bid
        elif policy.entry_fill_model == "next_trade":
            base_price = snapshot.last_trade_price
        else:
            base_price = snapshot.mid

        # Apply slippage
        if policy.slippage_model == "fixed_bps":
            slippage = base_price * policy.slippage_bps / 10000
        elif policy.slippage_model == "spread_proportional":
            slippage = (snapshot.ask - snapshot.bid) * 0.5 if snapshot.ask and snapshot.bid else 0
        else:
            slippage = base_price * policy.slippage_bps / 10000

        if side == "LONG":
            return base_price + slippage
        else:
            return base_price - slippage

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
        strategy_type: Optional[str] = None,
        market_regime: str = "",
        exchange_timestamp: Optional[datetime] = None,
        bar_id: Optional[str] = None,
    ) -> MissedOpportunityEvent:
        """
        Call this when a signal fires but is blocked.

        Hook into EACH filter in the bot's filter chain. When a filter returns
        False (blocking the trade), call this method.
        """
        try:
            now = datetime.now(timezone.utc)
            exch_ts = exchange_timestamp or now

            snapshot = self.snapshot_service.capture_now(pair)
            policy = self._get_policy(strategy_type)

            hyp_entry = self._compute_hypothetical_entry(snapshot, side, policy)

            # Build assumption tags for transparency
            assumption_tags = [
                f"{policy.entry_fill_model}_fill",
                f"{policy.slippage_bps}bps_slippage" if policy.slippage_model == "fixed_bps"
                    else f"{policy.slippage_model}_slippage",
            ]
            if policy.fees_included:
                assumption_tags.append(f"{policy.fee_bps}bps_fees")
            else:
                assumption_tags.append("no_fees")
            assumption_tags.append(f"{policy.tp_sl_logic}_tp_sl")

            signal_hash = hashlib.sha256(
                f"{pair}|{side}|{signal_id}|{exch_ts.isoformat()}".encode()
            ).hexdigest()[:12]

            metadata = create_event_metadata(
                bot_id=self.bot_id,
                event_type="missed_opportunity",
                payload_key=signal_hash,
                exchange_timestamp=exch_ts,
                data_source_id=self.data_source_id,
                bar_id=bar_id,
            )

            event = MissedOpportunityEvent(
                event_metadata=metadata.to_dict(),
                market_snapshot=snapshot.to_dict(),
                bot_id=self.bot_id,
                pair=pair,
                side=side,
                signal=signal,
                signal_id=signal_id,
                signal_strength=signal_strength,
                signal_time=exch_ts.isoformat(),
                blocked_by=blocked_by,
                block_reason=block_reason,
                hypothetical_entry_price=hyp_entry,
                simulation_policy=policy.to_dict(),
                assumption_tags=assumption_tags,
                strategy_params_at_signal=strategy_params,
                market_regime=market_regime,
                backfill_status="pending",
            )

            self._write_event(event)

            # Queue for backfill
            with self._backfill_lock:
                self._pending_backfills.append({
                    "event_id": metadata.event_id,
                    "pair": pair,
                    "side": side,
                    "entry_price": hyp_entry,
                    "signal_time": exch_ts,
                    "policy": policy,
                    "snapshot": snapshot,
                    "file_date": now.strftime("%Y-%m-%d"),
                })

            return event

        except Exception as e:
            self._write_error("log_missed", f"{pair}_{signal_id}", e)
            return MissedOpportunityEvent(event_metadata={}, market_snapshot={})

    def run_backfill(self, data_provider):
        """
        Process pending backfills. Call this periodically (e.g., every 5 minutes)
        or after enough time has passed for outcomes to be known.

        Args:
            data_provider: the bot's market data provider (to fetch historical candles)
        """
        now = datetime.now(timezone.utc)
        completed = []

        with self._backfill_lock:
            pending = list(self._pending_backfills)

        for item in pending:
            elapsed = now - item["signal_time"]

            # Need at least 24h of data for full backfill
            if elapsed < timedelta(hours=24):
                # Try partial backfill
                outcomes = self._compute_outcomes(
                    item, data_provider, partial=True, elapsed=elapsed
                )
                if outcomes:
                    self._update_event(item["event_id"], item["file_date"], outcomes, status="partial")
                continue

            # Full backfill
            outcomes = self._compute_outcomes(item, data_provider, partial=False, elapsed=elapsed)
            if outcomes:
                self._update_event(item["event_id"], item["file_date"], outcomes, status="complete")
                completed.append(item)

        # Remove completed backfills
        with self._backfill_lock:
            for c in completed:
                if c in self._pending_backfills:
                    self._pending_backfills.remove(c)

    def _compute_outcomes(
        self, item: dict, data_provider, partial: bool, elapsed: timedelta
    ) -> Optional[dict]:
        """
        Compute hypothetical outcomes using historical candle data.

        ADAPT: replace data_provider calls with your bot's actual API.
        """
        try:
            pair = item["pair"]
            side = item["side"]
            entry_price = item["entry_price"]
            signal_time = item["signal_time"]
            policy = item["policy"]
            snapshot = item["snapshot"]

            # Fetch candles from signal time to now
            # ADAPT: your bot's candle fetching method
            candles = data_provider.get_ohlcv(
                pair, timeframe="5m",
                since=int(signal_time.timestamp() * 1000),
                limit=300  # ~25 hours of 5m candles
            )

            if not candles or len(candles) < 2:
                return None

            # Compute TP/SL prices based on policy
            if policy.tp_sl_logic == "atr_based":
                atr = snapshot.atr_14 or (entry_price * 0.01)  # fallback 1%
                if side == "LONG":
                    tp_price = entry_price + (atr * policy.tp_value)
                    sl_price = entry_price - (atr * policy.sl_value)
                else:
                    tp_price = entry_price - (atr * policy.tp_value)
                    sl_price = entry_price + (atr * policy.sl_value)
            elif policy.tp_sl_logic == "fixed_pct":
                if side == "LONG":
                    tp_price = entry_price * (1 + policy.tp_value / 100)
                    sl_price = entry_price * (1 - policy.sl_value / 100)
                else:
                    tp_price = entry_price * (1 - policy.tp_value / 100)
                    sl_price = entry_price * (1 + policy.sl_value / 100)
            else:
                # Default: use ATR-based with fallback
                atr = snapshot.atr_14 or (entry_price * 0.01)
                if side == "LONG":
                    tp_price = entry_price + (atr * 2)
                    sl_price = entry_price - atr
                else:
                    tp_price = entry_price - (atr * 2)
                    sl_price = entry_price + atr

            # Walk through candles and check TP/SL hits
            would_have_hit_tp = False
            would_have_hit_sl = False
            bars_to_tp = None
            bars_to_sl = None
            first_hit = "TIMEOUT"

            price_1h = None
            price_4h = None
            price_24h = None

            for i, candle in enumerate(candles):
                candle_time = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
                candle_elapsed = candle_time - signal_time
                high = candle[2]
                low = candle[3]
                close = candle[4]

                # Record time-based outcomes
                if candle_elapsed >= timedelta(hours=1) and price_1h is None:
                    price_1h = close
                if candle_elapsed >= timedelta(hours=4) and price_4h is None:
                    price_4h = close
                if candle_elapsed >= timedelta(hours=24) and price_24h is None:
                    price_24h = close

                # Check TP/SL
                if not would_have_hit_tp and not would_have_hit_sl:
                    if side == "LONG":
                        if high >= tp_price:
                            would_have_hit_tp = True
                            bars_to_tp = i
                            if first_hit == "TIMEOUT":
                                first_hit = "TP"
                        if low <= sl_price:
                            would_have_hit_sl = True
                            bars_to_sl = i
                            if first_hit == "TIMEOUT" or (first_hit == "TP" and bars_to_sl <= bars_to_tp):
                                first_hit = "SL"
                    else:
                        if low <= tp_price:
                            would_have_hit_tp = True
                            bars_to_tp = i
                            if first_hit == "TIMEOUT":
                                first_hit = "TP"
                        if high >= sl_price:
                            would_have_hit_sl = True
                            bars_to_sl = i
                            if first_hit == "TIMEOUT" or (first_hit == "TP" and bars_to_sl <= bars_to_tp):
                                first_hit = "SL"

            # Determine which hit first on same bar
            if bars_to_tp is not None and bars_to_sl is not None:
                if bars_to_tp < bars_to_sl:
                    first_hit = "TP"
                elif bars_to_sl < bars_to_tp:
                    first_hit = "SL"
                else:
                    first_hit = "SL"  # conservative: assume SL hit first on same bar

            # Compute PnL outcomes
            fee_factor = policy.fee_bps / 10000 if policy.fees_included else 0

            def compute_pnl(exit_price):
                if exit_price is None:
                    return None
                if side == "LONG":
                    gross = (exit_price - entry_price) / entry_price
                else:
                    gross = (entry_price - exit_price) / entry_price
                return round((gross - 2 * fee_factor) * 100, 4)  # as percentage, fees on entry + exit

            # Confidence based on data availability
            confidence = 0.3  # baseline
            if price_1h is not None:
                confidence += 0.2
            if price_4h is not None:
                confidence += 0.2
            if price_24h is not None:
                confidence += 0.2
            if would_have_hit_tp or would_have_hit_sl:
                confidence += 0.1

            result = {
                "outcome_1h": price_1h,
                "outcome_4h": price_4h,
                "outcome_24h": price_24h,
                "outcome_pnl_1h": compute_pnl(price_1h),
                "outcome_pnl_4h": compute_pnl(price_4h),
                "outcome_pnl_24h": compute_pnl(price_24h),
                "would_have_hit_tp": would_have_hit_tp,
                "would_have_hit_sl": would_have_hit_sl,
                "bars_to_tp": bars_to_tp,
                "bars_to_sl": bars_to_sl,
                "first_hit": first_hit,
                "simulation_confidence": round(confidence, 2),
            }

            return result

        except Exception as e:
            self._write_error("compute_outcomes", item.get("event_id", "unknown"), e)
            return None

    def _update_event(self, event_id: str, file_date: str, outcomes: dict, status: str):
        """Update the existing event in the JSONL file with backfill results."""
        filepath = self.data_dir / f"missed_{file_date}.jsonl"
        if not filepath.exists():
            return

        # Read all events, update the matching one, rewrite
        lines = filepath.read_text().strip().split("\n")
        updated = False
        new_lines = []
        for line in lines:
            try:
                event = json.loads(line)
                if event.get("event_metadata", {}).get("event_id") == event_id:
                    event.update(outcomes)
                    event["backfill_status"] = status
                    updated = True
                new_lines.append(json.dumps(event, default=str))
            except json.JSONDecodeError:
                new_lines.append(line)

        if updated:
            filepath.write_text("\n".join(new_lines) + "\n")

    def _write_event(self, event: MissedOpportunityEvent):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = self.data_dir / f"missed_{today}.jsonl"
        with open(filepath, "a") as f:
            f.write(json.dumps(event.to_dict(), default=str) + "\n")

    def _write_error(self, method: str, context: str, error: Exception):
        error_dir = Path(self.data_dir).parent / "errors"
        error_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = error_dir / f"instrumentation_errors_{today}.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": "missed_opportunity",
            "method": method,
            "context": context,
            "error": str(error),
        }
        with open(filepath, "a") as f:
            f.write(json.dumps(entry) + "\n")
```

### Step 2: Hook into the filter chain

From your audit report, you identified each filter in the bot's filter chain. For EACH filter, add a missed opportunity log call when the filter blocks a trade.

```python
# BEFORE (existing filter check):
def check_filters(self, signal):
    if not self.volume_filter.check(signal):
        return False
    if not self.spread_filter.check(signal):
        return False
    return True

# AFTER (with missed opportunity logging):
def check_filters(self, signal):
    if not self.volume_filter.check(signal):
        try:
            self.missed_logger.log_missed(
                pair=signal.pair,
                side=signal.side,
                signal=signal.description,
                signal_id=signal.signal_type,
                signal_strength=signal.strength,
                blocked_by="volume_filter",
                block_reason=f"Volume ratio {signal.volume_ratio:.2f} below threshold {self.volume_filter.threshold}",
                strategy_params=self.get_strategy_params(),
                strategy_type=self.config.strategy_type,
                market_regime=self.regime_classifier.current_regime(signal.pair),
            )
        except Exception:
            pass
        return False

    if not self.spread_filter.check(signal):
        try:
            self.missed_logger.log_missed(
                pair=signal.pair,
                side=signal.side,
                signal=signal.description,
                signal_id=signal.signal_type,
                signal_strength=signal.strength,
                blocked_by="spread_filter",
                block_reason=f"Spread {signal.spread_bps:.1f}bps above threshold {self.spread_filter.max_bps}bps",
                strategy_params=self.get_strategy_params(),
                strategy_type=self.config.strategy_type,
                market_regime=self.regime_classifier.current_regime(signal.pair),
            )
        except Exception:
            pass
        return False

    return True
```

### Step 3: Schedule the backfill

Add the backfill runner to the bot's periodic tasks:

```python
# In the bot's main loop or scheduler, run every 5 minutes:
if time.time() - last_backfill_time >= 300:
    missed_logger.run_backfill(data_provider=self.exchange)
    last_backfill_time = time.time()
```

### Step 4: Create simulation_policies.yaml

```yaml
# instrumentation/config/simulation_policies.yaml
# ADAPT: define one policy per strategy type used by this bot

simulation_policies:
  # Example for a trend-following strategy
  trend_follow:
    entry_fill_model: next_trade
    slippage_model: spread_proportional
    slippage_bps: 5
    fees_included: true
    fee_bps: 7
    tp_sl_logic: atr_based
    tp_value: 2.0       # 2x ATR take profit
    sl_value: 1.0       # 1x ATR stop loss
    max_hold_bars: 100

  # Example for a mean-reversion strategy
  mean_reversion:
    entry_fill_model: bid_ask
    slippage_model: fixed_bps
    slippage_bps: 3
    fees_included: true
    fee_bps: 5
    tp_sl_logic: fixed_pct
    tp_value: 0.8       # 0.8% take profit
    sl_value: 0.5       # 0.5% stop loss
    max_hold_bars: 50

  # Fallback
  default:
    entry_fill_model: mid
    slippage_model: fixed_bps
    slippage_bps: 5
    fees_included: true
    fee_bps: 7
    tp_sl_logic: atr_based
    tp_value: 2.0
    sl_value: 1.0
    max_hold_bars: 100
```

---

## Done Criteria

- [ ] `instrumentation/src/missed_opportunity.py` exists
- [ ] `instrumentation/config/simulation_policies.yaml` exists with policies matching this bot's strategies
- [ ] Every filter in the filter chain logs a missed opportunity when it blocks
- [ ] Missed events appear in `instrumentation/data/missed/`
- [ ] Each event includes `simulation_policy`, `assumption_tags`, and `hypothetical_entry_price`
- [ ] Backfill runs and updates events with outcomes (verify after 1+ hours)
- [ ] Partial backfill works (1h outcome filled before 4h and 24h)
- [ ] `simulation_confidence` is set appropriately
- [ ] Failure in logging does not block the filter chain
