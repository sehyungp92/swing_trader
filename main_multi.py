"""Unified multi-strategy launcher — runs all 5 strategies in one process.

Strategies (by priority): ATRSS(0), S5_PB(1), S5_DUAL(2), Breakout(3), Helix(4).
Shares a single IBKR adapter, OMS, and StrategyCoordinator across all strategies.
Cross-strategy coordination rules (tighten Helix stop on ATRSS entry, size boost)
are implemented via the shared coordinator.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("multi_strategy")


async def main() -> None:
    """Wire up shared IB session, multi-strategy OMS, and start all engines."""
    from shared.ibkr_core.config.loader import IBKRConfig
    from shared.ibkr_core.client.session import IBSession
    from shared.ibkr_core.mapping.contract_factory import ContractFactory
    from shared.ibkr_core.adapters.execution_adapter import IBKRExecutionAdapter
    from shared.oms.services.factory import build_multi_strategy_oms
    from shared.oms.risk.calculator import RiskCalculator
    from shared.services.bootstrap import bootstrap_database
    from shared.market_calendar import MarketCalendar

    # Strategy imports
    from strategy.config import (
        STRATEGY_ID as ATRSS_ID,
        SYMBOL_CONFIGS as ATRSS_CONFIGS,
        build_instruments as atrss_build_instruments,
    )
    from strategy.engine import ATRSSEngine

    from strategy_2.config import (
        STRATEGY_ID as HELIX_ID,
        SYMBOL_CONFIGS as HELIX_CONFIGS,
        build_instruments as helix_build_instruments,
    )
    from strategy_2.engine import HelixEngine

    from strategy_3.config import (
        STRATEGY_ID as BREAKOUT_ID,
        SYMBOL_CONFIGS as BREAKOUT_CONFIGS,
        build_instruments as breakout_build_instruments,
    )
    from strategy_3.engine import BreakoutEngine

    from strategy_4.config import (
        S5_PB_STRATEGY_ID, S5_DUAL_STRATEGY_ID,
        S5_PB_CONFIGS, S5_DUAL_CONFIGS,
        build_instruments as s5_build_instruments,
    )
    from strategy_4.engine import KeltnerEngine

    from shared.overlay.config import OverlayConfig
    from shared.overlay.engine import OverlayEngine

    # -------------------------------------------------------------------
    # 1. Load IBKR configuration
    # -------------------------------------------------------------------
    config_dir = Path(__file__).resolve().parent / "config"
    ibkr_config = IBKRConfig(config_dir)
    logger.info(
        "Loaded IBKR config: host=%s port=%d",
        ibkr_config.profile.host, ibkr_config.profile.port,
    )

    # -------------------------------------------------------------------
    # 2. Create and connect IB session (shared)
    # -------------------------------------------------------------------
    session = IBSession(ibkr_config)
    await session.start()
    await session.wait_ready()
    logger.info("IB session connected")

    # -------------------------------------------------------------------
    # 3. Create execution adapter (shared)
    # -------------------------------------------------------------------
    contract_factory = ContractFactory(
        ib=session.ib,
        templates=ibkr_config.contracts,
        routes=ibkr_config.routes,
    )
    adapter = IBKRExecutionAdapter(
        session=session,
        contract_factory=contract_factory,
        account=ibkr_config.profile.account_id,
    )

    # -------------------------------------------------------------------
    # 4. Bootstrap database (graceful degradation)
    # -------------------------------------------------------------------
    bootstrap_ctx = await bootstrap_database()
    trade_recorder = bootstrap_ctx.trade_recorder

    # -------------------------------------------------------------------
    # 4b. Bootstrap instrumentation (per-strategy kits)
    # -------------------------------------------------------------------
    instrumentation_ctx = None
    atrss_kit = None
    helix_kit = None
    breakout_kit = None
    s5_pb_kit = None
    s5_dual_kit = None
    try:
        from instrumentation.src.bootstrap import bootstrap_instrumentation, bootstrap_kit

        # Shared context for overlay engine and periodic tasks (daily snapshots, backfill)
        all_symbols = sorted(set(
            list(ATRSS_CONFIGS) + list(HELIX_CONFIGS) + list(BREAKOUT_CONFIGS)
            + list(S5_PB_CONFIGS) + list(S5_DUAL_CONFIGS)
        ))
        instrumentation_ctx = bootstrap_instrumentation(symbols=all_symbols)
        logger.info("Instrumentation bootstrapped for %s", all_symbols)

        # Per-strategy InstrumentationKits (each gets its own bot_id)
        atrss_kit = bootstrap_kit(
            strategy_id=ATRSS_ID,
            symbols=list(ATRSS_CONFIGS.keys()),
        )
        logger.info("ATRSS InstrumentationKit bootstrapped")

        helix_kit = bootstrap_kit(
            strategy_id=HELIX_ID,
            symbols=list(HELIX_CONFIGS.keys()),
        )
        logger.info("AKC_HELIX InstrumentationKit bootstrapped")

        breakout_kit = bootstrap_kit(
            strategy_id=BREAKOUT_ID,
            symbols=list(BREAKOUT_CONFIGS.keys()),
        )
        logger.info("SWING_BREAKOUT_V3 InstrumentationKit bootstrapped")

        s5_pb_kit = bootstrap_kit(
            strategy_id=S5_PB_STRATEGY_ID,
            symbols=list(S5_PB_CONFIGS.keys()),
        )
        logger.info("S5_PB InstrumentationKit bootstrapped")

        s5_dual_kit = bootstrap_kit(
            strategy_id=S5_DUAL_STRATEGY_ID,
            symbols=list(S5_DUAL_CONFIGS.keys()),
        )
        logger.info("S5_DUAL InstrumentationKit bootstrapped")
    except Exception:
        logger.warning("Instrumentation bootstrap failed — running without instrumentation", exc_info=True)

    # -------------------------------------------------------------------
    # 5. Register instruments from all strategies (union)
    # -------------------------------------------------------------------
    atrss_instruments = atrss_build_instruments()
    helix_instruments = helix_build_instruments()
    breakout_instruments = breakout_build_instruments()
    s5_pb_instruments = s5_build_instruments(S5_PB_CONFIGS)
    s5_dual_instruments = s5_build_instruments(S5_DUAL_CONFIGS)
    # Merge all instruments (InstrumentRegistry is global singleton)
    all_instruments = {**atrss_instruments, **helix_instruments,
                       **breakout_instruments, **s5_pb_instruments,
                       **s5_dual_instruments}
    logger.info(
        "Registered instruments: ATRSS=%s, Helix=%s, Breakout=%s, S5_PB=%s, S5_DUAL=%s",
        list(atrss_instruments), list(helix_instruments), list(breakout_instruments),
        list(s5_pb_instruments), list(s5_dual_instruments),
    )

    # -------------------------------------------------------------------
    # 6. Fetch account equity
    # -------------------------------------------------------------------
    equity = 100_000.0  # fallback default
    try:
        accounts = session.ib.managedAccounts()
        if accounts:
            summary = await session.ib.accountSummaryAsync(accounts[0])
            for item in summary:
                if item.tag == "NetLiquidation" and item.currency == "USD":
                    equity = float(item.value)
                    logger.info("Account equity from IB: $%.2f", equity)
                    break
    except Exception:
        logger.warning("Could not fetch equity from IB, using default $%.2f", equity)

    # -------------------------------------------------------------------
    # 6b. Overlay configuration
    # -------------------------------------------------------------------
    overlay_config = OverlayConfig(
        enabled=True,
        symbols=["QQQ", "GLD"],
        max_equity_pct=0.85,
        ema_fast=13,
        ema_slow=48,
        ema_overrides={"QQQ": (10, 21), "GLD": (13, 21)},
        weights=None,
        state_file=str(Path(__file__).resolve().parent / "overlay_state.json"),
    )

    # -------------------------------------------------------------------
    # 7. Compute per-strategy unit_risk_dollars
    # -------------------------------------------------------------------
    atrss_urd = RiskCalculator.compute_unit_risk_dollars(
        nav=equity, unit_risk_pct=0.012,  # 1.2% base risk (optimized_v1)
    )
    helix_urd = RiskCalculator.compute_unit_risk_dollars(
        nav=equity, unit_risk_pct=0.005,  # 0.50% base risk per Helix spec
    )
    breakout_urd = RiskCalculator.compute_unit_risk_dollars(
        nav=equity, unit_risk_pct=0.005,  # 0.50% base risk per Breakout spec
    )
    s5_pb_urd = RiskCalculator.compute_unit_risk_dollars(
        nav=equity, unit_risk_pct=0.008,  # 0.80% base risk per S5_PB spec
    )
    s5_dual_urd = RiskCalculator.compute_unit_risk_dollars(
        nav=equity, unit_risk_pct=0.008,  # 0.80% base risk per S5_DUAL spec
    )

    # -------------------------------------------------------------------
    # 8. Build shared multi-strategy OMS
    # -------------------------------------------------------------------
    market_cal = MarketCalendar()

    oms, coordinator = await build_multi_strategy_oms(
        adapter=adapter,
        strategies=[
            {
                "id": ATRSS_ID,
                "unit_risk_dollars": atrss_urd,
                "daily_stop_R": 2.0,
                "priority": 0,       # highest expectancy
                "max_heat_R": 1.00,
                "max_working_orders": 4,
            },
            {
                "id": S5_PB_STRATEGY_ID,
                "unit_risk_dollars": s5_pb_urd,
                "daily_stop_R": 2.0,
                "priority": 1,       # 80% WR on IBIT (optimized_v2)
                "max_heat_R": 1.50,
                "max_working_orders": 2,
            },
            {
                "id": S5_DUAL_STRATEGY_ID,
                "unit_risk_dollars": s5_dual_urd,
                "daily_stop_R": 2.0,
                "priority": 2,       # 70.7% WR on GLD+IBIT (optimized_v2)
                "max_heat_R": 1.50,
                "max_working_orders": 2,
            },
            {
                "id": BREAKOUT_ID,
                "unit_risk_dollars": breakout_urd,
                "daily_stop_R": 2.0,
                "priority": 3,       # rare signals (3 trades), priority barely matters
                "max_heat_R": 0.65,
                "max_working_orders": 2,
            },
            {
                "id": HELIX_ID,
                "unit_risk_dollars": helix_urd,
                "daily_stop_R": 2.5,
                "priority": 4,       # 34% WR, high stale-exit rate — lowest priority
                "max_heat_R": 0.85,
                "max_working_orders": 4,
            },
        ],
        heat_cap_R=2.0,  # expanded (optimized_v2)
        portfolio_daily_stop_R=3.0,
        db_pool=bootstrap_ctx.pool,
        market_calendar=market_cal,
    )

    # -------------------------------------------------------------------
    # 8b. Wire coordinator action logger
    # -------------------------------------------------------------------
    if instrumentation_ctx and getattr(instrumentation_ctx, 'coordination_logger', None):
        coordinator.set_action_logger(instrumentation_ctx.coordination_logger.log_action)
        logger.info("Coordinator action logger wired")

    # -------------------------------------------------------------------
    # 9. Start OMS
    # -------------------------------------------------------------------
    await oms.start()
    logger.info("Multi-strategy OMS started")

    # Start instrumentation sidecar (background thread)
    if instrumentation_ctx is not None:
        try:
            instrumentation_ctx.start()
        except Exception:
            logger.warning("Instrumentation start failed", exc_info=True)

    # Start per-strategy InstrumentationKit contexts
    for kit_name, kit_obj in [
        ("ATRSS", atrss_kit), ("AKC_HELIX", helix_kit),
        ("SWING_BREAKOUT_V3", breakout_kit),
        ("S5_PB", s5_pb_kit), ("S5_DUAL", s5_dual_kit),
    ]:
        if kit_obj is not None:
            try:
                kit_obj._ctx.start()
            except Exception:
                logger.warning("%s Kit context start failed", kit_name, exc_info=True)

    # -------------------------------------------------------------------
    # 10. Create strategy engines (shared OMS, coordinator)
    # -------------------------------------------------------------------
    atrss_engine = ATRSSEngine(
        ib_session=session,
        oms_service=oms,
        instruments=atrss_instruments,
        config=ATRSS_CONFIGS,
        trade_recorder=trade_recorder,
        equity=equity,
        market_calendar=market_cal,
        kit=atrss_kit,
    )

    helix_engine = HelixEngine(
        ib_session=session,
        oms_service=oms,
        instruments=helix_instruments,
        config=HELIX_CONFIGS,
        trade_recorder=trade_recorder,
        equity=equity,
        coordinator=coordinator,
        market_calendar=market_cal,
        instrumentation_kit=helix_kit,
    )

    breakout_engine = BreakoutEngine(
        ib_session=session,
        oms_service=oms,
        instruments=breakout_instruments,
        config=BREAKOUT_CONFIGS,
        trade_recorder=trade_recorder,
        equity=equity,
        market_calendar=market_cal,
        instrumentation=breakout_kit,
    )

    s5_pb_engine = KeltnerEngine(
        strategy_id=S5_PB_STRATEGY_ID,
        ib_session=session,
        oms_service=oms,
        instruments=s5_pb_instruments,
        config=S5_PB_CONFIGS,
        trade_recorder=trade_recorder,
        equity=equity,
        market_calendar=market_cal,
        kit=s5_pb_kit,
    )

    s5_dual_engine = KeltnerEngine(
        strategy_id=S5_DUAL_STRATEGY_ID,
        ib_session=session,
        oms_service=oms,
        instruments=s5_dual_instruments,
        config=S5_DUAL_CONFIGS,
        trade_recorder=trade_recorder,
        equity=equity,
        market_calendar=market_cal,
        kit=s5_dual_kit,
    )

    overlay_engine = OverlayEngine(
        ib_session=session,
        equity=equity,
        config=overlay_config,
        market_calendar=market_cal,
        instrumentation=instrumentation_ctx,
    )

    # -------------------------------------------------------------------
    # 10b. Wire overlay state provider to all kits
    # -------------------------------------------------------------------
    if overlay_config.enabled:
        overlay_state_fn = overlay_engine.get_signals
        if instrumentation_ctx is not None:
            instrumentation_ctx.overlay_state_provider = overlay_state_fn
        for kit_obj in [atrss_kit, helix_kit, breakout_kit, s5_pb_kit, s5_dual_kit]:
            if kit_obj is not None:
                kit_obj.ctx.overlay_state_provider = overlay_state_fn

    # -------------------------------------------------------------------
    # 11. Start all engines
    # -------------------------------------------------------------------
    await atrss_engine.start()
    logger.info("ATRSS engine started (priority 0, symbols: %s)", list(ATRSS_CONFIGS))
    await s5_pb_engine.start()
    logger.info("S5_PB engine started (priority 1, symbols: %s)", list(S5_PB_CONFIGS))
    await s5_dual_engine.start()
    logger.info("S5_DUAL engine started (priority 2, symbols: %s)", list(S5_DUAL_CONFIGS))
    await breakout_engine.start()
    logger.info("Breakout engine started (priority 3, symbols: %s)", list(BREAKOUT_CONFIGS))
    await helix_engine.start()
    logger.info("Helix engine started (priority 4, symbols: %s)", list(HELIX_CONFIGS))

    if overlay_config.enabled:
        await overlay_engine.start()
        logger.info("Overlay engine started (symbols: %s, max_pct: %.0f%%)",
                     overlay_config.symbols, overlay_config.max_equity_pct * 100)

    # -------------------------------------------------------------------
    # 11b. Launch instrumentation periodic tasks
    # -------------------------------------------------------------------
    _daily_snapshot_task = None
    _backfill_task = None
    _heartbeat_task = None

    if instrumentation_ctx is not None:
        async def _run_daily_snapshot() -> None:
            """Build + save daily snapshot at 16:05 ET each trading day."""
            from zoneinfo import ZoneInfo
            et = ZoneInfo("America/New_York")
            while True:
                try:
                    now_et = datetime.now(timezone.utc).astimezone(et)
                    # Next 16:05 ET
                    target = now_et.replace(hour=16, minute=5, second=0, microsecond=0)
                    if target <= now_et:
                        target += timedelta(days=1)
                    # Skip weekends
                    while target.weekday() >= 5:
                        target += timedelta(days=1)
                    delay = (target - now_et).total_seconds()
                    await asyncio.sleep(delay)
                    try:
                        snap = instrumentation_ctx.daily_builder.build()
                        instrumentation_ctx.daily_builder.save(snap)
                        logger.info("Daily instrumentation snapshot saved")
                    except Exception:
                        logger.warning("Daily snapshot build failed", exc_info=True)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.warning("Daily snapshot task error", exc_info=True)
                    await asyncio.sleep(300)

        async def _run_backfill() -> None:
            """Run missed-opportunity backfill every 5 minutes."""
            while True:
                try:
                    await asyncio.sleep(300)
                    try:
                        instrumentation_ctx.missed_logger.run_backfill(data_provider=None)
                    except Exception:
                        logger.debug("Backfill cycle error", exc_info=True)
                except asyncio.CancelledError:
                    break

        async def _run_heartbeat() -> None:
            """Emit portfolio heartbeat every 60 seconds."""
            import time as _time
            start_time = _time.monotonic()
            error_counter = 0
            while True:
                try:
                    await asyncio.sleep(60)
                    uptime = _time.monotonic() - start_time
                    active_positions = (
                        len(getattr(atrss_engine, "positions", {}))
                        + len(getattr(helix_engine, "active_setups", {}))
                        + len(getattr(breakout_engine, "active_setups", {}))
                        + len(getattr(s5_pb_engine, "positions", {}))
                        + len(getattr(s5_dual_engine, "positions", {}))
                    )
                    open_orders = (
                        len(getattr(atrss_engine, "pending_orders", {}))
                        + len(getattr(helix_engine, "pending_setups", {}))
                        + len(getattr(s5_pb_engine, "_pending_entry", {}))
                        + len(getattr(s5_dual_engine, "_pending_entry", {}))
                    )
                    atrss_kit.emit_heartbeat(
                        active_positions=active_positions,
                        open_orders=open_orders,
                        uptime_s=uptime,
                        error_count_1h=error_counter,
                    )
                except asyncio.CancelledError:
                    break
                except Exception:
                    error_counter += 1
                    await asyncio.sleep(60)

        _daily_snapshot_task = asyncio.create_task(_run_daily_snapshot())
        _backfill_task = asyncio.create_task(_run_backfill())
        _heartbeat_task = asyncio.create_task(_run_heartbeat())
        logger.info("Instrumentation periodic tasks started")

    # -------------------------------------------------------------------
    # 12. Run until interrupted
    # -------------------------------------------------------------------
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            pass

    logger.info(
        "Multi-strategy runner active — ATRSS + S5_PB + S5_DUAL + Breakout + Helix + Overlay — press Ctrl+C to stop"
    )

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    # -------------------------------------------------------------------
    # 13. Graceful shutdown
    # M7: Correct ordering: engines → OMS → database → broker connection
    # Engines must stop first (cancel pending orders, stop scheduling).
    # OMS stops next (drain queues, flush state).
    # Database closes after OMS has flushed.
    # Broker connection closes last.
    # -------------------------------------------------------------------
    logger.info("Shutting down …")

    # 0. Cancel instrumentation periodic tasks
    if _daily_snapshot_task is not None:
        _daily_snapshot_task.cancel()
    if _backfill_task is not None:
        _backfill_task.cancel()
    if _heartbeat_task is not None:
        _heartbeat_task.cancel()

    # 0b. Build final daily snapshot before engines stop
    if instrumentation_ctx is not None:
        try:
            snap = instrumentation_ctx.daily_builder.build()
            instrumentation_ctx.daily_builder.save(snap)
            logger.info("Final daily instrumentation snapshot saved")
        except Exception:
            logger.debug("Final daily snapshot failed", exc_info=True)

    # 1. Stop overlay engine (independent of OMS, stop before strategy engines)
    if overlay_config.enabled:
        await overlay_engine.stop()
        logger.info("Overlay engine stopped")

    # 1b. Stop strategy engines (highest level — stop generating intents)
    await breakout_engine.stop()
    await helix_engine.stop()
    await s5_dual_engine.stop()
    await s5_pb_engine.stop()
    await atrss_engine.stop()
    logger.info("All strategy engines stopped")

    # 2. Stop OMS (drain execution queue, flush pending state)
    await oms.stop()
    logger.info("OMS stopped")

    # 2b. Stop instrumentation (sidecar thread)
    if instrumentation_ctx is not None:
        try:
            instrumentation_ctx.stop()
            logger.info("Instrumentation stopped")
        except Exception:
            logger.debug("Instrumentation stop failed", exc_info=True)

    # 2c. Stop per-strategy InstrumentationKit contexts
    for kit_name, kit_obj in [
        ("ATRSS", atrss_kit), ("AKC_HELIX", helix_kit),
        ("SWING_BREAKOUT_V3", breakout_kit),
        ("S5_PB", s5_pb_kit), ("S5_DUAL", s5_dual_kit),
    ]:
        if kit_obj is not None:
            try:
                kit_obj._ctx.stop()
            except Exception:
                logger.debug("%s Kit context stop failed", kit_name, exc_info=True)

    # 3. Close database (after OMS has flushed all state)
    if bootstrap_ctx.has_db:
        from shared.services.bootstrap import shutdown_database
        await shutdown_database(bootstrap_ctx)
        logger.info("Database shutdown complete")

    # 4. Disconnect broker (last — no more messages to send/receive)
    await session.stop()
    logger.info("IB session disconnected")

    logger.info("Multi-strategy shutdown complete")


def _setup_logging() -> None:
    """Configure structured logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


if __name__ == "__main__":
    _setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
