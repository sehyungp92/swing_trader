# Instrumentation Kit & Enriched Events Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close all critical instrumentation gaps and implement the five highest-impact data capture improvements identified in `feedback.md`, giving the orchestrator rich, per-strategy trade telemetry with signal confluence, filter thresholds, position sizing inputs, portfolio state, and post-exit price tracking.

**Architecture:** Build an `InstrumentationKit` facade class that wraps `InstrumentationContext` with a clean 3-method API (`log_entry`, `log_exit`, `log_missed`). The kit internalizes all `safe_instrument` wrapping, regime classification, snapshot capture, and process scoring — replacing the 15-line boilerplate blocks currently duplicated across all 4 strategy engines. Enrich the `TradeEvent` dataclass with new fields for signal factors, filter decisions, sizing inputs, and portfolio state. Bridge PostgreSQL trade data into the instrumentation layer by making TradeRecorder emit to the kit.

**Tech Stack:** Python 3.12, dataclasses, JSONL, YAML config, asyncpg (PG bridge), pytest

**Sequencing Rationale:** Tasks are ordered to minimize rework:
1. Schema enrichment first (pure data model, no behavioral changes)
2. Kit facade second (depends on enriched schema)
3. Per-strategy bot_id third (config change in bootstrap, used by kit)
4. Strategy integration fourth (replaces old hooks with kit calls, wires up enriched data)
5. Hooks manifest fifth (documents final state after integration)
6. Post-exit tracking sixth (independent background service)
7. PG bridge last (depends on kit being integrated)

**Pre-existing Fixes (no work needed):**
- **Docker volume mounts** (feedback gap #3): Already implemented. `docker-compose.yml` defines named volumes `instrumentation_atrss`, `instrumentation_helix`, `instrumentation_breakout`, `instrumentation_keltner` mounted at `/app/instrumentation/data` for each strategy container.

---

## Task 1: Enrich TradeEvent Schema

**Files:**
- Modify: `instrumentation/src/trade_logger.py:22-84` (TradeEvent dataclass)
- Test: `instrumentation/tests/test_trade_logger.py`

Add four new field groups to `TradeEvent` that enable signal confluence analysis, filter optimization, sizing audits, and portfolio correlation tracking.

**Step 1: Write the failing test**

Add to `instrumentation/tests/test_trade_logger.py`:

```python
def test_trade_event_has_enriched_fields():
    """TradeEvent must include signal_factors, filter_decisions, sizing_inputs, portfolio_state."""
    from instrumentation.src.trade_logger import TradeEvent

    te = TradeEvent(
        trade_id="test_enriched",
        event_metadata={},
        entry_snapshot={},
        signal_factors=[
            {"factor_name": "adx", "factor_value": 28.5, "threshold": 25.0, "contribution": "trend_confirm"},
            {"factor_name": "ema_sep", "factor_value": 0.02, "threshold": 0.01, "contribution": "momentum"},
        ],
        filter_decisions=[
            {"filter_name": "quality_gate", "threshold": 3.0, "actual_value": 4.5, "passed": True, "margin_pct": 50.0},
            {"filter_name": "momentum", "threshold": 0.0, "actual_value": 0.5, "passed": True, "margin_pct": 100.0},
        ],
        sizing_inputs={
            "target_risk_pct": 0.02,
            "account_equity": 100000.0,
            "volatility_basis": 1.5,
            "sizing_model": "atr_risk",
        },
        portfolio_state_at_entry={
            "total_exposure_pct": 0.45,
            "net_direction": "LONG",
            "num_positions": 3,
            "correlated_positions": ["SPY", "QQQ"],
        },
    )

    d = te.to_dict()
    assert len(d["signal_factors"]) == 2
    assert d["signal_factors"][0]["factor_name"] == "adx"
    assert d["filter_decisions"][1]["passed"] is True
    assert d["sizing_inputs"]["target_risk_pct"] == 0.02
    assert d["portfolio_state_at_entry"]["num_positions"] == 3


def test_trade_event_enriched_fields_default_empty():
    """Enriched fields default to empty collections when not provided."""
    from instrumentation.src.trade_logger import TradeEvent

    te = TradeEvent(trade_id="test_defaults", event_metadata={}, entry_snapshot={})
    d = te.to_dict()
    assert d["signal_factors"] == []
    assert d["filter_decisions"] == []
    assert d["sizing_inputs"] is None
    assert d["portfolio_state_at_entry"] is None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest instrumentation/tests/test_trade_logger.py::test_trade_event_has_enriched_fields -v`
Expected: FAIL — `TradeEvent.__init__() got an unexpected keyword argument 'signal_factors'`

**Step 3: Write minimal implementation**

In `instrumentation/src/trade_logger.py`, add these fields to the `TradeEvent` dataclass after the `strategy_params_at_entry` field (after line 70):

```python
    # Signal confluence — what factors contributed to this entry
    signal_factors: List[dict] = field(default_factory=list)
    # Filter threshold context — how close was each filter to blocking
    filter_decisions: List[dict] = field(default_factory=list)
    # Position sizing inputs — what drove the size decision
    sizing_inputs: Optional[dict] = None
    # Portfolio state at entry — exposure, direction, correlated positions
    portfolio_state_at_entry: Optional[dict] = None
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest instrumentation/tests/test_trade_logger.py -v`
Expected: PASS (both new tests + all existing tests)

**Step 5: Commit**

```bash
git add instrumentation/src/trade_logger.py instrumentation/tests/test_trade_logger.py
git commit -m "feat: enrich TradeEvent with signal_factors, filter_decisions, sizing_inputs, portfolio_state"
```

---

## Task 2: Build InstrumentationKit Facade

**Files:**
- Create: `instrumentation/src/kit.py`
- Modify: `instrumentation/src/__init__.py` (export kit)
- Test: `instrumentation/tests/test_kit.py`

The Kit is the central deliverable. It replaces 15-line boilerplate blocks across all 4 engines with a single method call. All `safe_instrument` wrapping, regime classification, snapshot capture, and process scoring happen inside the Kit.

**Step 1: Write the failing test**

Create `instrumentation/tests/test_kit.py`:

```python
"""Tests for InstrumentationKit facade."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


def _make_kit(strategy_id="TEST_STRAT"):
    """Build a Kit with mocked services."""
    from instrumentation.src.kit import InstrumentationKit
    from instrumentation.src.context import InstrumentationContext

    ctx = InstrumentationContext()
    ctx.trade_logger = MagicMock()
    ctx.process_scorer = MagicMock()
    ctx.regime_classifier = MagicMock()
    ctx.missed_logger = MagicMock()
    ctx.snapshot_service = MagicMock()
    ctx.data_dir = "instrumentation/data"

    # Default return values
    ctx.regime_classifier.current_regime.return_value = "trending_up"
    mock_trade_event = MagicMock()
    mock_trade_event.to_dict.return_value = {"trade_id": "t1", "stage": "entry"}
    ctx.trade_logger.log_entry.return_value = mock_trade_event

    exit_event = MagicMock()
    exit_event.to_dict.return_value = {"trade_id": "t1", "stage": "exit", "pnl": 100.0}
    ctx.trade_logger.log_exit.return_value = exit_event

    return InstrumentationKit(ctx, strategy_id=strategy_id)


class TestKitLogEntry:
    def test_log_entry_calls_trade_logger(self):
        kit = _make_kit()
        kit.log_entry(
            trade_id="t1",
            pair="QQQ",
            side="LONG",
            entry_price=500.0,
            position_size=10.0,
            position_size_quote=5000.0,
            entry_signal="PULLBACK",
            entry_signal_id="QQQ_PB_123",
            entry_signal_strength=0.7,
            active_filters=["quality_gate"],
            passed_filters=["quality_gate"],
            strategy_params={"atrh": 1.5},
        )
        kit._ctx.trade_logger.log_entry.assert_called_once()
        call_kwargs = kit._ctx.trade_logger.log_entry.call_args
        assert call_kwargs.kwargs["strategy_id"] == "TEST_STRAT"

    def test_log_entry_auto_classifies_regime(self):
        kit = _make_kit()
        kit.log_entry(
            trade_id="t1", pair="SPY", side="LONG",
            entry_price=500.0, position_size=10.0,
            position_size_quote=5000.0, entry_signal="BRK",
            entry_signal_id="x", entry_signal_strength=0.5,
            active_filters=[], passed_filters=[],
            strategy_params={},
        )
        kit._ctx.regime_classifier.current_regime.assert_called_with("SPY")
        call_kwargs = kit._ctx.trade_logger.log_entry.call_args
        assert call_kwargs.kwargs["market_regime"] == "trending_up"

    def test_log_entry_passes_enriched_fields(self):
        kit = _make_kit()
        sf = [{"factor_name": "adx", "factor_value": 30, "threshold": 25, "contribution": "trend"}]
        fd = [{"filter_name": "quality", "threshold": 3, "actual_value": 5, "passed": True, "margin_pct": 66.7}]
        si = {"target_risk_pct": 0.02, "account_equity": 100000, "volatility_basis": 1.5, "sizing_model": "atr"}
        ps = {"total_exposure_pct": 0.3, "net_direction": "LONG", "num_positions": 2, "correlated_positions": []}

        kit.log_entry(
            trade_id="t1", pair="QQQ", side="LONG",
            entry_price=500.0, position_size=10.0,
            position_size_quote=5000.0, entry_signal="PB",
            entry_signal_id="x", entry_signal_strength=0.7,
            active_filters=[], passed_filters=[],
            strategy_params={},
            signal_factors=sf,
            filter_decisions=fd,
            sizing_inputs=si,
            portfolio_state_at_entry=ps,
        )
        call_kwargs = kit._ctx.trade_logger.log_entry.call_args.kwargs
        assert call_kwargs["signal_factors"] == sf
        assert call_kwargs["filter_decisions"] == fd
        assert call_kwargs["sizing_inputs"] == si
        assert call_kwargs["portfolio_state_at_entry"] == ps

    def test_log_entry_never_raises(self):
        kit = _make_kit()
        kit._ctx.trade_logger.log_entry.side_effect = RuntimeError("boom")
        # Must not raise
        result = kit.log_entry(
            trade_id="t1", pair="QQQ", side="LONG",
            entry_price=500.0, position_size=10.0,
            position_size_quote=5000.0, entry_signal="PB",
            entry_signal_id="x", entry_signal_strength=0.5,
            active_filters=[], passed_filters=[],
            strategy_params={},
        )
        assert result is None


class TestKitLogExit:
    def test_log_exit_calls_logger_and_scorer(self):
        kit = _make_kit()
        kit.log_exit(trade_id="t1", exit_price=510.0, exit_reason="STOP_LOSS")
        kit._ctx.trade_logger.log_exit.assert_called_once()
        kit._ctx.process_scorer.score_and_write.assert_called_once()

    def test_log_exit_skips_scorer_when_no_event(self):
        kit = _make_kit()
        kit._ctx.trade_logger.log_exit.return_value = None
        kit.log_exit(trade_id="t1", exit_price=510.0, exit_reason="STOP_LOSS")
        kit._ctx.process_scorer.score_and_write.assert_not_called()

    def test_log_exit_never_raises(self):
        kit = _make_kit()
        kit._ctx.trade_logger.log_exit.side_effect = RuntimeError("boom")
        result = kit.log_exit(trade_id="t1", exit_price=510.0, exit_reason="STOP")
        assert result is None


class TestKitLogMissed:
    def test_log_missed_calls_missed_logger(self):
        kit = _make_kit()
        kit.log_missed(
            pair="QQQ", side="LONG", signal="pullback",
            signal_id="x", signal_strength=0.5,
            blocked_by="quality_gate",
        )
        kit._ctx.missed_logger.log_missed.assert_called_once()
        call_kwargs = kit._ctx.missed_logger.log_missed.call_args.kwargs
        assert call_kwargs["strategy_id"] == "TEST_STRAT"

    def test_log_missed_never_raises(self):
        kit = _make_kit()
        kit._ctx.missed_logger.log_missed.side_effect = RuntimeError("boom")
        result = kit.log_missed(
            pair="QQQ", side="LONG", signal="x",
            signal_id="x", signal_strength=0.0,
            blocked_by="gate",
        )
        assert result is None


class TestKitClassifyRegime:
    def test_classify_regime_returns_string(self):
        kit = _make_kit()
        assert kit.classify_regime("QQQ") == "trending_up"

    def test_classify_regime_returns_unknown_on_error(self):
        kit = _make_kit()
        kit._ctx.regime_classifier.current_regime.side_effect = RuntimeError("fail")
        assert kit.classify_regime("QQQ") == "unknown"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest instrumentation/tests/test_kit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'instrumentation.src.kit'`

**Step 3: Write minimal implementation**

Create `instrumentation/src/kit.py`:

```python
"""InstrumentationKit — unified facade for strategy instrumentation.

Replaces scattered safe_instrument() boilerplate in strategy engines
with a clean 3-method API: log_entry, log_exit, log_missed.

Usage::

    from instrumentation.src.kit import InstrumentationKit

    kit = InstrumentationKit(ctx, strategy_id="ATRSS")
    kit.log_entry(trade_id="t1", pair="QQQ", ...)
    kit.log_exit(trade_id="t1", exit_price=510.0, exit_reason="STOP_LOSS")
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, List

from .context import InstrumentationContext

logger = logging.getLogger("instrumentation.kit")


class InstrumentationKit:
    """Unified instrumentation facade.

    Wraps InstrumentationContext services with fail-safe error handling.
    Every public method swallows exceptions — instrumentation never
    crashes or blocks trading.
    """

    def __init__(self, ctx: InstrumentationContext, strategy_id: str):
        self._ctx = ctx
        self._strategy_id = strategy_id

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
        *,
        expected_entry_price: Optional[float] = None,
        entry_latency_ms: Optional[int] = None,
        exchange_timestamp: Optional[datetime] = None,
        bar_id: Optional[str] = None,
        signal_factors: Optional[List[dict]] = None,
        filter_decisions: Optional[List[dict]] = None,
        sizing_inputs: Optional[dict] = None,
        portfolio_state_at_entry: Optional[dict] = None,
    ):
        """Log a trade entry with full context. Never raises."""
        try:
            regime = self.classify_regime(pair)
            return self._ctx.trade_logger.log_entry(
                trade_id=trade_id,
                pair=pair,
                side=side,
                entry_price=entry_price,
                position_size=position_size,
                position_size_quote=position_size_quote,
                entry_signal=entry_signal,
                entry_signal_id=entry_signal_id,
                entry_signal_strength=entry_signal_strength,
                active_filters=active_filters,
                passed_filters=passed_filters,
                strategy_params=strategy_params,
                strategy_id=self._strategy_id,
                expected_entry_price=expected_entry_price,
                entry_latency_ms=entry_latency_ms,
                exchange_timestamp=exchange_timestamp,
                bar_id=bar_id,
                market_regime=regime,
                signal_factors=signal_factors or [],
                filter_decisions=filter_decisions or [],
                sizing_inputs=sizing_inputs,
                portfolio_state_at_entry=portfolio_state_at_entry,
            )
        except Exception as e:
            logger.debug("Kit.log_entry failed: %s", e)
            return None

    def log_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        fees_paid: float = 0.0,
        exchange_timestamp: Optional[datetime] = None,
        expected_exit_price: Optional[float] = None,
        exit_latency_ms: Optional[int] = None,
    ):
        """Log a trade exit + auto-score process quality. Never raises."""
        try:
            trade_event = self._ctx.trade_logger.log_exit(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_reason=exit_reason,
                fees_paid=fees_paid,
                exchange_timestamp=exchange_timestamp,
                expected_exit_price=expected_exit_price,
                exit_latency_ms=exit_latency_ms,
            )
            if trade_event:
                try:
                    self._ctx.process_scorer.score_and_write(
                        trade_event.to_dict(),
                        self._strategy_id,
                        self._ctx.data_dir,
                    )
                except Exception as e:
                    logger.debug("Kit.log_exit scoring failed: %s", e)
            return trade_event
        except Exception as e:
            logger.debug("Kit.log_exit failed: %s", e)
            return None

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
        exchange_timestamp: Optional[datetime] = None,
        bar_id: Optional[str] = None,
    ):
        """Log a missed opportunity (signal blocked by filter). Never raises."""
        try:
            regime = self.classify_regime(pair)
            return self._ctx.missed_logger.log_missed(
                pair=pair,
                side=side,
                signal=signal,
                signal_id=signal_id,
                signal_strength=signal_strength,
                blocked_by=blocked_by,
                block_reason=block_reason,
                strategy_params=strategy_params,
                strategy_id=self._strategy_id,
                market_regime=regime,
                exchange_timestamp=exchange_timestamp,
                bar_id=bar_id,
            )
        except Exception as e:
            logger.debug("Kit.log_missed failed: %s", e)
            return None

    def classify_regime(self, symbol: str) -> str:
        """Classify market regime for symbol. Returns 'unknown' on error."""
        try:
            return self._ctx.regime_classifier.current_regime(symbol)
        except Exception:
            return "unknown"

    def capture_snapshot(self, symbol: str):
        """Capture market snapshot. Returns None on error."""
        try:
            return self._ctx.snapshot_service.capture_now(symbol)
        except Exception as e:
            logger.debug("Kit.capture_snapshot failed: %s", e)
            return None
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest instrumentation/tests/test_kit.py -v`
Expected: All 11 tests PASS

**Step 5: Commit**

```bash
git add instrumentation/src/kit.py instrumentation/tests/test_kit.py
git commit -m "feat: add InstrumentationKit facade with log_entry, log_exit, log_missed"
```

---

## Task 3: Update TradeLogger to Accept Enriched Fields

**Files:**
- Modify: `instrumentation/src/trade_logger.py:105-125` (log_entry signature)
- Test: `instrumentation/tests/test_trade_logger.py`

The `log_entry` method must accept and pass through the new enriched fields to `TradeEvent`.

**Step 1: Write the failing test**

Add to `instrumentation/tests/test_trade_logger.py`:

```python
def test_log_entry_stores_enriched_fields(tmp_path):
    """log_entry must pass signal_factors, filter_decisions, sizing_inputs, portfolio_state to TradeEvent."""
    from instrumentation.src.trade_logger import TradeLogger
    from unittest.mock import MagicMock

    snap_svc = MagicMock()
    snap_svc.capture_now.return_value = MagicMock(
        to_dict=lambda: {}, atr_14=1.0, spread_bps=0.5,
        volume_24h=1e6, funding_rate=None, open_interest=None,
    )

    config = {"bot_id": "test", "data_dir": str(tmp_path), "data_source_id": "test"}
    tl = TradeLogger(config, snap_svc)

    sf = [{"factor_name": "adx", "factor_value": 30, "threshold": 25, "contribution": "trend"}]
    fd = [{"filter_name": "gate", "threshold": 3, "actual_value": 5, "passed": True, "margin_pct": 66.7}]
    si = {"target_risk_pct": 0.02, "account_equity": 100000, "volatility_basis": 1.5, "sizing_model": "atr"}
    ps = {"total_exposure_pct": 0.3, "net_direction": "LONG", "num_positions": 2, "correlated_positions": []}

    event = tl.log_entry(
        trade_id="t_enriched",
        pair="QQQ", side="LONG",
        entry_price=500.0, position_size=10.0, position_size_quote=5000.0,
        entry_signal="PB", entry_signal_id="x", entry_signal_strength=0.7,
        active_filters=["gate"], passed_filters=["gate"],
        strategy_params={"atrh": 1.5},
        signal_factors=sf,
        filter_decisions=fd,
        sizing_inputs=si,
        portfolio_state_at_entry=ps,
    )

    assert event.signal_factors == sf
    assert event.filter_decisions == fd
    assert event.sizing_inputs == si
    assert event.portfolio_state_at_entry == ps
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest instrumentation/tests/test_trade_logger.py::test_log_entry_stores_enriched_fields -v`
Expected: FAIL — `log_entry() got an unexpected keyword argument 'signal_factors'`

**Step 3: Write minimal implementation**

Update `TradeLogger.log_entry()` signature in `instrumentation/src/trade_logger.py`:

Add these parameters after `market_regime` (line 123):

```python
        signal_factors: Optional[List[dict]] = None,
        filter_decisions: Optional[List[dict]] = None,
        sizing_inputs: Optional[dict] = None,
        portfolio_state_at_entry: Optional[dict] = None,
```

And pass them to `TradeEvent()` constructor (after `strategy_params_at_entry=strategy_params`, around line 168):

```python
                signal_factors=signal_factors or [],
                filter_decisions=filter_decisions or [],
                sizing_inputs=sizing_inputs,
                portfolio_state_at_entry=portfolio_state_at_entry,
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest instrumentation/tests/test_trade_logger.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add instrumentation/src/trade_logger.py instrumentation/tests/test_trade_logger.py
git commit -m "feat: trade_logger.log_entry accepts enriched fields"
```

---

## Task 4: Per-Strategy Bot ID in Bootstrap

**Files:**
- Modify: `instrumentation/src/bootstrap.py:20-72` (add strategy_id param)
- Modify: `instrumentation/src/kit.py` (factory helper)
- Test: `instrumentation/tests/test_bootstrap.py` (add test)

Addresses feedback gap #6: all 5 strategies share `bot_id: swing_multi_01`. The orchestrator can't distinguish events by strategy at the relay envelope level.

**Step 1: Write the failing test**

Add to `instrumentation/tests/test_bootstrap.py` (create if needed):

```python
def test_bootstrap_with_strategy_id_overrides_bot_id():
    """When strategy_id is passed, bot_id in config should be overridden."""
    from instrumentation.src.bootstrap import bootstrap_instrumentation

    ctx = bootstrap_instrumentation(
        symbols=["QQQ"],
        strategy_id="ATRSS",
    )
    # The trade_logger should have bot_id = "ATRSS"
    assert ctx.trade_logger.bot_id == "ATRSS"


def test_bootstrap_without_strategy_id_uses_config_default():
    """Without strategy_id, bot_id comes from config (swing_multi_01)."""
    from instrumentation.src.bootstrap import bootstrap_instrumentation

    ctx = bootstrap_instrumentation(symbols=["QQQ"])
    assert ctx.trade_logger.bot_id == "swing_multi_01"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest instrumentation/tests/test_bootstrap.py::test_bootstrap_with_strategy_id_overrides_bot_id -v`
Expected: FAIL — `bootstrap_instrumentation() got an unexpected keyword argument 'strategy_id'`

**Step 3: Write minimal implementation**

In `instrumentation/src/bootstrap.py`, update the function signature:

```python
def bootstrap_instrumentation(
    symbols: list[str] | None = None,
    data_provider=None,
    strategy_id: str | None = None,
) -> "InstrumentationContext":
```

After `config = _load_config()` (line 43), add:

```python
    if strategy_id:
        config["bot_id"] = strategy_id
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest instrumentation/tests/test_bootstrap.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add instrumentation/src/bootstrap.py instrumentation/tests/test_bootstrap.py
git commit -m "feat: bootstrap accepts strategy_id to override bot_id for per-strategy event identity"
```

---

## Task 5: Add Kit Factory to Bootstrap

**Files:**
- Modify: `instrumentation/src/bootstrap.py`
- Test: `instrumentation/tests/test_bootstrap.py`

Add a convenience factory that creates both context + kit in one call. This is what strategy `main.py` files will use.

**Step 1: Write the failing test**

Add to `instrumentation/tests/test_bootstrap.py`:

```python
def test_bootstrap_kit_returns_kit_instance():
    """bootstrap_kit should return an InstrumentationKit."""
    from instrumentation.src.bootstrap import bootstrap_kit
    from instrumentation.src.kit import InstrumentationKit

    kit = bootstrap_kit(strategy_id="ATRSS", symbols=["QQQ"])
    assert isinstance(kit, InstrumentationKit)
    assert kit._strategy_id == "ATRSS"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest instrumentation/tests/test_bootstrap.py::test_bootstrap_kit_returns_kit_instance -v`
Expected: FAIL — `cannot import name 'bootstrap_kit'`

**Step 3: Write minimal implementation**

Add to `instrumentation/src/bootstrap.py`:

```python
def bootstrap_kit(
    strategy_id: str,
    symbols: list[str] | None = None,
    data_provider=None,
) -> "InstrumentationKit":
    """Create an InstrumentationKit with all services wired up.

    Convenience wrapper: bootstraps context + wraps in Kit facade.

    Args:
        strategy_id: Strategy identifier (used as bot_id and for scoring).
        symbols: Active trading symbols.
        data_provider: Optional data source for snapshots/regime.

    Returns:
        InstrumentationKit ready for ``log_entry``/``log_exit`` calls.
        Call ``kit._ctx.start()`` to enable sidecar forwarding.
    """
    from .kit import InstrumentationKit

    ctx = bootstrap_instrumentation(
        symbols=symbols,
        data_provider=data_provider,
        strategy_id=strategy_id,
    )
    return InstrumentationKit(ctx, strategy_id=strategy_id)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest instrumentation/tests/test_bootstrap.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add instrumentation/src/bootstrap.py instrumentation/tests/test_bootstrap.py
git commit -m "feat: add bootstrap_kit() factory for one-call Kit creation"
```

---

## Task 6: Integrate Kit into Strategy 1 (ATRSS)

**Files:**
- Modify: `strategy/engine.py` (replace all instrumentation blocks)
- Modify: `strategy/main.py` (use bootstrap_kit)

Replace the 6 scattered `if self._instr: try: from instrumentation.src.hooks import safe_instrument; ...` blocks with clean Kit calls. Wire up enriched data (signal_factors, filter_decisions, sizing_inputs) from ATRSS's existing context.

**Step 1: Update main.py to use bootstrap_kit**

In `strategy/main.py`, find where `bootstrap_instrumentation` is called and replace with:

```python
from instrumentation.src.bootstrap import bootstrap_kit

kit = bootstrap_kit(strategy_id="ATRSS", symbols=symbol_list, data_provider=data_provider)
kit._ctx.start()
```

Pass `kit` (not `ctx`) to the engine constructor. The engine should store it as `self._kit` instead of `self._instr`.

**Step 2: Replace engine.py entry hooks**

Replace the block at `engine.py:1475-1500` with:

```python
            # Hook 4: Instrumentation trade entry
            if self._kit:
                side_str = "LONG" if direction == Direction.LONG else "SHORT"
                self._kit.log_entry(
                    trade_id=trade_id or f"{sym}_{fill_time.isoformat()}",
                    pair=sym,
                    side=side_str,
                    entry_price=fill_price,
                    position_size=float(fill_qty),
                    position_size_quote=fill_price * fill_qty,
                    entry_signal=ctype.value,
                    entry_signal_id=f"{sym}_{ctype.value}_{fill_time.isoformat()}",
                    entry_signal_strength=meta.get("quality_score", 0.5),
                    active_filters=["quality_gate", "momentum", "reentry"],
                    passed_filters=["quality_gate"],
                    strategy_params={"atrh": atrh, "stop": meta["initial_stop"]},
                    expected_entry_price=meta["trigger_price"],
                    signal_factors=[
                        {"factor_name": "quality_score", "factor_value": meta.get("quality_score", 0),
                         "threshold": cfg.quality_gate_threshold, "contribution": "entry_quality"},
                        {"factor_name": "adx", "factor_value": daily.adx,
                         "threshold": 20.0, "contribution": "trend_strength"},
                        {"factor_name": "ema_sep_pct", "factor_value": daily.ema_sep_pct,
                         "threshold": 0.0, "contribution": "momentum"},
                    ],
                    filter_decisions=[
                        {"filter_name": "quality_gate",
                         "threshold": cfg.quality_gate_threshold,
                         "actual_value": meta.get("quality_score", 0),
                         "passed": True,
                         "margin_pct": round((meta.get("quality_score", 0) - cfg.quality_gate_threshold)
                                            / cfg.quality_gate_threshold * 100, 1)
                                       if cfg.quality_gate_threshold > 0 else 100.0},
                    ],
                    sizing_inputs={
                        "target_risk_pct": cfg.risk_pct,
                        "account_equity": self._equity,
                        "volatility_basis": atrh,
                        "sizing_model": "atr_risk",
                    },
                )
```

**Step 3: Replace engine.py exit hooks**

Replace the blocks at `engine.py:1606-1626` and `engine.py:1688-1708` with:

```python
                # Hook 5: Instrumentation trade exit
                if self._kit:
                    for leg in pos.legs:
                        tid = leg.trade_id or f"{sym}_{leg.fill_time.isoformat()}"
                        self._kit.log_exit(
                            trade_id=tid,
                            exit_price=fill_price,
                            exit_reason="STOP_LOSS",  # or reason variable
                        )
```

**Step 4: Replace missed opportunity hooks**

Replace the blocks at `engine.py:309-326`, `engine.py:427-440`, `engine.py:444-458`, `engine.py:486-502`, `engine.py:522-540` with:

```python
                        if self._kit:
                            self._kit.log_missed(
                                pair=sym,
                                side="LONG" if direction == Direction.LONG else "SHORT",
                                signal="pullback",
                                signal_id=f"{sym}_pullback_{now.isoformat()}",
                                signal_strength=quality_score,
                                blocked_by="quality_gate",
                                block_reason=f"score {quality_score} < threshold {cfg.quality_gate_threshold}",
                            )
```

**Step 5: Replace Hook 1 (periodic snapshots)**

Replace the block at `engine.py:331-339` with:

```python
        if self._kit:
            for sym in self._config:
                self._kit.capture_snapshot(sym)
```

(Regime classification is now done automatically inside `log_entry`.)

**Step 6: Run tests**

Run: `python -m pytest strategy/tests/ -v` (if tests exist)
Run: `python -m pytest instrumentation/tests/ -v`
Expected: PASS

**Step 7: Commit**

```bash
git add strategy/engine.py strategy/main.py
git commit -m "refactor(ATRSS): replace instrumentation boilerplate with InstrumentationKit"
```

---

## Task 7: Integrate Kit into Strategy 2 (AKC-Helix)

**Files:**
- Modify: `strategy_2/engine.py`
- Modify: `strategy_2/main.py`

Same pattern as Task 6. Key differences:
- `entry_signal` = `setup.setup_class.value` (CLASS_A, CLASS_B, etc.)
- `entry_signal_strength` = currently hardcoded 0.5 — keep for now
- `strategy_params` includes `adx_at_entry`, `regime_4h`, `size_mult`
- `active_filters`/`passed_filters` are currently empty — populate from gates

**Step 1: Update main.py**

```python
kit = bootstrap_kit(strategy_id="AKC_HELIX", symbols=symbol_list, data_provider=data_provider)
```

**Step 2: Replace entry hook at engine.py:2011-2040**

```python
        if self._kit:
            side_str = "LONG" if setup.direction == Direction.LONG else "SHORT"
            self._kit.log_entry(
                trade_id=setup.trade_id or setup.setup_id,
                pair=setup.symbol,
                side=side_str,
                entry_price=fill_price,
                position_size=float(fill_qty),
                position_size_quote=fill_price * fill_qty,
                entry_signal=setup.setup_class.value,
                entry_signal_id=setup.setup_id,
                entry_signal_strength=0.5,
                active_filters=list(setup.gates_checked) if hasattr(setup, 'gates_checked') else [],
                passed_filters=list(setup.gates_passed) if hasattr(setup, 'gates_passed') else [],
                strategy_params={
                    "adx_at_entry": setup.adx_at_entry,
                    "regime_4h": setup.regime_4h_at_entry,
                    "size_mult": setup.setup_size_mult,
                },
                expected_entry_price=setup.bos_level,
                signal_factors=[
                    {"factor_name": "adx", "factor_value": setup.adx_at_entry,
                     "threshold": 20.0, "contribution": "trend_strength"},
                    {"factor_name": "setup_class", "factor_value": setup.setup_class.value,
                     "threshold": "CLASS_A", "contribution": "setup_quality"},
                    {"factor_name": "size_mult", "factor_value": setup.setup_size_mult,
                     "threshold": 0.5, "contribution": "conviction"},
                ],
                sizing_inputs={
                    "target_risk_pct": self._base_risk_pct,
                    "account_equity": self._equity,
                    "volatility_basis": setup.adx_at_entry,
                    "sizing_model": "helix_class_mult",
                },
            )
```

**Step 3: Replace exit hooks at engine.py:1744-1766, 2111-2130**

Same pattern as ATRSS — replace with `self._kit.log_exit(...)`.

**Step 4: Replace missed opportunity hooks at engine.py:457-470**

```python
                        if self._kit:
                            self._kit.log_missed(
                                pair=setup.symbol,
                                side="LONG" if setup.direction == Direction.LONG else "SHORT",
                                signal=setup.setup_class.value,
                                signal_id=setup.setup_id,
                                signal_strength=0.5,
                                blocked_by="allocator",
                                block_reason="rejected by portfolio allocator",
                            )
```

**Step 5: Replace Hook 1 at engine.py:491-499**

```python
        if self._kit:
            for sym in self._config:
                self._kit.capture_snapshot(sym)
```

**Step 6: Commit**

```bash
git add strategy_2/engine.py strategy_2/main.py
git commit -m "refactor(AKC_HELIX): replace instrumentation boilerplate with InstrumentationKit"
```

---

## Task 8: Integrate Kit into Strategy 3 (SWING_BREAKOUT_V3)

**Files:**
- Modify: `strategy_3/engine.py`
- Modify: `strategy_3/main.py`

**Step 1: Update main.py**

```python
kit = bootstrap_kit(strategy_id="SWING_BREAKOUT_V3", symbols=symbol_list, data_provider=data_provider)
```

**Step 2: Replace entry hook at engine.py:1728-1753**

```python
                    if self._kit:
                        side_str = "LONG" if setup.direction == Direction.LONG else "SHORT"
                        self._kit.log_entry(
                            trade_id=setup.setup_id,
                            pair=setup.symbol,
                            side=side_str,
                            entry_price=setup.fill_price,
                            position_size=float(setup.fill_qty),
                            position_size_quote=setup.fill_price * setup.fill_qty,
                            entry_signal=setup.entry_type.value if hasattr(setup.entry_type, 'value') else str(setup.entry_type),
                            entry_signal_id=setup.setup_id,
                            entry_signal_strength=setup.quality_mult if hasattr(setup, 'quality_mult') else 0.5,
                            active_filters=[],
                            passed_filters=[],
                            strategy_params={"final_risk_dollars": setup.final_risk_dollars},
                            expected_entry_price=setup.fill_price,
                            sizing_inputs={
                                "target_risk_pct": self._base_risk_pct if hasattr(self, '_base_risk_pct') else 0.01,
                                "account_equity": self._equity,
                                "volatility_basis": setup.final_risk_dollars,
                                "sizing_model": "breakout_r_risk",
                            },
                        )
```

**Step 3: Replace exit hook at engine.py:1551-1569**

```python
                if self._kit:
                    self._kit.log_exit(
                        trade_id=setup.setup_id,
                        exit_price=exit_price,
                        exit_reason=reason,
                    )
```

**Step 4: Replace Hook 1 at engine.py:758-766**

```python
        if self._kit:
            for sym in self._config:
                self._kit.capture_snapshot(sym)
```

**Step 5: Commit**

```bash
git add strategy_3/engine.py strategy_3/main.py
git commit -m "refactor(BREAKOUT): replace instrumentation boilerplate with InstrumentationKit"
```

---

## Task 9: Integrate Kit into Strategy 4 (Keltner)

**Files:**
- Modify: `strategy_4/engine.py`
- Modify: `strategy_4/main.py`

**Step 1: Update main.py**

```python
kit = bootstrap_kit(strategy_id="KELTNER_MOMENTUM", symbols=symbol_list, data_provider=data_provider)
```

**Step 2: Replace entry hook at engine.py:665-690**

```python
            if self._kit:
                side_str = "LONG" if direction == Direction.LONG else "SHORT"
                self._kit.log_entry(
                    trade_id=f"{symbol}_{pos.entry_time.isoformat()}",
                    pair=symbol,
                    side=side_str,
                    entry_price=fill_price,
                    position_size=float(fill_qty),
                    position_size_quote=fill_price * fill_qty,
                    entry_signal="keltner_breakout",
                    entry_signal_id=f"{symbol}_kelt_{pos.entry_time.isoformat()}",
                    entry_signal_strength=0.5,
                    active_filters=[],
                    passed_filters=[],
                    strategy_params={"stop_dist": stop_dist, "r_price": r_price},
                    expected_entry_price=fill_price,
                    sizing_inputs={
                        "target_risk_pct": self._base_risk_pct if hasattr(self, '_base_risk_pct') else 0.01,
                        "account_equity": self._equity if hasattr(self, '_equity') else 0.0,
                        "volatility_basis": stop_dist,
                        "sizing_model": "keltner_atr",
                    },
                )
```

**Step 3: Replace exit hooks at engine.py:703-722, 732-751**

```python
                if self._kit:
                    tid = f"{symbol}_{pos.entry_time.isoformat()}"
                    self._kit.log_exit(
                        trade_id=tid,
                        exit_price=fill_price,
                        exit_reason="STOP_LOSS",  # or "SIGNAL"
                    )
```

**Step 4: Replace Hook 1 at engine.py:196-204**

```python
        if self._kit:
            for sym in self._config:
                self._kit.capture_snapshot(sym)
```

**Step 5: Commit**

```bash
git add strategy_4/engine.py strategy_4/main.py
git commit -m "refactor(KELTNER): replace instrumentation boilerplate with InstrumentationKit"
```

---

## Task 10: Hooks Manifest (Documentation)

**Files:**
- Create: `instrumentation/HOOKS_MANIFEST.md`

Addresses feedback gap #5: hooks.py lacks documentation on which hooks are implemented per strategy.

**Step 1: Create the manifest**

Create `instrumentation/HOOKS_MANIFEST.md`:

```markdown
# Instrumentation Hooks Manifest

Maps strategy classes to their instrumented events via InstrumentationKit.

## Hook Points

| Hook | Kit Method | When Fired |
|------|-----------|------------|
| Trade Entry | `kit.log_entry()` | After entry fill confirmed |
| Trade Exit | `kit.log_exit()` | After exit fill confirmed (stop, signal, flatten) |
| Missed Opportunity | `kit.log_missed()` | Signal blocked by filter/gate/allocator |
| Market Snapshot | `kit.capture_snapshot()` | Each decision cycle (periodic) |
| Regime Classification | Auto (inside log_entry) | Automatically on every entry |
| Process Scoring | Auto (inside log_exit) | Automatically on every exit |

## Per-Strategy Coverage

### ATRSS (strategy/)
| Event | Location | Signal Types |
|-------|----------|-------------|
| Entry | `engine.py:_on_fill` | PULLBACK, BREAKOUT, REVERSE, ADDON_A, ADDON_B |
| Exit (stop) | `engine.py:_close_position` | STOP_LOSS |
| Exit (flatten) | `engine.py:_flatten_position` | FLATTEN, BIAS_FLIP, TIMEOUT, STALL |
| Missed (quality) | `engine.py:_scan_signals` | quality_gate, momentum, reentry |
| Missed (allocator) | `engine.py:_allocate` | allocator rejection |
| Missed (short) | `engine.py:_scan_signals` | short_disabled, short_gate_fail |
| Snapshot | `engine.py:_decision_cycle` | All symbols each cycle |

### AKC_HELIX (strategy_2/)
| Event | Location | Signal Types |
|-------|----------|-------------|
| Entry | `engine.py:_on_entry_fill` | CLASS_A, CLASS_B, CLASS_C, CLASS_D |
| Exit (stop) | `engine.py:_on_stop_fill` | STOP_LOSS |
| Exit (flatten) | `engine.py:_flatten_setup` | FLATTEN |
| Missed (allocator) | `engine.py:_allocate` | allocator rejection |
| Snapshot | `engine.py:_decision_cycle` | All symbols each cycle |

### SWING_BREAKOUT_V3 (strategy_3/)
| Event | Location | Signal Types |
|-------|----------|-------------|
| Entry | `engine.py:_process_oms_events` | ENTRY_A, ENTRY_B, ENTRY_C |
| Exit (stop) | `engine.py:_on_stop_fill` | STOP_LOSS, TP, TRAIL |
| Snapshot | `engine.py:_decision_cycle` | All symbols each cycle |

### KELTNER_MOMENTUM (strategy_4/)
| Event | Location | Signal Types |
|-------|----------|-------------|
| Entry | `engine.py:_on_fill` | keltner_breakout |
| Exit (stop) | `engine.py:_on_fill` (role=stop) | STOP_LOSS |
| Exit (signal) | `engine.py:_on_fill` (role=signal_exit) | SIGNAL |
| Snapshot | `engine.py:_decision_cycle` | All symbols each cycle |
```

**Step 2: Commit**

```bash
git add instrumentation/HOOKS_MANIFEST.md
git commit -m "docs: add hooks manifest mapping strategies to instrumented events"
```

---

## Task 11: Post-Exit Price Tracking

**Files:**
- Create: `instrumentation/src/post_exit_tracker.py`
- Test: `instrumentation/tests/test_post_exit_tracker.py`
- Modify: `instrumentation/src/bootstrap.py` (wire into context)

Addresses highest-impact #5: backfill 1h/4h post-exit price movement on completed trades.

**Step 1: Write the failing test**

Create `instrumentation/tests/test_post_exit_tracker.py`:

```python
"""Tests for post-exit price tracker."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock


def test_backfill_computes_post_exit_movement(tmp_path):
    """Backfill should compute 1h and 4h price movement after exit."""
    from instrumentation.src.post_exit_tracker import PostExitTracker

    # Write a completed trade
    trades_dir = tmp_path / "trades"
    trades_dir.mkdir()
    exit_time = datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc)
    trade = {
        "trade_id": "t1",
        "pair": "QQQ",
        "side": "LONG",
        "exit_price": 500.0,
        "exit_time": exit_time.isoformat(),
        "stage": "exit",
        "post_exit_1h": None,
        "post_exit_4h": None,
    }
    trade_file = trades_dir / f"trades_{exit_time.strftime('%Y-%m-%d')}.jsonl"
    trade_file.write_text(json.dumps(trade) + "\n")

    # Mock data provider that returns prices at +1h and +4h
    data_provider = MagicMock()
    data_provider.get_price_at.side_effect = lambda sym, ts: {
        exit_time + timedelta(hours=1): 505.0,
        exit_time + timedelta(hours=4): 510.0,
    }.get(ts)

    tracker = PostExitTracker(data_dir=str(tmp_path), data_provider=data_provider)
    results = tracker.run_backfill()

    assert len(results) == 1
    assert results[0]["post_exit_1h_pct"] == 1.0  # (505-500)/500 * 100
    assert results[0]["post_exit_4h_pct"] == 2.0  # (510-500)/500 * 100


def test_backfill_skips_already_filled(tmp_path):
    """Trades with post_exit data already filled should be skipped."""
    from instrumentation.src.post_exit_tracker import PostExitTracker

    trades_dir = tmp_path / "trades"
    trades_dir.mkdir()
    trade = {
        "trade_id": "t1",
        "pair": "QQQ",
        "side": "LONG",
        "exit_price": 500.0,
        "exit_time": "2026-03-01T14:00:00+00:00",
        "stage": "exit",
        "post_exit_1h_pct": 1.0,
        "post_exit_4h_pct": 2.0,
    }
    trade_file = trades_dir / "trades_2026-03-01.jsonl"
    trade_file.write_text(json.dumps(trade) + "\n")

    tracker = PostExitTracker(data_dir=str(tmp_path), data_provider=MagicMock())
    results = tracker.run_backfill()
    assert len(results) == 0


def test_backfill_skips_recent_trades(tmp_path):
    """Trades exited less than 4h ago should not be backfilled yet."""
    from instrumentation.src.post_exit_tracker import PostExitTracker

    trades_dir = tmp_path / "trades"
    trades_dir.mkdir()
    recent_exit = datetime.now(timezone.utc) - timedelta(hours=1)
    trade = {
        "trade_id": "t_recent",
        "pair": "QQQ",
        "side": "LONG",
        "exit_price": 500.0,
        "exit_time": recent_exit.isoformat(),
        "stage": "exit",
    }
    trade_file = trades_dir / f"trades_{recent_exit.strftime('%Y-%m-%d')}.jsonl"
    trade_file.write_text(json.dumps(trade) + "\n")

    tracker = PostExitTracker(data_dir=str(tmp_path), data_provider=MagicMock())
    results = tracker.run_backfill()
    assert len(results) == 0
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest instrumentation/tests/test_post_exit_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `instrumentation/src/post_exit_tracker.py`:

```python
"""Post-exit price tracker — backfills 1h/4h price movement after trade exit.

Run periodically (e.g. every 30 min) to enrich completed trades with
post-exit price data for exit timing analysis.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("instrumentation.post_exit_tracker")

_MIN_AGE_HOURS = 4  # Wait at least 4h after exit before backfilling


class PostExitTracker:
    """Backfills post-exit price movement on completed trades."""

    def __init__(self, data_dir: str, data_provider):
        self._trades_dir = Path(data_dir) / "trades"
        self._results_dir = Path(data_dir) / "post_exit"
        self._results_dir.mkdir(parents=True, exist_ok=True)
        self._data_provider = data_provider

    def run_backfill(self) -> list[dict]:
        """Scan recent trade files and backfill post-exit prices.

        Returns list of backfilled result dicts.
        """
        results = []
        now = datetime.now(timezone.utc)

        if not self._trades_dir.exists():
            return results

        for filepath in sorted(self._trades_dir.glob("trades_*.jsonl")):
            with open(filepath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        trade = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if trade.get("stage") != "exit":
                        continue
                    if trade.get("post_exit_1h_pct") is not None:
                        continue

                    exit_time_str = trade.get("exit_time")
                    if not exit_time_str:
                        continue

                    exit_time = datetime.fromisoformat(exit_time_str)
                    if (now - exit_time).total_seconds() < _MIN_AGE_HOURS * 3600:
                        continue

                    result = self._backfill_trade(trade, exit_time)
                    if result:
                        results.append(result)

        if results:
            self._write_results(results)

        return results

    def _backfill_trade(self, trade: dict, exit_time: datetime) -> Optional[dict]:
        """Compute post-exit price movement for a single trade."""
        try:
            symbol = trade["pair"]
            exit_price = trade["exit_price"]
            side = trade["side"]

            price_1h = self._data_provider.get_price_at(symbol, exit_time + timedelta(hours=1))
            price_4h = self._data_provider.get_price_at(symbol, exit_time + timedelta(hours=4))

            if price_1h is None or price_4h is None:
                return None

            # Compute % move from exit price
            move_1h = (price_1h - exit_price) / exit_price * 100
            move_4h = (price_4h - exit_price) / exit_price * 100

            # For SHORT trades, favorable = price going down
            if side == "SHORT":
                move_1h = -move_1h
                move_4h = -move_4h

            return {
                "trade_id": trade["trade_id"],
                "pair": symbol,
                "side": side,
                "exit_price": exit_price,
                "exit_time": trade["exit_time"],
                "post_exit_1h_pct": round(move_1h, 4),
                "post_exit_4h_pct": round(move_4h, 4),
                "price_1h": price_1h,
                "price_4h": price_4h,
            }
        except Exception as e:
            logger.debug("Post-exit backfill failed for %s: %s", trade.get("trade_id"), e)
            return None

    def _write_results(self, results: list[dict]) -> None:
        """Append results to daily JSONL file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = self._results_dir / f"post_exit_{today}.jsonl"
        with open(filepath, "a") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest instrumentation/tests/test_post_exit_tracker.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add instrumentation/src/post_exit_tracker.py instrumentation/tests/test_post_exit_tracker.py
git commit -m "feat: add post-exit price tracker for 1h/4h movement backfill"
```

---

## Task 12: PostgreSQL-Instrumentation Bridge

**Files:**
- Create: `instrumentation/src/pg_bridge.py`
- Test: `instrumentation/tests/test_pg_bridge.py`

Addresses feedback gap #4: PostgreSQL trade data doesn't flow through instrumentation. Creates a lightweight bridge that emits instrumentation events when TradeRecorder writes to PG, eliminating the dual source of truth.

**Step 1: Write the failing test**

Create `instrumentation/tests/test_pg_bridge.py`:

```python
"""Tests for PG-Instrumentation bridge."""
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone
from decimal import Decimal
import pytest


@pytest.mark.asyncio
async def test_bridge_emits_entry_event_on_record_entry():
    """Bridge should call kit.log_entry when TradeRecorder.record_entry is called."""
    from instrumentation.src.pg_bridge import InstrumentedTradeRecorder

    inner = AsyncMock()
    inner.record_entry = AsyncMock(return_value="trade_123")

    kit = MagicMock()

    bridge = InstrumentedTradeRecorder(inner, kit)

    trade_id = await bridge.record_entry(
        strategy_id="ATRSS",
        instrument="QQQ",
        direction="LONG",
        quantity=10,
        entry_price=Decimal("500.00"),
        entry_ts=datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc),
        setup_tag="PULLBACK",
        entry_type="PULLBACK",
        meta={"regime": "trending_up"},
    )

    assert trade_id == "trade_123"
    inner.record_entry.assert_awaited_once()
    kit.log_entry.assert_called_once()
    call_kwargs = kit.log_entry.call_args.kwargs
    assert call_kwargs["pair"] == "QQQ"
    assert call_kwargs["side"] == "LONG"


@pytest.mark.asyncio
async def test_bridge_emits_exit_event_on_record_exit():
    """Bridge should call kit.log_exit when TradeRecorder.record_exit is called."""
    from instrumentation.src.pg_bridge import InstrumentedTradeRecorder

    inner = AsyncMock()
    inner.record_exit = AsyncMock()

    kit = MagicMock()

    bridge = InstrumentedTradeRecorder(inner, kit)

    await bridge.record_exit(
        trade_id="trade_123",
        exit_price=Decimal("510.00"),
        exit_ts=datetime(2026, 3, 1, 16, 0, tzinfo=timezone.utc),
        exit_reason="STOP",
        realized_r=Decimal("1.5"),
    )

    inner.record_exit.assert_awaited_once()
    kit.log_exit.assert_called_once()
    call_kwargs = kit.log_exit.call_args.kwargs
    assert call_kwargs["trade_id"] == "trade_123"
    assert call_kwargs["exit_price"] == 510.0
    assert call_kwargs["exit_reason"] == "STOP"


@pytest.mark.asyncio
async def test_bridge_pg_write_succeeds_even_if_kit_fails():
    """PG write must succeed even if instrumentation fails."""
    from instrumentation.src.pg_bridge import InstrumentedTradeRecorder

    inner = AsyncMock()
    inner.record_entry = AsyncMock(return_value="trade_123")

    kit = MagicMock()
    kit.log_entry.side_effect = RuntimeError("instrumentation down")

    bridge = InstrumentedTradeRecorder(inner, kit)

    trade_id = await bridge.record_entry(
        strategy_id="ATRSS",
        instrument="QQQ",
        direction="LONG",
        quantity=10,
        entry_price=Decimal("500.00"),
        entry_ts=datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc),
    )

    assert trade_id == "trade_123"  # PG write succeeded
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest instrumentation/tests/test_pg_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `instrumentation/src/pg_bridge.py`:

```python
"""PG-Instrumentation bridge — wraps TradeRecorder to emit instrumentation events.

Decorator pattern: wraps the existing TradeRecorder so PG writes and
instrumentation writes happen in a single call. PG is primary — if
instrumentation fails, the PG write still succeeds.

Usage::

    from shared.services.trade_recorder import TradeRecorder
    from instrumentation.src.pg_bridge import InstrumentedTradeRecorder

    pg_recorder = TradeRecorder(store)
    bridge = InstrumentedTradeRecorder(pg_recorder, kit)

    # Use bridge everywhere you'd use pg_recorder
    trade_id = await bridge.record_entry(...)
    await bridge.record_exit(...)
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

logger = logging.getLogger("instrumentation.pg_bridge")


class InstrumentedTradeRecorder:
    """Wraps TradeRecorder to emit instrumentation events alongside PG writes."""

    def __init__(self, inner, kit):
        self._inner = inner
        self._kit = kit

    async def record_entry(
        self,
        strategy_id: str,
        instrument: str,
        direction: str,
        quantity: int,
        entry_price: Decimal,
        entry_ts: datetime,
        setup_tag: str = None,
        entry_type: str = None,
        meta: dict = None,
        account_id: str = "default",
    ) -> str:
        """Record entry to PG + emit instrumentation event."""
        trade_id = await self._inner.record_entry(
            strategy_id=strategy_id,
            instrument=instrument,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            entry_ts=entry_ts,
            setup_tag=setup_tag,
            entry_type=entry_type,
            meta=meta,
            account_id=account_id,
        )

        try:
            self._kit.log_entry(
                trade_id=trade_id,
                pair=instrument,
                side=direction,
                entry_price=float(entry_price),
                position_size=float(quantity),
                position_size_quote=float(entry_price) * quantity,
                entry_signal=entry_type or setup_tag or "",
                entry_signal_id=trade_id,
                entry_signal_strength=0.5,
                active_filters=[],
                passed_filters=[],
                strategy_params=meta or {},
                exchange_timestamp=entry_ts,
            )
        except Exception as e:
            logger.debug("PG bridge entry instrumentation failed: %s", e)

        return trade_id

    async def record_exit(
        self,
        trade_id: str,
        exit_price: Decimal,
        exit_ts: datetime,
        exit_reason: str,
        realized_r: Decimal,
        realized_usd: Decimal = None,
        notes: str = None,
        mae_r: Decimal = None,
        mfe_r: Decimal = None,
        duration_seconds: int = None,
        duration_bars: int = None,
        max_adverse_price: Decimal = None,
        max_favorable_price: Decimal = None,
    ) -> None:
        """Record exit to PG + emit instrumentation event."""
        await self._inner.record_exit(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_ts=exit_ts,
            exit_reason=exit_reason,
            realized_r=realized_r,
            realized_usd=realized_usd,
            notes=notes,
            mae_r=mae_r,
            mfe_r=mfe_r,
            duration_seconds=duration_seconds,
            duration_bars=duration_bars,
            max_adverse_price=max_adverse_price,
            max_favorable_price=max_favorable_price,
        )

        try:
            self._kit.log_exit(
                trade_id=trade_id,
                exit_price=float(exit_price),
                exit_reason=exit_reason,
                exchange_timestamp=exit_ts,
            )
        except Exception as e:
            logger.debug("PG bridge exit instrumentation failed: %s", e)

    async def record(self, data: dict) -> str:
        """Pass-through to inner record (single-call trades)."""
        return await self._inner.record(data)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest instrumentation/tests/test_pg_bridge.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add instrumentation/src/pg_bridge.py instrumentation/tests/test_pg_bridge.py
git commit -m "feat: add PG-instrumentation bridge to unify trade data sources"
```

---

## Task 13: Add Post-Exit Fields to TradeEvent

**Files:**
- Modify: `instrumentation/src/trade_logger.py` (add 2 fields to TradeEvent)

Small addendum — the TradeEvent schema should include post-exit fields so the post_exit_tracker results can be correlated back.

**Step 1: Add fields to TradeEvent dataclass**

After the `portfolio_state_at_entry` field, add:

```python
    # Post-exit price tracking (backfilled by PostExitTracker)
    post_exit_1h_pct: Optional[float] = None
    post_exit_4h_pct: Optional[float] = None
```

**Step 2: Run all tests**

Run: `python -m pytest instrumentation/tests/ -v`
Expected: PASS

**Step 3: Commit**

```bash
git add instrumentation/src/trade_logger.py
git commit -m "feat: add post_exit_1h_pct and post_exit_4h_pct fields to TradeEvent"
```

---

## Task 14: Update Sidecar Event Types for Post-Exit

**Files:**
- Modify: `instrumentation/src/sidecar.py`

Add `post_exit` to the directory-to-event-type mapping so the sidecar forwards post-exit backfill results.

**Step 1: Update _DIR_TO_EVENT_TYPE**

Add this entry to the mapping dict:

```python
    "post_exit": "post_exit",
```

**Step 2: Run tests**

Run: `python -m pytest instrumentation/tests/ -v`
Expected: PASS

**Step 3: Commit**

```bash
git add instrumentation/src/sidecar.py
git commit -m "feat: sidecar forwards post_exit backfill events to relay"
```

---

## Summary

| Task | Addresses | Change Type |
|------|-----------|-------------|
| 1 | Highest Impact #1, #2, #3 | Schema enrichment |
| 2 | Critical Gap #1, Highest Impact #4 | Kit facade |
| 3 | Highest Impact #1, #2, #3 | TradeLogger integration |
| 4 | Critical Gap #6 | Per-strategy bot_id |
| 5 | Critical Gap #1 | Factory helper |
| 6-9 | Critical Gap #1, #2, Highest Impact #1-4 | Strategy integration |
| 10 | Critical Gap #5 | Documentation |
| 11 | Highest Impact #5 | Post-exit tracking |
| 12 | Critical Gap #4 | PG bridge |
| 13-14 | Highest Impact #5 | Schema + sidecar |

**Pre-existing (no work):** Critical Gap #3 (Docker volumes already mounted)

**Total estimated commits:** 14
