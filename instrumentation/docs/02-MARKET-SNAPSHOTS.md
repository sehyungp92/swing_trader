# Task 2: Implement Market Snapshot Service

## Goal

Create a lightweight service that captures and stores the state of the market at regular intervals and on-demand. Every trade event and missed opportunity event will reference a market snapshot, providing consistent ground-truth context for analysis.

This solves a critical problem: if different events capture market data at slightly different times or from different sources, downstream analysis produces inconsistent results ("analysis hallucinations").

## Schema

```python
# instrumentation/src/event_metadata.py

import hashlib
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class EventMetadata:
    """Attached to every event emitted by this bot."""
    event_id: str                    # deterministic hash (see compute_event_id)
    bot_id: str                      # from instrumentation_config.yaml
    exchange_timestamp: str          # ISO 8601, from exchange/broker
    local_timestamp: str             # ISO 8601, from this machine's clock
    clock_skew_ms: int               # exchange_ts - local_ts in milliseconds
    data_source_id: str              # e.g. "binance_futures_ws", "bybit_perp_rest"
    bar_id: Optional[str] = None     # candle open time, e.g. "2026-03-01T14:00Z_5m"

    def to_dict(self) -> dict:
        return asdict(self)


def compute_event_id(bot_id: str, timestamp: str, event_type: str, payload_key: str) -> str:
    """
    Deterministic event ID. Guarantees idempotency at every layer.

    Args:
        bot_id: this bot's unique identifier
        timestamp: exchange timestamp as ISO string
        event_type: "trade" | "missed_opportunity" | "error" | "snapshot" | "daily"
        payload_key: unique key within event type (e.g. trade_id, signal hash)

    Returns:
        16-character hex hash
    """
    raw = f"{bot_id}|{timestamp}|{event_type}|{payload_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_clock_skew(exchange_ts: datetime, local_ts: datetime) -> int:
    """Returns estimated clock skew in milliseconds."""
    delta = exchange_ts - local_ts
    return int(delta.total_seconds() * 1000)


def create_event_metadata(
    bot_id: str,
    event_type: str,
    payload_key: str,
    exchange_timestamp: datetime,
    data_source_id: str,
    bar_id: Optional[str] = None,
) -> EventMetadata:
    """Factory function. Call this for every event you emit."""
    local_now = datetime.now(timezone.utc)
    exchange_ts_str = exchange_timestamp.isoformat()
    local_ts_str = local_now.isoformat()

    return EventMetadata(
        event_id=compute_event_id(bot_id, exchange_ts_str, event_type, payload_key),
        bot_id=bot_id,
        exchange_timestamp=exchange_ts_str,
        local_timestamp=local_ts_str,
        clock_skew_ms=compute_clock_skew(exchange_timestamp, local_now),
        data_source_id=data_source_id,
        bar_id=bar_id,
    )
```

```python
# instrumentation/src/market_snapshot.py

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class MarketSnapshot:
    """
    Point-in-time capture of market state for a single symbol.
    Referenced by trade events and missed opportunity events.
    """
    snapshot_id: str              # deterministic: hash(symbol + timestamp)
    symbol: str                   # e.g. "BTC/USDT"
    timestamp: str                # exchange time, ISO 8601
    bid: float
    ask: float
    mid: float                    # (bid + ask) / 2
    spread_bps: float             # (ask - bid) / mid * 10000
    last_trade_price: float
    volume_1m: Optional[float] = None     # last 1 minute volume
    volume_5m: Optional[float] = None     # last 5 minute volume
    volume_24h: Optional[float] = None
    atr_14: Optional[float] = None        # 14-period ATR on bot's timeframe
    funding_rate: Optional[float] = None  # for perps, null for spot
    open_interest: Optional[float] = None # for perps, null for spot
    mark_price: Optional[float] = None    # for perps

    def to_dict(self) -> dict:
        return asdict(self)
```

## Implementation

### Step 1: Create the snapshot capture function

Find where this bot accesses market data (identified in your audit report, Data Sources section). Create a function that reads the current state and returns a `MarketSnapshot`.

```python
# instrumentation/src/market_snapshot.py (continued)

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict


class MarketSnapshotService:
    """
    Captures and stores market snapshots.

    Usage:
        service = MarketSnapshotService(config, data_provider)
        service.start()   # begins periodic capture
        snapshot = service.capture_now("BTC/USDT")  # on-demand for trade events
    """

    def __init__(self, config: dict, data_provider):
        """
        Args:
            config: from instrumentation_config.yaml
            data_provider: the bot's existing market data object
                Must support:
                  - get_ticker(symbol) -> {bid, ask, last, ...}
                  - get_ohlcv(symbol, timeframe, limit) -> [[ts, o, h, l, c, v], ...]
                Or equivalent. Adapt the capture method to your bot's data API.
        """
        self.bot_id = config["bot_id"]
        self.data_dir = Path(config["data_dir"]) / "snapshots"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.interval = config.get("market_snapshots", {}).get("interval_seconds", 60)
        self.symbols = config.get("market_snapshots", {}).get("symbols", [])
        self.data_provider = data_provider
        self.data_source_id = ""  # set during init based on provider type
        self._cache: Dict[str, MarketSnapshot] = {}

    def _compute_snapshot_id(self, symbol: str, timestamp: str) -> str:
        raw = f"{symbol}|{timestamp}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def capture_now(self, symbol: str) -> MarketSnapshot:
        """
        Capture a snapshot immediately. Call this when a trade or signal occurs.
        Returns the snapshot and caches it.

        IMPORTANT: Adapt the data_provider calls below to match your bot's
        actual market data API. The method names here are illustrative.
        """
        try:
            # --- ADAPT THIS BLOCK TO YOUR BOT'S DATA API ---
            ticker = self.data_provider.get_ticker(symbol)
            bid = float(ticker.get("bid", 0))
            ask = float(ticker.get("ask", 0))
            last = float(ticker.get("last", 0))
            volume_24h = float(ticker.get("quoteVolume", 0) or ticker.get("volume", 0))

            mid = (bid + ask) / 2 if bid and ask else last
            spread_bps = ((ask - bid) / mid * 10000) if mid > 0 and bid > 0 and ask > 0 else 0

            # Funding rate (perps only)
            funding_rate = None
            try:
                funding = self.data_provider.get_funding_rate(symbol)
                funding_rate = float(funding) if funding is not None else None
            except (AttributeError, Exception):
                pass  # not available or not a perps exchange

            # Open interest (perps only)
            open_interest = None
            try:
                oi = self.data_provider.get_open_interest(symbol)
                open_interest = float(oi) if oi is not None else None
            except (AttributeError, Exception):
                pass

            # ATR (from bot's candle data)
            atr_14 = None
            try:
                atr_14 = self._compute_atr(symbol, period=14)
            except Exception:
                pass

            # Volume 1m/5m (if available from recent candles)
            volume_1m = None
            volume_5m = None
            try:
                volume_1m, volume_5m = self._compute_recent_volume(symbol)
            except Exception:
                pass
            # --- END ADAPT BLOCK ---

            now = datetime.now(timezone.utc)
            ts_str = now.isoformat()

            snapshot = MarketSnapshot(
                snapshot_id=self._compute_snapshot_id(symbol, ts_str),
                symbol=symbol,
                timestamp=ts_str,
                bid=bid,
                ask=ask,
                mid=mid,
                spread_bps=round(spread_bps, 2),
                last_trade_price=last,
                volume_1m=volume_1m,
                volume_5m=volume_5m,
                volume_24h=volume_24h,
                atr_14=atr_14,
                funding_rate=funding_rate,
                open_interest=open_interest,
            )

            self._cache[symbol] = snapshot
            self._write_snapshot(snapshot)
            return snapshot

        except Exception as e:
            # CRITICAL: snapshot failure must never block trading
            # Return a degraded snapshot with what we have
            now = datetime.now(timezone.utc)
            ts_str = now.isoformat()
            degraded = MarketSnapshot(
                snapshot_id=self._compute_snapshot_id(symbol, ts_str),
                symbol=symbol,
                timestamp=ts_str,
                bid=0, ask=0, mid=0, spread_bps=0,
                last_trade_price=0,
            )
            self._write_snapshot(degraded)
            return degraded

    def get_latest(self, symbol: str) -> Optional[MarketSnapshot]:
        """Return the most recent cached snapshot for a symbol."""
        return self._cache.get(symbol)

    def _write_snapshot(self, snapshot: MarketSnapshot):
        """Append snapshot to daily JSONL file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = self.data_dir / f"snapshots_{today}.jsonl"
        with open(filepath, "a") as f:
            f.write(json.dumps(snapshot.to_dict()) + "\n")

    def _compute_atr(self, symbol: str, period: int = 14) -> Optional[float]:
        """
        Compute ATR from the bot's candle data.
        ADAPT: replace with your bot's actual candle access method.
        """
        # Example using ccxt-style OHLCV
        candles = self.data_provider.get_ohlcv(symbol, timeframe="1h", limit=period + 1)
        if not candles or len(candles) < period + 1:
            return None

        trs = []
        for i in range(1, len(candles)):
            high = candles[i][2]
            low = candles[i][3]
            prev_close = candles[i-1][4]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)

        return sum(trs[-period:]) / period if trs else None

    def _compute_recent_volume(self, symbol: str):
        """
        Compute 1m and 5m volume from recent candles.
        ADAPT: replace with your bot's actual candle access method.
        """
        candles_1m = self.data_provider.get_ohlcv(symbol, timeframe="1m", limit=5)
        volume_1m = float(candles_1m[-1][5]) if candles_1m else None
        volume_5m = sum(float(c[5]) for c in candles_1m[-5:]) if candles_1m and len(candles_1m) >= 5 else None
        return volume_1m, volume_5m

    def run_periodic(self):
        """
        Call this from your bot's main loop or schedule it.
        Captures snapshots for all configured symbols.
        """
        for symbol in self.symbols:
            self.capture_now(symbol)

    def cleanup_old_files(self, max_age_days: int = 30):
        """Delete snapshot files older than max_age_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        for filepath in self.data_dir.glob("snapshots_*.jsonl"):
            try:
                date_str = filepath.stem.replace("snapshots_", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff:
                    filepath.unlink()
            except (ValueError, OSError):
                pass
```

### Step 2: Integrate with the bot's main loop

Find the bot's main loop or event handler (from your audit report, Architecture Pattern section).

**For polling-loop bots:**
```python
# In the bot's main loop, add:
# (run every self.snapshot_service.interval seconds)

if time.time() - last_snapshot_time >= snapshot_service.interval:
    snapshot_service.run_periodic()
    last_snapshot_time = time.time()
```

**For event-driven bots:**
```python
# Schedule periodic snapshots alongside the event listener
import asyncio

async def snapshot_loop(snapshot_service):
    while True:
        snapshot_service.run_periodic()
        await asyncio.sleep(snapshot_service.interval)

# Add to event loop startup
asyncio.create_task(snapshot_loop(snapshot_service))
```

**For on-demand capture (at trade time):**
```python
# When a trade entry signal fires, capture a snapshot BEFORE placing the order:
entry_snapshot = snapshot_service.capture_now(symbol)
# Pass entry_snapshot to the trade logger (Task 3)
```

### Step 3: Auto-populate symbols

During initialization, read the bot's configured trading pairs and set them in the snapshot service:

```python
# During bot startup
symbols = bot.get_active_symbols()  # ADAPT to your bot's method
config["market_snapshots"]["symbols"] = symbols
snapshot_service = MarketSnapshotService(config, bot.data_provider)
```

---

## Integration Points

From your audit report, you should have identified:

1. **Data provider object** — the bot's existing market data accessor. Pass this to `MarketSnapshotService.__init__()`.
2. **Main loop / event handler** — where to schedule periodic captures.
3. **Entry/exit functions** — where to call `capture_now()` on-demand.

**ADAPT blocks:** Every method in `MarketSnapshotService` that accesses market data has an `# ADAPT` comment. You must replace these with calls to this specific bot's actual data API. The examples assume ccxt-style methods; adjust for whatever library this bot uses.

---

## Done Criteria

- [ ] `instrumentation/src/event_metadata.py` exists with `EventMetadata`, `compute_event_id`, `create_event_metadata`
- [ ] `instrumentation/src/market_snapshot.py` exists with `MarketSnapshot` and `MarketSnapshotService`
- [ ] All `# ADAPT` blocks have been replaced with this bot's actual data API calls
- [ ] Periodic snapshots are running (verify by checking `instrumentation/data/snapshots/` for a JSONL file)
- [ ] On-demand `capture_now()` works (call it manually and check output)
- [ ] Degraded snapshot is returned on failure (never crashes)
- [ ] Old files are cleaned up (test `cleanup_old_files`)
- [ ] `instrumentation_config.yaml` has been created with correct bot_id, symbols, and data_dir
