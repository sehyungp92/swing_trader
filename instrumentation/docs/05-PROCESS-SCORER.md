# Task 5: Implement Process Quality Scorer

## Goal

Create a deterministic rules engine that scores every trade's process quality independent of PnL and tags root causes from a controlled taxonomy. This prevents Claude from narrativizing — it interprets structured labels, not raw data.

**Key principle:** A trade is not bad because it lost money. A trade is bad because the process was wrong. A losing trade with perfect process is a `normal_loss`. A winning trade with bad process is a lucky mistake.

## Schema

```python
# instrumentation/src/process_scorer.py

import json
import yaml
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional, Dict, Tuple


# Controlled root cause taxonomy — these are the ONLY valid tags
ROOT_CAUSES = [
    "regime_mismatch",        # strategy type doesn't fit current regime
    "weak_signal",            # signal strength below threshold
    "strong_signal",          # signal strength well above threshold (positive)
    "late_entry",             # entered >N bars after signal fired
    "early_exit",             # exited before TP/SL hit, leaving money on table
    "premature_stop",         # SL too tight for current volatility
    "slippage_spike",         # execution cost >2x expected
    "good_execution",         # slippage below average (positive)
    "filter_blocked_good",    # filter killed a trade that would have worked
    "filter_saved_bad",       # filter correctly blocked a losing trade
    "risk_cap_hit",           # position rejected by risk limits
    "data_gap",               # missing candles or stale data feed
    "order_reject",           # exchange rejected order
    "latency_spike",          # execution latency >P99
    "correlation_crowding",   # too many bots on same side at same time
    "funding_adverse",        # funding rate working against position
    "funding_favorable",      # funding rate working for position (positive)
    "regime_aligned",         # strategy type matches current regime (positive)
    "normal_loss",            # everything correct, standard statistical loss
    "normal_win",             # everything correct, standard win
    "exceptional_win",        # perfect process + outsized return
]


@dataclass
class ProcessScore:
    """Output of the process quality scorer for a single trade."""
    trade_id: str
    process_quality_score: int              # 0–100
    root_causes: List[str]                  # from ROOT_CAUSES only
    evidence_refs: List[str]                # file:line or field references
    positive_factors: List[str]             # things that went RIGHT
    negative_factors: List[str]             # things that went WRONG
    classification: str                     # "good_process" | "bad_process" | "neutral"

    def to_dict(self) -> dict:
        return asdict(self)
```

## Implementation

### Step 1: Define scoring rules per strategy

Create `instrumentation/config/process_scoring_rules.yaml`:

```yaml
# instrumentation/config/process_scoring_rules.yaml
# ADAPT: every threshold and rule here depends on YOUR strategy

# Global rules (apply to all strategies)
global:
  max_entry_latency_ms: 5000       # alert if entry takes longer
  max_slippage_multiplier: 2.0     # slippage > 2x expected is a spike
  min_signal_strength: 0.3         # below this is "weak_signal"
  strong_signal_threshold: 0.7     # above this is "strong_signal"

# Per-strategy rules
strategies:
  trend_follow:
    preferred_regimes:
      - trending_up
      - trending_down
    adverse_regimes:
      - ranging
    max_hold_bars: 200
    expected_slippage_bps: 5
    min_signal_strength: 0.4
    strong_signal_threshold: 0.75

  mean_reversion:
    preferred_regimes:
      - ranging
    adverse_regimes:
      - trending_up
      - trending_down
    max_hold_bars: 50
    expected_slippage_bps: 3
    min_signal_strength: 0.35
    strong_signal_threshold: 0.7

  # Add your strategies here
  # ADAPT: match the strategy_type values used in your bot
```

### Step 2: Implement the scorer

```python
# instrumentation/src/process_scorer.py (continued)

class ProcessScorer:
    """
    Deterministic rules engine for trade process quality.

    Does NOT use LLMs. Pure rules-based scoring.

    Usage:
        scorer = ProcessScorer("instrumentation/config/process_scoring_rules.yaml")
        score = scorer.score_trade(trade_event, strategy_type="trend_follow")
    """

    def __init__(self, rules_path: str = "instrumentation/config/process_scoring_rules.yaml"):
        with open(rules_path) as f:
            self.rules = yaml.safe_load(f)
        self.global_rules = self.rules.get("global", {})
        self.strategy_rules = self.rules.get("strategies", {})

    def _get_rules(self, strategy_type: str) -> dict:
        """Get merged rules: strategy-specific overrides global."""
        merged = dict(self.global_rules)
        if strategy_type in self.strategy_rules:
            merged.update(self.strategy_rules[strategy_type])
        return merged

    def score_trade(self, trade: dict, strategy_type: str = "default") -> ProcessScore:
        """
        Score a completed trade event (must have exit data).

        Args:
            trade: a TradeEvent.to_dict() — the full trade record
            strategy_type: key into strategy_rules

        Returns:
            ProcessScore with score, root causes, and evidence
        """
        rules = self._get_rules(strategy_type)
        score = 100
        root_causes = []
        evidence = []
        positive = []
        negative = []

        # --- REGIME FIT ---
        regime = trade.get("market_regime", "")
        preferred = rules.get("preferred_regimes", [])
        adverse = rules.get("adverse_regimes", [])

        if regime and adverse and regime in adverse:
            score -= 20
            root_causes.append("regime_mismatch")
            negative.append(f"Regime '{regime}' is adverse for {strategy_type}")
            evidence.append(f"market_regime={regime}, adverse_regimes={adverse}")
        elif regime and preferred and regime in preferred:
            root_causes.append("regime_aligned")
            positive.append(f"Regime '{regime}' is preferred for {strategy_type}")

        # --- SIGNAL STRENGTH ---
        strength = trade.get("entry_signal_strength", 0.5)
        min_strength = rules.get("min_signal_strength", 0.3)
        strong_threshold = rules.get("strong_signal_threshold", 0.7)

        if strength < min_strength:
            score -= 25
            root_causes.append("weak_signal")
            negative.append(f"Signal strength {strength:.2f} below threshold {min_strength}")
            evidence.append(f"entry_signal_strength={strength}")
        elif strength >= strong_threshold:
            root_causes.append("strong_signal")
            positive.append(f"Signal strength {strength:.2f} above strong threshold {strong_threshold}")

        # --- ENTRY LATENCY ---
        latency = trade.get("entry_latency_ms")
        max_latency = rules.get("max_entry_latency_ms", 5000)

        if latency is not None and latency > max_latency:
            score -= 15
            root_causes.append("late_entry")
            negative.append(f"Entry latency {latency}ms exceeds {max_latency}ms")
            evidence.append(f"entry_latency_ms={latency}")
        elif latency is not None and latency < max_latency * 0.5:
            positive.append(f"Fast entry: {latency}ms")

        # --- SLIPPAGE ---
        entry_slippage = trade.get("entry_slippage_bps")
        expected_slippage = rules.get("expected_slippage_bps", 5)
        max_slip_mult = rules.get("max_slippage_multiplier", 2.0)

        if entry_slippage is not None and entry_slippage > expected_slippage * max_slip_mult:
            score -= 10
            root_causes.append("slippage_spike")
            negative.append(f"Entry slippage {entry_slippage:.1f}bps vs expected {expected_slippage}bps")
            evidence.append(f"entry_slippage_bps={entry_slippage}")
        elif entry_slippage is not None and entry_slippage < expected_slippage * 0.5:
            root_causes.append("good_execution")
            positive.append(f"Below-average slippage: {entry_slippage:.1f}bps")

        exit_slippage = trade.get("exit_slippage_bps")
        if exit_slippage is not None and exit_slippage > expected_slippage * max_slip_mult:
            score -= 10
            if "slippage_spike" not in root_causes:
                root_causes.append("slippage_spike")
            negative.append(f"Exit slippage {exit_slippage:.1f}bps vs expected {expected_slippage}bps")
            evidence.append(f"exit_slippage_bps={exit_slippage}")

        # --- EXIT REASON ANALYSIS ---
        exit_reason = trade.get("exit_reason", "")
        pnl = trade.get("pnl", 0)

        if exit_reason == "MANUAL":
            score -= 10
            root_causes.append("early_exit")
            negative.append("Manual exit — was this justified?")
            evidence.append(f"exit_reason=MANUAL")

        if exit_reason == "STOP_LOSS" and entry_slippage is not None:
            # Check if SL was too tight relative to volatility
            atr = trade.get("atr_at_entry")
            strategy_params = trade.get("strategy_params_at_entry", {})
            sl_mult = strategy_params.get("sl_atr_multiplier") or strategy_params.get("sl_atr_mult")

            if atr and sl_mult and sl_mult < 0.8:
                score -= 10
                root_causes.append("premature_stop")
                negative.append(f"SL multiplier {sl_mult}x ATR may be too tight")
                evidence.append(f"sl_atr_multiplier={sl_mult}, atr_at_entry={atr}")

        # --- FUNDING RATE ---
        funding = trade.get("funding_rate_at_entry")
        side = trade.get("side", "")
        if funding is not None and abs(funding) > 0.01:  # significant funding
            if (side == "LONG" and funding > 0.03) or (side == "SHORT" and funding < -0.03):
                score -= 5
                root_causes.append("funding_adverse")
                negative.append(f"Funding rate {funding:.4f} working against {side} position")
                evidence.append(f"funding_rate_at_entry={funding}")
            elif (side == "LONG" and funding < -0.01) or (side == "SHORT" and funding > 0.01):
                root_causes.append("funding_favorable")
                positive.append(f"Funding rate {funding:.4f} favorable for {side}")

        # --- FINAL CLASSIFICATION ---
        score = max(0, min(100, score))

        # Add result-based tags (these don't affect score, just classification)
        if score >= 80:
            if pnl and pnl > 0:
                pnl_pct = abs(trade.get("pnl_pct", 0))
                if pnl_pct > 3.0:  # >3% is exceptional
                    root_causes.append("exceptional_win")
                else:
                    root_causes.append("normal_win")
            elif pnl is not None and pnl <= 0:
                root_causes.append("normal_loss")

        # Classification
        if score >= 70:
            classification = "good_process"
        elif score >= 40:
            classification = "neutral"
        else:
            classification = "bad_process"

        return ProcessScore(
            trade_id=trade.get("trade_id", "unknown"),
            process_quality_score=score,
            root_causes=root_causes,
            evidence_refs=evidence,
            positive_factors=positive,
            negative_factors=negative,
            classification=classification,
        )
```

### Step 3: Integrate with the trade logger

Call the scorer after every trade exit and attach the results to the trade event:

```python
# In your trade exit flow, after log_exit:

trade_event = trade_logger.log_exit(trade_id=..., ...)
if trade_event:
    try:
        process_score = process_scorer.score_trade(
            trade_event.to_dict(),
            strategy_type=config.strategy_type
        )
        # Write the score alongside the trade
        score_dir = Path(config["data_dir"]) / "scores"
        score_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = score_dir / f"scores_{today}.jsonl"
        with open(filepath, "a") as f:
            f.write(json.dumps(process_score.to_dict()) + "\n")
    except Exception:
        pass
```

Or, more cleanly, extend `TradeLogger.log_exit()` to accept and store the process score:

```python
# Add to TradeLogger.log_exit, just before _write_event:
if self.process_scorer:
    try:
        score = self.process_scorer.score_trade(trade.to_dict(), strategy_type=self.strategy_type)
        # Embed score directly in the trade event
        trade.process_quality_score = score.process_quality_score
        trade.root_causes = score.root_causes
        trade.evidence_refs = score.evidence_refs
    except Exception:
        pass
```

Choose whichever integration pattern fits this bot's architecture better. The important thing is that every completed trade has a process score attached.

---

## Done Criteria

- [ ] `instrumentation/src/process_scorer.py` exists with `ProcessScorer` and `ProcessScore`
- [ ] `instrumentation/config/process_scoring_rules.yaml` exists with rules adapted to this bot's strategies
- [ ] Root causes come ONLY from the controlled `ROOT_CAUSES` list — no free-form tags
- [ ] Every completed trade has a process score (check `instrumentation/data/scores/` or embedded in trade events)
- [ ] Score of 100 is possible (test with a perfectly-executed trade)
- [ ] Score < 50 triggers correctly (test with regime mismatch + weak signal + high slippage)
- [ ] `normal_loss` is tagged when score >= 80 but PnL is negative
- [ ] Evidence refs point to specific field values (auditable)
- [ ] Scorer never crashes (returns degraded score on bad input)
