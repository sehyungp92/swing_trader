"""Tests for TradeLogger."""
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock

from instrumentation.src.trade_logger import TradeLogger
from instrumentation.src.market_snapshot import MarketSnapshotService, MarketSnapshot


class TestTradeLogger:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "bot_id": "test_bot",
            "data_dir": self.tmpdir,
            "data_source_id": "test",
            "market_snapshots": {"interval_seconds": 60, "symbols": []},
        }
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
        assert trade.pnl == 950.0
        assert trade.stage == "exit"

    def test_log_exit_short_pnl(self):
        self.logger.log_entry(
            trade_id="t2", pair="BTC/USDT", side="SHORT",
            entry_price=50000, position_size=1.0, position_size_quote=50000,
            entry_signal="test", entry_signal_id="test",
            entry_signal_strength=0.5, active_filters=[], passed_filters=[],
            strategy_params={},
        )
        trade = self.logger.log_exit(
            trade_id="t2", exit_price=49000, exit_reason="TAKE_PROFIT", fees_paid=50,
        )
        assert trade is not None
        assert trade.pnl == 950.0  # (50000-49000)*1.0 - 50

    def test_log_exit_missing_trade_returns_none(self):
        result = self.logger.log_exit(
            trade_id="nonexistent", exit_price=51000, exit_reason="SIGNAL",
        )
        assert result is None

    def test_entry_failure_does_not_crash(self):
        self.snap_service.capture_now.side_effect = Exception("broken")
        trade = self.logger.log_entry(
            trade_id="t1", pair="BTC/USDT", side="LONG",
            entry_price=50000, position_size=1.0, position_size_quote=50000,
            entry_signal="test", entry_signal_id="test",
            entry_signal_strength=0.5, active_filters=[], passed_filters=[],
            strategy_params={},
        )
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

    def test_slippage_computed(self):
        trade = self.logger.log_entry(
            trade_id="t1", pair="BTC/USDT", side="LONG",
            entry_price=50010, position_size=1.0, position_size_quote=50010,
            entry_signal="test", entry_signal_id="test",
            entry_signal_strength=0.5, active_filters=[], passed_filters=[],
            strategy_params={},
            expected_entry_price=50000,
        )
        assert trade.entry_slippage_bps is not None
        assert trade.entry_slippage_bps > 0

    def test_get_open_trades(self):
        self.logger.log_entry(
            trade_id="t1", pair="BTC/USDT", side="LONG",
            entry_price=50000, position_size=1.0, position_size_quote=50000,
            entry_signal="test", entry_signal_id="test",
            entry_signal_strength=0.5, active_filters=[], passed_filters=[],
            strategy_params={},
        )
        open_trades = self.logger.get_open_trades()
        assert "t1" in open_trades

    def test_strategy_id_captured(self):
        trade = self.logger.log_entry(
            trade_id="t1", pair="QQQ", side="LONG",
            entry_price=500, position_size=10, position_size_quote=5000,
            entry_signal="pullback", entry_signal_id="pullback_signal",
            entry_signal_strength=0.7, active_filters=[], passed_filters=[],
            strategy_params={}, strategy_id="ATRSS",
        )
        assert trade.strategy_id == "ATRSS"
