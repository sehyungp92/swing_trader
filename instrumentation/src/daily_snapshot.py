"""Daily Aggregate Snapshots — end-of-day rollup computed locally.

Reads today's trade events, missed opportunities, and process scores,
then computes the daily aggregate for the central analysis system.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("instrumentation.daily_snapshot")


@dataclass
class DailySnapshot:
    """End-of-day aggregate for a single bot."""
    date: str
    bot_id: str
    strategy_type: str

    # Trade counts
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    breakeven_count: int = 0

    # PnL
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    total_fees: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0

    # Risk
    max_drawdown_pct: float = 0.0
    max_exposure: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    exposure_pct: float = 0.0

    # Rolling metrics
    sharpe_rolling_30d: Optional[float] = None
    sortino_rolling_30d: Optional[float] = None
    calmar_rolling_30d: Optional[float] = None

    # Missed opportunities
    missed_count: int = 0
    missed_would_have_won: int = 0
    missed_potential_pnl: float = 0.0
    top_missed_filter: str = ""

    # Process quality
    avg_process_quality: float = 0.0
    process_scores_distribution: Dict[str, int] = field(default_factory=dict)
    root_cause_distribution: Dict[str, int] = field(default_factory=dict)

    # Regime breakdown
    regime_breakdown: Dict[str, dict] = field(default_factory=dict)

    # Excursion & efficiency aggregates (Task 19)
    avg_mfe_pct: Optional[float] = None
    avg_mae_pct: Optional[float] = None
    avg_exit_efficiency: Optional[float] = None
    session_breakdown: Dict[str, dict] = field(default_factory=dict)

    # Execution quality
    avg_entry_slippage_bps: Optional[float] = None
    avg_exit_slippage_bps: Optional[float] = None
    avg_entry_latency_ms: Optional[float] = None

    # Health
    error_count: int = 0
    uptime_pct: float = 100.0
    data_gaps: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class DailySnapshotBuilder:
    """Reads today's events and computes the daily aggregate.

    Usage::

        builder = DailySnapshotBuilder(config)
        snapshot = builder.build(date_str="2026-03-01")
        builder.save(snapshot)
    """

    def __init__(self, config: dict):
        self.bot_id = config["bot_id"]
        self.strategy_type = config.get("strategy_type", "multi_strategy")
        self.data_dir = Path(config["data_dir"])

    def build(self, date_str: str = None) -> DailySnapshot:
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        trades = self._load_trades(date_str)
        missed = self._load_missed(date_str)
        scores = self._load_scores(date_str)
        errors = self._load_errors(date_str)

        snapshot = DailySnapshot(
            date=date_str,
            bot_id=self.bot_id,
            strategy_type=self.strategy_type,
        )

        # --- TRADE AGGREGATES ---
        completed = [t for t in trades if t.get("stage") == "exit" and t.get("pnl") is not None]
        snapshot.total_trades = len(completed)

        if completed:
            pnls = [t["pnl"] for t in completed]
            fees = [t.get("fees_paid", 0) or 0 for t in completed]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]

            snapshot.win_count = len(wins)
            snapshot.loss_count = len(losses)
            snapshot.breakeven_count = len([p for p in pnls if p == 0])
            snapshot.gross_pnl = round(sum(pnls) + sum(fees), 4)
            snapshot.net_pnl = round(sum(pnls), 4)
            snapshot.total_fees = round(sum(fees), 4)
            snapshot.best_trade_pnl = round(max(pnls), 4)
            snapshot.worst_trade_pnl = round(min(pnls), 4)
            snapshot.avg_win = round(sum(wins) / len(wins), 4) if wins else 0
            snapshot.avg_loss = round(sum(losses) / len(losses), 4) if losses else 0
            snapshot.win_rate = round(len(wins) / len(completed), 4)

            gross_wins = sum(wins) if wins else 0
            gross_losses = abs(sum(losses)) if losses else 0
            snapshot.profit_factor = round(gross_wins / gross_losses, 4) if gross_losses > 0 else float("inf")

            # Slippage averages
            entry_slips = [t.get("entry_slippage_bps") for t in completed if t.get("entry_slippage_bps") is not None]
            exit_slips = [t.get("exit_slippage_bps") for t in completed if t.get("exit_slippage_bps") is not None]
            latencies = [t.get("entry_latency_ms") for t in completed if t.get("entry_latency_ms") is not None]

            snapshot.avg_entry_slippage_bps = round(sum(entry_slips) / len(entry_slips), 2) if entry_slips else None
            snapshot.avg_exit_slippage_bps = round(sum(exit_slips) / len(exit_slips), 2) if exit_slips else None
            snapshot.avg_entry_latency_ms = round(sum(latencies) / len(latencies), 1) if latencies else None

            # Regime breakdown
            regime_data: Dict[str, dict] = {}
            for t in completed:
                regime = t.get("market_regime", "unknown")
                if regime not in regime_data:
                    regime_data[regime] = {"trades": 0, "pnl": 0.0, "wins": 0}
                regime_data[regime]["trades"] += 1
                regime_data[regime]["pnl"] += t["pnl"]
                if t["pnl"] > 0:
                    regime_data[regime]["wins"] += 1
            for data in regime_data.values():
                data["pnl"] = round(data["pnl"], 4)
                data["win_rate"] = round(data["wins"] / data["trades"], 4) if data["trades"] > 0 else 0
            snapshot.regime_breakdown = regime_data

            # Excursion & efficiency aggregates
            mfe_pcts = [t.get("mfe_pct") for t in completed if t.get("mfe_pct") is not None]
            mae_pcts = [t.get("mae_pct") for t in completed if t.get("mae_pct") is not None]
            efficiencies = [t.get("exit_efficiency") for t in completed if t.get("exit_efficiency") is not None]

            snapshot.avg_mfe_pct = round(sum(mfe_pcts) / len(mfe_pcts), 6) if mfe_pcts else None
            snapshot.avg_mae_pct = round(sum(mae_pcts) / len(mae_pcts), 6) if mae_pcts else None
            snapshot.avg_exit_efficiency = round(sum(efficiencies) / len(efficiencies), 4) if efficiencies else None

            # Session breakdown
            session_data: Dict[str, dict] = {}
            for t in completed:
                session = t.get("market_session", "unknown")
                if session not in session_data:
                    session_data[session] = {"trades": 0, "pnl": 0.0, "wins": 0}
                session_data[session]["trades"] += 1
                session_data[session]["pnl"] += t["pnl"]
                if t["pnl"] > 0:
                    session_data[session]["wins"] += 1
            for data in session_data.values():
                data["pnl"] = round(data["pnl"], 4)
                data["win_rate"] = round(data["wins"] / data["trades"], 4) if data["trades"] > 0 else 0
            snapshot.session_breakdown = session_data

        # --- MISSED OPPORTUNITIES ---
        snapshot.missed_count = len(missed)
        missed_winners = [m for m in missed if m.get("first_hit") == "TP"]
        snapshot.missed_would_have_won = len(missed_winners)

        if missed:
            filter_win_counts: Counter = Counter()
            for m in missed_winners:
                filter_win_counts[m.get("blocked_by", "unknown")] += 1
            if filter_win_counts:
                snapshot.top_missed_filter = filter_win_counts.most_common(1)[0][0]

        # --- PROCESS QUALITY ---
        if scores:
            quality_scores = [s.get("process_quality_score", 50) for s in scores]
            snapshot.avg_process_quality = round(sum(quality_scores) / len(quality_scores), 1)

            classifications = Counter(s.get("classification", "neutral") for s in scores)
            snapshot.process_scores_distribution = dict(classifications)

            all_causes: List[str] = []
            for s in scores:
                all_causes.extend(s.get("root_causes", []))
            snapshot.root_cause_distribution = dict(Counter(all_causes))

        # --- ERRORS ---
        snapshot.error_count = len(errors)

        return snapshot

    def save(self, snapshot: DailySnapshot) -> None:
        daily_dir = self.data_dir / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        filepath = daily_dir / f"daily_{snapshot.date}.json"
        with open(filepath, "w") as f:
            json.dump(snapshot.to_dict(), f, indent=2, default=str)

    def _load_jsonl(self, directory: str, prefix: str, date_str: str) -> list:
        filepath = self.data_dir / directory / f"{prefix}_{date_str}.jsonl"
        if not filepath.exists():
            return []
        events = []
        for line in filepath.read_text().strip().split("\n"):
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events

    def _load_trades(self, date_str: str) -> list:
        return self._load_jsonl("trades", "trades", date_str)

    def _load_missed(self, date_str: str) -> list:
        return self._load_jsonl("missed", "missed", date_str)

    def _load_scores(self, date_str: str) -> list:
        return self._load_jsonl("scores", "scores", date_str)

    def _load_errors(self, date_str: str) -> list:
        return self._load_jsonl("errors", "instrumentation_errors", date_str)
