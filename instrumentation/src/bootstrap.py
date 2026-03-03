"""Bootstrap instrumentation — factory that reads config and builds all services.

Usage::

    from instrumentation.src.bootstrap import bootstrap_instrumentation
    ctx = bootstrap_instrumentation(symbols=["QQQ", "SPY"])
    ctx.start()
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("instrumentation.bootstrap")

_CONFIG_PATH = Path("instrumentation/config/instrumentation_config.yaml")


def bootstrap_instrumentation(
    symbols: list[str] | None = None,
    data_provider=None,
) -> "InstrumentationContext":
    """Create an InstrumentationContext with all services wired up.

    Args:
        symbols: Active trading symbols (populates market_snapshots.symbols).
        data_provider: Optional data source for snapshots/regime. None is fine —
            snapshots degrade gracefully to zeros.

    Returns:
        Fully wired InstrumentationContext ready for ``ctx.start()``.
    """
    from .context import InstrumentationContext
    from .market_snapshot import MarketSnapshotService
    from .trade_logger import TradeLogger
    from .missed_opportunity import MissedOpportunityLogger
    from .process_scorer import ProcessScorer
    from .daily_snapshot import DailySnapshotBuilder
    from .regime_classifier import RegimeClassifier
    from .sidecar import Sidecar

    config = _load_config()

    # Populate symbols into config
    if symbols:
        config.setdefault("market_snapshots", {})["symbols"] = list(symbols)

    snapshot_service = MarketSnapshotService(config, data_provider=data_provider)
    trade_logger = TradeLogger(config, snapshot_service)
    missed_logger = MissedOpportunityLogger(config, snapshot_service)
    process_scorer = ProcessScorer()
    daily_builder = DailySnapshotBuilder(config)
    regime_classifier = RegimeClassifier(data_provider=data_provider)
    sidecar = Sidecar(config)

    ctx = InstrumentationContext(
        snapshot_service=snapshot_service,
        trade_logger=trade_logger,
        missed_logger=missed_logger,
        process_scorer=process_scorer,
        daily_builder=daily_builder,
        regime_classifier=regime_classifier,
        sidecar=sidecar,
        data_dir=config.get("data_dir", "instrumentation/data"),
    )

    logger.info(
        "Instrumentation bootstrapped: symbols=%s, data_dir=%s",
        symbols, ctx.data_dir,
    )
    return ctx


def _load_config() -> dict:
    """Load instrumentation_config.yaml, falling back to defaults."""
    if _CONFIG_PATH.exists():
        try:
            import yaml
            with open(_CONFIG_PATH) as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Failed to load %s: %s — using defaults", _CONFIG_PATH, e)

    return {
        "bot_id": "swing_multi_01",
        "data_dir": "instrumentation/data",
        "data_source_id": "ibkr_execution",
        "market_snapshots": {"interval_seconds": 60, "symbols": []},
        "sidecar": {
            "relay_url": "",
            "batch_size": 50,
            "retry_max": 5,
            "poll_interval_seconds": 60,
        },
    }
