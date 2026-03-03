"""Tests for DailySnapshotBuilder."""
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from instrumentation.src.daily_snapshot import DailySnapshotBuilder, DailySnapshot


class TestDailySnapshot:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "bot_id": "test_bot",
            "strategy_type": "test_strategy",
            "data_dir": self.tmpdir,
        }
        self.builder = DailySnapshotBuilder(self.config)
        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _write_trades(self, trades):
        trades_dir = Path(self.tmpdir) / "trades"
        trades_dir.mkdir(parents=True, exist_ok=True)
        filepath = trades_dir / f"trades_{self.today}.jsonl"
        with open(filepath, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

    def _write_missed(self, events):
        missed_dir = Path(self.tmpdir) / "missed"
        missed_dir.mkdir(parents=True, exist_ok=True)
        filepath = missed_dir / f"missed_{self.today}.jsonl"
        with open(filepath, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

    def _write_scores(self, scores):
        scores_dir = Path(self.tmpdir) / "scores"
        scores_dir.mkdir(parents=True, exist_ok=True)
        filepath = scores_dir / f"scores_{self.today}.jsonl"
        with open(filepath, "w") as f:
            for s in scores:
                f.write(json.dumps(s) + "\n")

    def test_build_with_trades(self):
        self._write_trades([
            {"stage": "entry", "trade_id": "t1"},
            {"stage": "exit", "trade_id": "t1", "pnl": 100, "fees_paid": 5,
             "market_regime": "trending_up"},
            {"stage": "entry", "trade_id": "t2"},
            {"stage": "exit", "trade_id": "t2", "pnl": -50, "fees_paid": 5,
             "market_regime": "trending_up"},
        ])
        snap = self.builder.build(self.today)
        assert snap.total_trades == 2
        assert snap.win_count == 1
        assert snap.loss_count == 1
        assert snap.net_pnl == 50.0  # 100 + (-50)
        assert snap.win_rate == 0.5

    def test_build_with_no_data(self):
        snap = self.builder.build(self.today)
        assert snap.total_trades == 0
        assert snap.net_pnl == 0
        assert snap.missed_count == 0

    def test_build_with_missed(self):
        self._write_missed([
            {"blocked_by": "volume_filter", "first_hit": "TP"},
            {"blocked_by": "quality_gate", "first_hit": "SL"},
            {"blocked_by": "volume_filter", "first_hit": "TP"},
        ])
        snap = self.builder.build(self.today)
        assert snap.missed_count == 3
        assert snap.missed_would_have_won == 2
        assert snap.top_missed_filter == "volume_filter"

    def test_build_with_scores(self):
        self._write_scores([
            {"process_quality_score": 90, "classification": "good_process",
             "root_causes": ["regime_aligned", "normal_win"]},
            {"process_quality_score": 30, "classification": "bad_process",
             "root_causes": ["regime_mismatch", "weak_signal"]},
        ])
        snap = self.builder.build(self.today)
        assert snap.avg_process_quality == 60.0
        assert snap.process_scores_distribution["good_process"] == 1
        assert snap.process_scores_distribution["bad_process"] == 1

    def test_save_creates_json(self):
        snap = self.builder.build(self.today)
        self.builder.save(snap)
        filepath = Path(self.tmpdir) / "daily" / f"daily_{self.today}.json"
        assert filepath.exists()
        data = json.loads(filepath.read_text())
        assert data["bot_id"] == "test_bot"
        assert data["date"] == self.today

    def test_regime_breakdown(self):
        self._write_trades([
            {"stage": "exit", "trade_id": "t1", "pnl": 100, "market_regime": "trending_up"},
            {"stage": "exit", "trade_id": "t2", "pnl": -50, "market_regime": "trending_up"},
            {"stage": "exit", "trade_id": "t3", "pnl": 200, "market_regime": "volatile"},
        ])
        snap = self.builder.build(self.today)
        assert "trending_up" in snap.regime_breakdown
        assert snap.regime_breakdown["trending_up"]["trades"] == 2
        assert snap.regime_breakdown["volatile"]["trades"] == 1

    def test_profit_factor(self):
        self._write_trades([
            {"stage": "exit", "trade_id": "t1", "pnl": 300, "fees_paid": 0},
            {"stage": "exit", "trade_id": "t2", "pnl": -100, "fees_paid": 0},
        ])
        snap = self.builder.build(self.today)
        assert snap.profit_factor == 3.0
