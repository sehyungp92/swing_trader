# Task 9: Write Tests

## Goal

Unit tests for every instrumentation component plus an integration test that simulates a full trade lifecycle. Tests must be runnable without a live exchange connection.

## Test Structure

All tests go in `instrumentation/tests/`. Use `pytest`.

```bash
pip install pytest pytest-asyncio
```

### Test 1: Event Metadata

```python
# instrumentation/tests/test_event_metadata.py

from instrumentation.src.event_metadata import (
    compute_event_id, create_event_metadata, compute_clock_skew
)
from datetime import datetime, timezone, timedelta


class TestEventMetadata:
    def test_event_id_deterministic(self):
        """Same inputs must always produce the same event_id."""
        id1 = compute_event_id("bot1", "2026-03-01T10:00:00Z", "trade", "abc123")
        id2 = compute_event_id("bot1", "2026-03-01T10:00:00Z", "trade", "abc123")
        assert id1 == id2

    def test_event_id_unique_on_different_input(self):
        """Different inputs must produce different event_ids."""
        id1 = compute_event_id("bot1", "2026-03-01T10:00:00Z", "trade", "abc123")
        id2 = compute_event_id("bot1", "2026-03-01T10:00:01Z", "trade", "abc123")
        assert id1 != id2

    def test_event_id_length(self):
        eid = compute_event_id("bot1", "2026-03-01T10:00:00Z", "trade", "abc")
        assert len(eid) == 16

    def test_clock_skew_positive(self):
        exch = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
        local = datetime(2026, 3, 1, 9, 59, 59, tzinfo=timezone.utc)
        skew = compute_clock_skew(exch, local)
        assert skew == 1000  # exchange is 1 second ahead

    def test_clock_skew_negative(self):
        exch = datetime(2026, 3, 1, 9, 59, 59, tzinfo=timezone.utc)
        local = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
        skew = compute_clock_skew(exch, local)
        assert skew == -1000

    def test_create_event_metadata_returns_all_fields(self):
        now = datetime.now(timezone.utc)
        meta = create_event_metadata(
            bot_id="bot1",
            event_type="trade",
            payload_key="test123",
            exchange_timestamp=now,
            data_source_id="test_source",
        )
        assert meta.event_id
        assert meta.bot_id == "bot1"
        assert meta.exchange_timestamp
        assert meta.local_timestamp
        assert meta.data_source_id == "test_source"
```

### Test 2: Market Snapshot

```python
# instrumentation/tests/test_market_snapshot.py

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from instrumentation.src.market_snapshot import MarketSnapshotService


class MockDataProvider:
    """Mock exchange data for testing."""
    def get_ticker(self, symbol):
        return {"bid": 50000.0, "ask": 50010.0, "last": 50005.0, "quoteVolume": 1000000}

    def get_ohlcv(self, symbol, timeframe="1h", limit=15):
        # Return fake candles with known ATR
        base = 50000
        return [[i * 3600000, base, base + 100, base - 100, base + 50, 1000]
                for i in range(limit)]


class TestMarketSnapshot:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "bot_id": "test_bot",
            "data_dir": self.tmpdir,
            "market_snapshots": {"interval_seconds": 60, "symbols": ["BTC/USDT"]},
        }
        self.service = MarketSnapshotService(self.config, MockDataProvider())

    def test_capture_now_returns_snapshot(self):
        snap = self.service.capture_now("BTC/USDT")
        assert snap.symbol == "BTC/USDT"
        assert snap.bid == 50000.0
        assert snap.ask == 50010.0
        assert snap.mid == 50005.0
        assert snap.spread_bps > 0

    def test_capture_writes_to_file(self):
        self.service.capture_now("BTC/USDT")
        files = list(Path(self.tmpdir).joinpath("snapshots").glob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text().strip()
        data = json.loads(content)
        assert data["symbol"] == "BTC/USDT"

    def test_degraded_snapshot_on_failure(self):
        """Snapshot service must never crash, even with bad data."""
        bad_provider = MagicMock()
        bad_provider.get_ticker.side_effect = Exception("connection lost")
        service = MarketSnapshotService(self.config, bad_provider)
        snap = service.capture_now("BTC/USDT")
        assert snap.symbol == "BTC/USDT"
        assert snap.bid == 0  # degraded
```

### Test 3: Trade Logger

```python
# instrumentation/tests/test_trade_logger.py

import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from instrumentation.src.trade_logger import TradeLogger
from instrumentation.src.market_snapshot import MarketSnapshotService


class TestTradeLogger:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "bot_id": "test_bot",
            "data_dir": self.tmpdir,
            "data_source_id": "test",
            "market_snapshots": {"interval_seconds": 60, "symbols": []},
        }
        # Use a mock snapshot service
        from unittest.mock import MagicMock
        from instrumentation.src.market_snapshot import MarketSnapshot
        self.snap_service = MagicMock(spec=MarketSnapshotService)
        self.snap_service.capture_now.return_value = MarketSnapshot(
            snapshot_id="test", symbol="BTC/USDT", timestamp="2026-03-01T10:00:00Z",
            bid=50000, ask=50010, mid=50005, spread_bps=2.0, last_trade_price=50005,
            atr_14=500, volume_24h=1000000,
        )
        self.logger = TradeLogger(self.config, self.snap_service)

    def test_log_entry_creates_event(self):
        trade = self.logger.log_entry(
            trade_id="t1", pair="BTC/USDT", side="LONG",
            entry_price=50005, position_size=0.1, position_size_quote=5000.5,
            entry_signal="EMA cross", entry_signal_id="ema_cross",
            entry_signal_strength=0.8, active_filters=["volume"],
            passed_filters=["volume"], strategy_params={"ema_fast": 12},
        )
        assert trade.trade_id == "t1"
        assert trade.side == "LONG"
        assert trade.stage == "entry"

    def test_log_exit_computes_pnl(self):
        self.logger.log_entry(
            trade_id="t1", pair="BTC/USDT", side="LONG",
            entry_price=50000, position_size=1.0, position_size_quote=50000,
            entry_signal="test", entry_signal_id="test",
            entry_signal_strength=0.5, active_filters=[], passed_filters=[],
            strategy_params={},
        )
        trade = self.logger.log_exit(
            trade_id="t1", exit_price=51000, exit_reason="TAKE_PROFIT", fees_paid=50,
        )
        assert trade is not None
        assert trade.pnl == 950.0  # (51000 - 50000) * 1.0 - 50
        assert trade.stage == "exit"

    def test_log_exit_missing_trade_returns_none(self):
        result = self.logger.log_exit(
            trade_id="nonexistent", exit_price=51000, exit_reason="SIGNAL",
        )
        assert result is None

    def test_entry_failure_does_not_crash(self):
        """Instrumentation failure must never block trading."""
        self.snap_service.capture_now.side_effect = Exception("broken")
        trade = self.logger.log_entry(
            trade_id="t1", pair="BTC/USDT", side="LONG",
            entry_price=50000, position_size=1.0, position_size_quote=50000,
            entry_signal="test", entry_signal_id="test",
            entry_signal_strength=0.5, active_filters=[], passed_filters=[],
            strategy_params={},
        )
        # Should return a minimal trade, not crash
        assert trade.trade_id == "t1"

    def test_events_written_to_jsonl(self):
        self.logger.log_entry(
            trade_id="t1", pair="BTC/USDT", side="LONG",
            entry_price=50000, position_size=1.0, position_size_quote=50000,
            entry_signal="test", entry_signal_id="test",
            entry_signal_strength=0.5, active_filters=[], passed_filters=[],
            strategy_params={},
        )
        files = list(Path(self.tmpdir).joinpath("trades").glob("*.jsonl"))
        assert len(files) == 1
```

### Test 4: Process Scorer

```python
# instrumentation/tests/test_process_scorer.py

import tempfile
import yaml
from pathlib import Path
from instrumentation.src.process_scorer import ProcessScorer, ROOT_CAUSES


class TestProcessScorer:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        rules = {
            "global": {
                "max_entry_latency_ms": 5000,
                "max_slippage_multiplier": 2.0,
                "min_signal_strength": 0.3,
                "strong_signal_threshold": 0.7,
            },
            "strategies": {
                "trend_follow": {
                    "preferred_regimes": ["trending_up", "trending_down"],
                    "adverse_regimes": ["ranging"],
                    "expected_slippage_bps": 5,
                },
            },
        }
        self.rules_path = Path(self.tmpdir) / "rules.yaml"
        with open(self.rules_path, "w") as f:
            yaml.dump(rules, f)
        self.scorer = ProcessScorer(str(self.rules_path))

    def test_perfect_trade_scores_high(self):
        trade = {
            "trade_id": "t1", "market_regime": "trending_up",
            "entry_signal_strength": 0.8, "entry_latency_ms": 100,
            "entry_slippage_bps": 2.0, "exit_slippage_bps": 2.0,
            "exit_reason": "TAKE_PROFIT", "pnl": 500, "pnl_pct": 2.0,
        }
        score = self.scorer.score_trade(trade, "trend_follow")
        assert score.process_quality_score >= 80
        assert "regime_aligned" in score.root_causes
        assert score.classification == "good_process"

    def test_bad_trade_scores_low(self):
        trade = {
            "trade_id": "t2", "market_regime": "ranging",
            "entry_signal_strength": 0.1, "entry_latency_ms": 10000,
            "entry_slippage_bps": 20.0, "exit_slippage_bps": 15.0,
            "exit_reason": "STOP_LOSS", "pnl": -200, "pnl_pct": -1.5,
        }
        score = self.scorer.score_trade(trade, "trend_follow")
        assert score.process_quality_score < 50
        assert "regime_mismatch" in score.root_causes
        assert "weak_signal" in score.root_causes
        assert score.classification == "bad_process"

    def test_normal_loss_tagged_correctly(self):
        """Good process but negative PnL = normal_loss, not bad_process."""
        trade = {
            "trade_id": "t3", "market_regime": "trending_up",
            "entry_signal_strength": 0.8, "entry_latency_ms": 200,
            "entry_slippage_bps": 3.0, "exit_reason": "STOP_LOSS",
            "pnl": -100, "pnl_pct": -0.5,
        }
        score = self.scorer.score_trade(trade, "trend_follow")
        assert score.process_quality_score >= 80
        assert "normal_loss" in score.root_causes
        assert score.classification == "good_process"

    def test_all_root_causes_from_taxonomy(self):
        """No root cause should exist outside the controlled taxonomy."""
        trade = {"trade_id": "t4", "pnl": 0}
        score = self.scorer.score_trade(trade, "trend_follow")
        for cause in score.root_causes:
            assert cause in ROOT_CAUSES, f"'{cause}' not in ROOT_CAUSES taxonomy"
```

### Test 5: Integration Test

```python
# instrumentation/tests/test_integration.py

import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock

from instrumentation.src.market_snapshot import MarketSnapshot, MarketSnapshotService
from instrumentation.src.trade_logger import TradeLogger
from instrumentation.src.missed_opportunity import MissedOpportunityLogger
from instrumentation.src.daily_snapshot import DailySnapshotBuilder


class TestFullLifecycle:
    """Simulate a complete day: signals, blocked trades, executed trades, daily rollup."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "bot_id": "integration_test",
            "strategy_type": "trend_follow",
            "data_dir": self.tmpdir,
            "data_source_id": "test",
            "market_snapshots": {"interval_seconds": 60, "symbols": ["BTC/USDT"]},
        }

        # Mock snapshot service
        self.snap_service = MagicMock(spec=MarketSnapshotService)
        self.snap_service.capture_now.return_value = MarketSnapshot(
            snapshot_id="test", symbol="BTC/USDT",
            timestamp=datetime.now(timezone.utc).isoformat(),
            bid=50000, ask=50010, mid=50005, spread_bps=2.0,
            last_trade_price=50005, atr_14=500, volume_24h=1000000,
        )

        self.trade_logger = TradeLogger(self.config, self.snap_service)
        self.missed_logger = MissedOpportunityLogger(self.config, self.snap_service)

    def test_full_day_lifecycle(self):
        """Simulate: 2 executed trades + 1 missed opportunity + daily snapshot."""

        # Trade 1: winning trade
        self.trade_logger.log_entry(
            trade_id="t1", pair="BTC/USDT", side="LONG",
            entry_price=50000, position_size=0.5, position_size_quote=25000,
            entry_signal="EMA cross bullish", entry_signal_id="ema_cross_bull",
            entry_signal_strength=0.8, active_filters=["volume"],
            passed_filters=["volume"], strategy_params={"ema_fast": 12},
            market_regime="trending_up",
        )
        self.trade_logger.log_exit(
            trade_id="t1", exit_price=51000, exit_reason="TAKE_PROFIT", fees_paid=25,
        )

        # Trade 2: losing trade
        self.trade_logger.log_entry(
            trade_id="t2", pair="BTC/USDT", side="LONG",
            entry_price=51000, position_size=0.5, position_size_quote=25500,
            entry_signal="EMA cross bullish", entry_signal_id="ema_cross_bull",
            entry_signal_strength=0.6, active_filters=["volume"],
            passed_filters=["volume"], strategy_params={"ema_fast": 12},
            market_regime="trending_up",
        )
        self.trade_logger.log_exit(
            trade_id="t2", exit_price=50500, exit_reason="STOP_LOSS", fees_paid=25,
        )

        # Missed opportunity
        self.missed_logger.log_missed(
            pair="BTC/USDT", side="LONG",
            signal="EMA cross bullish", signal_id="ema_cross_bull",
            signal_strength=0.75, blocked_by="volume_filter",
            block_reason="Volume below threshold",
            strategy_type="trend_follow", market_regime="trending_up",
        )

        # Build daily snapshot
        builder = DailySnapshotBuilder(self.config)
        snapshot = builder.build()
        builder.save(snapshot)

        # Verify
        assert snapshot.total_trades == 2
        assert snapshot.win_count == 1
        assert snapshot.loss_count == 1
        assert snapshot.net_pnl != 0  # has actual value
        assert snapshot.missed_count == 1

        # Verify files exist
        trade_files = list(Path(self.tmpdir).joinpath("trades").glob("*.jsonl"))
        missed_files = list(Path(self.tmpdir).joinpath("missed").glob("*.jsonl"))
        daily_files = list(Path(self.tmpdir).joinpath("daily").glob("*.json"))

        assert len(trade_files) == 1
        assert len(missed_files) == 1
        assert len(daily_files) == 1

        # Verify trade JSONL has both entry and exit
        trade_lines = trade_files[0].read_text().strip().split("\n")
        assert len(trade_lines) == 4  # 2 entries + 2 exits

        # Verify daily snapshot JSON is valid
        daily_data = json.loads(daily_files[0].read_text())
        assert daily_data["total_trades"] == 2
```

---

## Running Tests

```bash
cd <bot_root>

# Ensure the instrumentation package is importable
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Run all tests
pytest instrumentation/tests/ -v

# Run a specific test
pytest instrumentation/tests/test_process_scorer.py -v
```

## Done Criteria

- [ ] All test files exist in `instrumentation/tests/`
- [ ] `pytest instrumentation/tests/ -v` passes with 0 failures
- [ ] Integration test covers: entry, exit, missed opportunity, daily snapshot
- [ ] Fault tolerance tested: logger failure doesn't crash
- [ ] Root cause taxonomy enforcement tested
- [ ] Event ID determinism tested
