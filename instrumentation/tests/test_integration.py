"""Integration test — simulates a full day of trading activity."""
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
    """Simulate: signals, blocked trades, executed trades, daily rollup."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "bot_id": "integration_test",
            "strategy_type": "ATRSS",
            "data_dir": self.tmpdir,
            "data_source_id": "test",
            "market_snapshots": {"interval_seconds": 60, "symbols": ["QQQ"]},
        }

        self.snap_service = MagicMock(spec=MarketSnapshotService)
        self.snap_service.capture_now.return_value = MarketSnapshot(
            snapshot_id="test", symbol="QQQ",
            timestamp=datetime.now(timezone.utc).isoformat(),
            bid=500, ask=500.10, mid=500.05, spread_bps=2.0,
            last_trade_price=500.05, atr_14=5.0, volume_24h=50000000,
        )

        self.trade_logger = TradeLogger(self.config, self.snap_service)
        self.missed_logger = MissedOpportunityLogger(self.config, self.snap_service)

    def test_full_day_lifecycle(self):
        """Simulate: 2 executed trades + 1 missed opportunity + daily snapshot."""

        # Trade 1: winning trade
        self.trade_logger.log_entry(
            trade_id="t1", pair="QQQ", side="LONG",
            entry_price=500, position_size=10, position_size_quote=5000,
            entry_signal="Pullback to EMA", entry_signal_id="pullback_signal",
            entry_signal_strength=0.8, active_filters=["quality_gate", "time_filter"],
            passed_filters=["quality_gate", "time_filter"],
            strategy_params={"ema_fast": 20, "ema_slow": 55},
            strategy_id="ATRSS", market_regime="trending_up",
        )
        self.trade_logger.log_exit(
            trade_id="t1", exit_price=510, exit_reason="TAKE_PROFIT", fees_paid=5,
        )

        # Trade 2: losing trade
        self.trade_logger.log_entry(
            trade_id="t2", pair="QQQ", side="LONG",
            entry_price=510, position_size=10, position_size_quote=5100,
            entry_signal="Breakout pullback", entry_signal_id="breakout_pullback",
            entry_signal_strength=0.6, active_filters=["quality_gate"],
            passed_filters=["quality_gate"],
            strategy_params={"ema_fast": 20},
            strategy_id="ATRSS", market_regime="trending_up",
        )
        self.trade_logger.log_exit(
            trade_id="t2", exit_price=505, exit_reason="STOP_LOSS", fees_paid=5,
        )

        # Missed opportunity
        self.missed_logger.log_missed(
            pair="QQQ", side="LONG",
            signal="Pullback to EMA", signal_id="pullback_signal",
            signal_strength=0.75, blocked_by="quality_gate",
            block_reason="Quality score 3.5 below threshold 4.0",
            strategy_type="ATRSS", strategy_id="ATRSS",
            market_regime="trending_up",
        )

        # Build daily snapshot
        builder = DailySnapshotBuilder(self.config)
        snapshot = builder.build()
        builder.save(snapshot)

        # Verify trade counts
        assert snapshot.total_trades == 2
        assert snapshot.win_count == 1
        assert snapshot.loss_count == 1
        assert snapshot.net_pnl != 0

        # Verify missed opportunity count
        assert snapshot.missed_count == 1

        # Verify files exist
        trade_files = list(Path(self.tmpdir).joinpath("trades").glob("*.jsonl"))
        missed_files = list(Path(self.tmpdir).joinpath("missed").glob("*.jsonl"))
        daily_files = list(Path(self.tmpdir).joinpath("daily").glob("*.json"))

        assert len(trade_files) == 1
        assert len(missed_files) == 1
        assert len(daily_files) == 1

        # Verify trade JSONL has both entries and exits
        trade_lines = trade_files[0].read_text().strip().split("\n")
        assert len(trade_lines) == 4  # 2 entries + 2 exits

        # Verify daily snapshot JSON is valid
        daily_data = json.loads(daily_files[0].read_text())
        assert daily_data["total_trades"] == 2
        assert daily_data["bot_id"] == "integration_test"

    def test_instrumentation_failure_does_not_block(self):
        """Verify that broken instrumentation never prevents trade execution."""
        # Break the snapshot service
        self.snap_service.capture_now.side_effect = Exception("snapshot service down")

        # Entry should still return a trade object (degraded)
        trade = self.trade_logger.log_entry(
            trade_id="t_broken", pair="QQQ", side="LONG",
            entry_price=500, position_size=10, position_size_quote=5000,
            entry_signal="test", entry_signal_id="test",
            entry_signal_strength=0.5, active_filters=[], passed_filters=[],
            strategy_params={},
        )
        assert trade is not None
        assert trade.trade_id == "t_broken"

        # Missed opportunity should still return an event (degraded)
        event = self.missed_logger.log_missed(
            pair="QQQ", side="LONG",
            signal="test", signal_id="test",
            signal_strength=0.5, blocked_by="test_filter",
        )
        assert event is not None

    def test_event_ids_are_unique(self):
        """Every event must have a unique event_id."""
        # Create multiple trades
        for i in range(5):
            self.trade_logger.log_entry(
                trade_id=f"t{i}", pair="QQQ", side="LONG",
                entry_price=500 + i, position_size=10, position_size_quote=5000,
                entry_signal="test", entry_signal_id="test",
                entry_signal_strength=0.5, active_filters=[], passed_filters=[],
                strategy_params={},
            )

        # Read all events and check IDs
        trade_files = list(Path(self.tmpdir).joinpath("trades").glob("*.jsonl"))
        assert len(trade_files) == 1

        event_ids = set()
        for line in trade_files[0].read_text().strip().split("\n"):
            data = json.loads(line)
            eid = data.get("event_metadata", {}).get("event_id")
            if eid:
                assert eid not in event_ids, f"Duplicate event_id: {eid}"
                event_ids.add(eid)
