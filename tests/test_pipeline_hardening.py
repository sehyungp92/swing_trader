from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shared.oms.events.bus import EventBus
from shared.oms.execution.router import ExecutionRouter, OrderPriority
from shared.oms.models.events import OMSEvent, OMSEventType
from shared.oms.models.instrument import Instrument
from shared.oms.models.order import OMSOrder, OrderRole, OrderSide, OrderStatus, OrderType
from shared.oms.persistence.in_memory import InMemoryRepository
from strategy_3.config import SymbolConfig as BreakoutSymbolConfig
from strategy_3.engine import BreakoutEngine
from strategy_3.models import CampaignState, Direction as BreakoutDirection, EntryType, ExitTier, PositionState, SetupInstance, SetupState
from strategy_4.config import SymbolConfig as KeltnerSymbolConfig
from strategy_4.engine import KeltnerEngine
from strategy_4.models import Direction as KeltnerDirection


def _make_instrument(symbol: str = "QQQ") -> Instrument:
    return Instrument(
        symbol=symbol,
        root=symbol,
        venue="SMART",
        tick_size=0.01,
        tick_value=0.01,
        multiplier=1.0,
        currency="USD",
    )


def _make_order(strategy_id: str, status: OrderStatus) -> OMSOrder:
    now = datetime.now(timezone.utc)
    return OMSOrder(
        strategy_id=strategy_id,
        instrument=_make_instrument(),
        side=OrderSide.BUY,
        qty=5,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
        role=OrderRole.ENTRY,
        status=status,
        reject_reason="blocked by test",
        created_at=now,
        last_update_at=now,
        filled_qty=2,
        remaining_qty=3,
        avg_fill_price=101.25,
    )


async def _run_breakout_event(engine: BreakoutEngine, event: OMSEvent) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    engine._running = True
    task = asyncio.create_task(engine._process_events(queue))
    await queue.put(event)
    await asyncio.sleep(0.05)
    engine._running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _make_breakout_filled_setup(
    *,
    fill_qty: int = 10,
    qty_open: int | None = None,
    fill_price: float = 100.0,
) -> SetupInstance:
    open_qty = fill_qty if qty_open is None else qty_open
    return SetupInstance(
        symbol="QQQ",
        direction=BreakoutDirection.LONG,
        entry_type=EntryType.A_AVWAP_RETEST,
        state=SetupState.FILLED,
        campaign_id=1,
        box_version=1,
        entry_price=100.0,
        stop0=95.0,
        final_risk_dollars=500.0,
        quality_mult=1.0,
        expiry_mult=1.0,
        shares_planned=fill_qty,
        current_stop=95.0,
        oca_group="BRK_QQQ_1",
        exit_tier=ExitTier.NEUTRAL,
        fill_price=fill_price,
        fill_qty=fill_qty,
        fill_ts=datetime.now(timezone.utc),
        avg_entry=fill_price,
        qty_open=open_qty,
    )


@pytest.mark.asyncio
async def test_event_bus_broadcasts_global_risk_halt_and_order_payloads() -> None:
    bus = EventBus()
    strategy_a = bus.subscribe("strategy_a")
    strategy_b = bus.subscribe("strategy_b")
    global_q = bus.subscribe_all()

    bus.emit_risk_halt("", "callback exception")

    event_a = strategy_a.get_nowait()
    event_b = strategy_b.get_nowait()
    event_global = global_q.get_nowait()
    assert event_a.event_type == OMSEventType.RISK_HALT
    assert event_b.event_type == OMSEventType.RISK_HALT
    assert event_global.payload["reason"] == "callback exception"

    order = _make_order("strategy_a", OrderStatus.REJECTED)
    bus.emit_order_event(order)

    reject_event = strategy_a.get_nowait()
    assert reject_event.event_type == OMSEventType.ORDER_REJECTED
    assert reject_event.payload["reject_reason"] == "blocked by test"
    assert reject_event.payload["role"] == OrderRole.ENTRY.value
    assert reject_event.payload["order_type"] == OrderType.LIMIT.value
    assert reject_event.payload["filled_qty"] == 2


@pytest.mark.asyncio
async def test_execution_router_expires_stale_queued_orders_while_congested() -> None:
    adapter = SimpleNamespace(is_congested=True, submit_order=AsyncMock())
    repo = InMemoryRepository()
    bus = EventBus()
    queue = bus.subscribe("router_test")
    router = ExecutionRouter(adapter, repo, bus=bus)

    order = _make_order("router_test", OrderStatus.RISK_APPROVED)
    await repo.save_order(order)
    router._queue.append(  # noqa: SLF001 - targeted regression coverage
        (
            OrderPriority.NEW_ENTRY,
            order,
            {"queued_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
        )
    )

    await router._expire_stale_queued_orders()  # noqa: SLF001 - targeted regression coverage

    updated = await repo.get_order(order.oms_order_id)
    assert updated is not None
    assert updated.status == OrderStatus.EXPIRED
    assert len(router._queue) == 0  # noqa: SLF001 - targeted regression coverage
    assert adapter.submit_order.await_count == 0
    assert repo._events[-1]["event_type"] == "QUEUE_EXPIRED"  # noqa: SLF001 - targeted regression coverage
    expired_event = queue.get_nowait()
    assert expired_event.event_type == OMSEventType.ORDER_EXPIRED


@pytest.mark.asyncio
async def test_keltner_engine_uses_fill_event_price_and_qty_payload() -> None:
    oms = SimpleNamespace(
        submit_intent=AsyncMock(return_value=SimpleNamespace(oms_order_id="stop-1"))
    )
    engine = KeltnerEngine(
        strategy_id="S5_TEST",
        ib_session=None,
        oms_service=oms,
        instruments={"QQQ": _make_instrument()},
        config={"QQQ": KeltnerSymbolConfig(symbol="QQQ")},
    )
    engine._running = True
    engine._pending_entry["QQQ"] = {
        "direction": KeltnerDirection.LONG,
        "stop_dist": 2.5,
    }
    engine._order_to_symbol["entry-1"] = "QQQ"
    engine._order_role["entry-1"] = "entry"

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(engine._process_events(queue))
    await queue.put(
        OMSEvent(
            event_type=OMSEventType.FILL,
            timestamp=datetime.now(timezone.utc),
            strategy_id="S5_TEST",
            oms_order_id="entry-1",
            payload={"price": 101.5, "qty": 3},
        )
    )
    await asyncio.sleep(0.05)
    engine._running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    position = engine.positions["QQQ"]
    assert position.fill_price == 101.5
    assert position.qty == 3
    assert position.initial_stop == pytest.approx(99.0)
    assert oms.submit_intent.await_count == 1


@pytest.mark.asyncio
async def test_breakout_engine_uses_fill_event_payload_and_real_event_names() -> None:
    oms = SimpleNamespace(
        submit_intent=AsyncMock(
            side_effect=[
                SimpleNamespace(oms_order_id="stop-1"),
                SimpleNamespace(oms_order_id="tp1-1"),
                SimpleNamespace(oms_order_id="tp2-1"),
            ]
        )
    )
    engine = BreakoutEngine(
        ib_session=None,
        oms_service=oms,
        instruments={"QQQ": _make_instrument()},
        config={"QQQ": BreakoutSymbolConfig(symbol="QQQ")},
    )
    engine._running = True
    engine.campaigns["QQQ"].state = CampaignState.BREAKOUT

    setup = SetupInstance(
        symbol="QQQ",
        direction=BreakoutDirection.LONG,
        entry_type=EntryType.A_AVWAP_RETEST,
        state=SetupState.ARMED,
        campaign_id=1,
        box_version=1,
        entry_price=100.0,
        stop0=95.0,
        final_risk_dollars=500.0,
        quality_mult=1.0,
        expiry_mult=1.0,
        shares_planned=10,
        current_stop=95.0,
        oca_group="BRK_QQQ_1",
        exit_tier=ExitTier.NEUTRAL,
    )
    engine.active_setups[setup.setup_id] = setup
    engine._order_to_setup["entry-1"] = setup.setup_id
    engine._order_kind["entry-1"] = "primary_entry"

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(engine._process_events(queue))
    await queue.put(
        OMSEvent(
            event_type=OMSEventType.FILL,
            timestamp=datetime.now(timezone.utc),
            strategy_id="BREAKOUT",
            oms_order_id="entry-1",
            payload={"price": 101.0, "qty": 10},
        )
    )
    await asyncio.sleep(0.05)
    engine._running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    position = engine.positions["QQQ"]
    assert setup.state == SetupState.FILLED
    assert setup.fill_price == 101.0
    assert setup.fill_qty == 10
    assert position.qty == 10
    assert engine._order_kind["stop-1"] == "stop"
    assert engine._order_kind["tp1-1"] == "tp1"
    assert engine._order_kind["tp2-1"] == "tp2"


@pytest.mark.asyncio
async def test_breakout_engine_applies_add_fill_to_live_position_state() -> None:
    engine = BreakoutEngine(
        ib_session=None,
        oms_service=SimpleNamespace(submit_intent=AsyncMock()),
        instruments={"QQQ": _make_instrument()},
        config={"QQQ": BreakoutSymbolConfig(symbol="QQQ")},
    )
    engine.campaigns["QQQ"].state = CampaignState.POSITION_OPEN

    setup = _make_breakout_filled_setup(fill_qty=10, qty_open=10, fill_price=100.0)
    engine.active_setups[setup.setup_id] = setup
    engine.positions["QQQ"] = PositionState(
        symbol="QQQ",
        direction=BreakoutDirection.LONG,
        qty=10,
        avg_cost=100.0,
        current_stop=95.0,
        campaign_id=1,
        box_version=1,
    )
    engine._track_order("add-1", setup.setup_id, "add_entry", 5)  # noqa: SLF001

    await _run_breakout_event(
        engine,
        OMSEvent(
            event_type=OMSEventType.FILL,
            timestamp=datetime.now(timezone.utc),
            strategy_id="BREAKOUT",
            oms_order_id="add-1",
            payload={"price": 110.0, "qty": 5},
        ),
    )

    position = engine.positions["QQQ"]
    assert setup.state == SetupState.ACTIVE
    assert setup.fill_qty == 15
    assert setup.qty_open == 15
    assert setup.avg_entry == pytest.approx((100.0 * 10 + 110.0 * 5) / 15)
    assert setup.add_count == 1
    assert position.qty == 15
    assert position.avg_cost == pytest.approx(setup.avg_entry)
    assert position.add_count == 1
    assert "add-1" not in engine._order_kind


@pytest.mark.asyncio
async def test_breakout_engine_applies_tp_fill_to_live_position_state() -> None:
    engine = BreakoutEngine(
        ib_session=None,
        oms_service=SimpleNamespace(submit_intent=AsyncMock()),
        instruments={"QQQ": _make_instrument()},
        config={"QQQ": BreakoutSymbolConfig(symbol="QQQ")},
    )
    engine.campaigns["QQQ"].state = CampaignState.POSITION_OPEN

    setup = _make_breakout_filled_setup(fill_qty=9, qty_open=9, fill_price=100.0)
    setup.stop_order_id = "stop-1"
    setup.tp1_order_id = "tp1-1"
    setup.tp2_order_id = "tp2-1"
    engine.active_setups[setup.setup_id] = setup
    engine.positions["QQQ"] = PositionState(
        symbol="QQQ",
        direction=BreakoutDirection.LONG,
        qty=9,
        avg_cost=100.0,
        current_stop=95.0,
        campaign_id=1,
        box_version=1,
    )
    engine._track_order("stop-1", setup.setup_id, "stop", 9)  # noqa: SLF001
    engine._track_order("tp1-1", setup.setup_id, "tp1", 3)  # noqa: SLF001
    engine._track_order("tp2-1", setup.setup_id, "tp2", 3)  # noqa: SLF001

    await _run_breakout_event(
        engine,
        OMSEvent(
            event_type=OMSEventType.FILL,
            timestamp=datetime.now(timezone.utc),
            strategy_id="BREAKOUT",
            oms_order_id="tp1-1",
            payload={"price": 103.0, "qty": 3},
        ),
    )

    position = engine.positions["QQQ"]
    assert setup.tp1_done is True
    assert position.tp1_done is True
    assert setup.state == SetupState.FILLED
    assert setup.qty_open == 6
    assert position.qty == 6
    assert setup.realized_pnl == pytest.approx(9.0)
    assert setup.tp1_order_id == ""
    assert setup.stop_order_id == "stop-1"
    assert engine._order_kind["stop-1"] == "stop"


@pytest.mark.asyncio
async def test_breakout_engine_applies_stop_fill_to_live_position_state() -> None:
    engine = BreakoutEngine(
        ib_session=None,
        oms_service=SimpleNamespace(submit_intent=AsyncMock()),
        instruments={"QQQ": _make_instrument()},
        config={"QQQ": BreakoutSymbolConfig(symbol="QQQ")},
    )
    engine.campaigns["QQQ"].state = CampaignState.POSITION_OPEN

    setup = _make_breakout_filled_setup(fill_qty=10, qty_open=10, fill_price=100.0)
    setup.stop_order_id = "stop-1"
    setup.tp1_order_id = "tp1-1"
    setup.tp2_order_id = "tp2-1"
    engine.active_setups[setup.setup_id] = setup
    engine.positions["QQQ"] = PositionState(
        symbol="QQQ",
        direction=BreakoutDirection.LONG,
        qty=10,
        avg_cost=100.0,
        current_stop=95.0,
        campaign_id=1,
        box_version=1,
    )
    engine._track_order("stop-1", setup.setup_id, "stop", 10)  # noqa: SLF001
    engine._track_order("tp1-1", setup.setup_id, "tp1", 3)  # noqa: SLF001
    engine._track_order("tp2-1", setup.setup_id, "tp2", 3)  # noqa: SLF001

    await _run_breakout_event(
        engine,
        OMSEvent(
            event_type=OMSEventType.FILL,
            timestamp=datetime.now(timezone.utc),
            strategy_id="BREAKOUT",
            oms_order_id="stop-1",
            payload={"price": 94.0, "qty": 10},
        ),
    )

    position = engine.positions["QQQ"]
    campaign = engine.campaigns["QQQ"]
    assert setup.state == SetupState.CLOSED
    assert setup.qty_open == 0
    assert position.qty == 0
    assert setup.realized_pnl == pytest.approx(-60.0)
    assert setup.r_state == pytest.approx(-0.12)
    assert setup.stop_order_id == ""
    assert setup.tp1_order_id == ""
    assert setup.tp2_order_id == ""
    assert campaign.last_exit_direction == BreakoutDirection.LONG
    assert "stop-1" not in engine._order_kind
    assert "tp1-1" not in engine._order_kind
    assert "tp2-1" not in engine._order_kind
