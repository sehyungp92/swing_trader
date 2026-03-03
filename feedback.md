# Ecosystem Evaluation: Trading Bot Repositories + Trading Assistant Orchestrator

## Context

This evaluation assesses whether the trading bot repositories (`k_stock_trader`, `momentum_trader`, `swing_trader`) and the `trading_assistant` orchestrator collectively form an optimal system for monitoring, analyzing, and continuously improving trading performance. The focus is on three dimensions:

1. **Data supply** — Are the bots capturing the right data, in the right format, with the right granularity?
2. **Data flow** — Is the integration between bots and orchestrator robust and complete?
3. **Improvement capability** — Can the orchestrator not only tune parameters but propose meaningful structural improvements?

---

## 1. K_STOCK_TRADER (Korean Equity Multi-Strategy Bot)

### What It Does Well

- **Comprehensive event capture.** `TradeEvent` in `instrumentation/src/trade_logger.py` captures 30+ fields per trade: entry/exit prices, signal metadata, regime, filters, slippage, latency, strategy params frozen at entry, and full market snapshots at both entry and exit. This is among the richest trade instrumentation I've seen.
- **Missed opportunity tracking with outcome backfill.** `MissedOpportunityLogger` in `instrumentation/src/missed_opportunity.py` records every blocked signal, then asynchronously backfills hypothetical outcomes at 1h/4h/24h using per-strategy simulation policies. This is the single most valuable data source for filter optimization.
- **Process quality scoring.** `ProcessScorer` in `instrumentation/src/process_scorer.py` assigns 0-100 scores with a controlled 21-element root cause taxonomy, per-strategy YAML rules, and separate classification of process vs. outcome. This correctly separates luck from skill at the individual trade level.
- **InstrumentationKit facade.** `instrumentation/facade.py` provides a clean 6-method API (`on_entry_fill`, `on_exit_fill`, `on_signal_blocked`, `periodic_tick`, `build_daily_snapshot`, `shutdown`) that never crashes the strategy. This is exactly how instrumentation should integrate.
- **Sidecar with HMAC signing.** `instrumentation/src/sidecar.py` handles local-first logging, watermark-based dedup, batched forwarding with exponential backoff, and sort_keys canonicalization for HMAC verification. Robust and well-designed.
- **Per-strategy simulation policies in YAML.** Different strategies (KMP, KPR, PCIM, NULRIMOK) have different TP/SL logic and cost assumptions for hypothetical outcome calculation. Simulation assumptions are transparent and auditable.

### Critical Gaps

1. **No signal confluence logging.** The `entry_signal` field is a bare string (e.g., "kmp_breakout"). The orchestrator has no idea what combination of indicators/conditions caused the signal to fire. Without this, the system can optimize when to trade (regime gates, filter thresholds) but cannot assess whether the signal logic itself is good. **Recommendation:** Log the top 3-5 confluence factors that triggered the signal (e.g., `["RSI_oversold", "volume_spike_2.3x", "MA_cross_confirmed", "sector_momentum_positive"]`) with their numeric values. This is the highest-value single improvement across all bots.

2. **No position sizing decision logging.** The system records `position_size` and `position_size_quote` but not the sizing model's inputs: target risk (R), account equity at time, volatility-adjusted size, any scaling factors applied. Without this, the assistant cannot assess whether position sizing is optimal. **Recommendation:** Add `sizing_model: str`, `target_risk_pct: float`, `account_equity: float`, `volatility_at_sizing: float` to TradeEvent.

3. **Active filters logged without threshold context.** `active_filters: ["volume_gate", "spread_gate"]` and `passed_filters: ["regime_gate"]` are recorded, but not the threshold values. The assistant knows a filter was active but can't tell the user "volume_gate threshold is 1.5x — the actual volume was 1.3x, so you missed by 13%". **Recommendation:** Change `active_filters` from `list[str]` to `list[dict]` with `{name, threshold, actual_value, passed: bool}`.

4. **Regime classifier is single-timeframe.** `RegimeClassifier` uses 50-period MA + ADX(14) + ATR percentile on a single timeframe. This misses multi-timeframe regime context (e.g., trending on H1 but ranging on D1). For Korean equities with strong sector rotation, sector-level regime would also be valuable. **Recommendation:** Add a `regime_context` dict to events: `{primary_regime, higher_tf_regime, sector_regime}`.

5. **No error event type forwarded via sidecar.** Instrumentation errors (`instrumentation_errors_*.jsonl`) are logged locally but never forwarded. The sidecar maps the `errors/` directory to the `error` event type, but these are instrumentation failures, not trading bot errors. Actual bot exceptions (OMS failures, API errors, connectivity issues) need a separate, explicitly emitted error event. **Recommendation:** Add an explicit `emit_error(severity, error_type, message, stack_trace)` method to `InstrumentationKit`.

6. **No heartbeat event.** The daily snapshot provides end-of-day health, but there's no real-time heartbeat. If the bot goes silent, the orchestrator won't know until the evening when the daily snapshot is expected. **Recommendation:** Add `emit_heartbeat()` to the facade, called every 30 seconds, forwarded by sidecar as `heartbeat` event type.

7. **KIS REST bid/ask limitation.** MarketSnapshot always records `bid=0, ask=0, spread_bps=0` because KIS REST doesn't provide real-time bid/ask. This means slippage analysis lacks market microstructure context. **Recommendation:** If KIS WebSocket is available, use it for snapshots. If not, document this limitation explicitly in the event so the assistant doesn't draw conclusions from zero spread.

8. **No tracking of how price moved after exit.** The system records exit price but not what happened next. Was the exit premature (price continued favorably)? Was it optimal (price reversed)? The process scorer checks `price_moved_pct` but this field isn't populated by the trade logger. **Recommendation:** Backfill post-exit price movement at 1h/4h intervals (similar to missed opportunity backfill) and populate `price_moved_pct` and `stop_distance_pct` on the exit event.

---

## 2. MOMENTUM_TRADER (NQ/MNQ Futures Multi-Strategy Bot)

### What It Does Well

- **OMS EventBus integration.** `InstrumentationManager` in `instrumentation/src/bootstrap.py` subscribes to the OMS event bus and automatically logs `RISK_DENIAL` events as missed opportunities. This is a cleaner integration pattern than the facade approach — events are captured automatically rather than requiring manual `on_*` calls.
- **Shared instrumentation codebase.** Uses the same core modules (trade_logger, missed_logger, process_scorer, etc.) as other bots, ensuring uniform event schemas.
- **Per-strategy IDs.** Each strategy gets its own `bot_id` (`helix_trendwrap`, `nqdtc`, `vdubus_swing`) allowing the orchestrator to analyze them independently.

### Critical Gaps

1. **No InstrumentationKit facade.** Unlike k_stock_trader, there's no facade.py. The `InstrumentationManager.bootstrap.py` only handles OMS `RISK_DENIAL` events automatically. Direct trade entry/exit logging must still be called manually from strategy code, but without a clean facade, this is fragile and likely incomplete. **Recommendation:** Create a facade.py (or use the same one from k_stock_trader adapted for futures).

2. **Cross-strategy coordination not instrumented.** The bot has a `StrategyCoordinator` that enforces proximity cooldowns (120-min between Helix-NQDTC during 09:45-11:30), direction filters, heat caps (3.5R), and daily stops (1.5R). None of these decisions are logged. When the orchestrator sees a missed opportunity blocked by "risk_gateway", it has no context about which coordination rule fired. **Recommendation:** Log coordinator decisions: `{rule: "proximity_cooldown", blocking_strategy: "helix", blocked_strategy: "nqdtc", cooldown_remaining_min: 45}`.

3. **Drawdown tier transitions not logged.** The bot has drawdown-based size reduction tiers (8%, 12%, 15%). When these fire, position sizing changes, but neither the transition nor the reduced sizing is recorded. **Recommendation:** Emit a `drawdown_tier_change` event with `{tier: "tier_2", drawdown_pct: 12.3, size_reduction_pct: 50}`.

4. **No concurrent position tracking.** The max 3 concurrent positions rule exists but there's no logging of concurrent position count at entry time. If the assistant sees reduced opportunity capture, it can't distinguish "no signals" from "signals blocked by concurrency limit". **Recommendation:** Add `concurrent_positions_at_entry: int` to TradeEvent.

5. **Futures-specific data missing from events.** For NQ/MNQ futures, the system should capture: contract expiry/roll information, margin utilization at entry, session (RTH vs ETH), tick value. These are critical for accurate PnL attribution and cost modeling. **Recommendation:** Add `session: str` (RTH/ETH), `contract_month: str`, `margin_used_pct: float` to TradeEvent.

6. **RISK_DENIAL handler lacks signal context.** The `_handle_risk_denial` method logs missed opportunities with `signal_strength=0.0` and a generic pair. The actual signal that was blocked, its strength, and the specific symbol are lost. **Recommendation:** Enrich the OMS RISK_DENIAL payload to include the originating signal details.

---

## 3. SWING_TRADER (ETF/Futures Multi-Strategy Bot)

### What It Does Well

- **Bootstrap factory pattern.** `bootstrap_instrumentation()` in `instrumentation/src/bootstrap.py` provides a clean one-function setup that returns an `InstrumentationContext` with all services wired.
- **Context module.** Has a dedicated `context.py` for multi-strategy aggregation.
- **Hooks module.** `hooks.py` provides a framework for injecting instrumentation into strategy hot paths without modifying strategy code directly.
- **Dashboard integration.** Next.js dashboard with 30-second polling, real-time views of positions/orders/equity — this is valuable for manual oversight.
- **Relay service on-VPS.** The only bot that has the relay service deployed alongside it, reducing network hops.

### Critical Gaps

1. **No InstrumentationKit facade (same as momentum_trader).** Strategies must manually call trade_logger methods. Without a clean facade, it's likely that some events are missed or logged inconsistently. **Recommendation:** Create a unified facade matching k_stock_trader's API.

2. **Multi-asset correlation not captured.** This bot trades 8 instruments across ETFs and futures. The instrumentation captures each trade independently but doesn't log the portfolio state at entry time: total exposure, directional bias, correlated positions. The orchestrator's `compute_portfolio_risk.py` can compute this after the fact, but the bot itself should log its view of portfolio state when making trade decisions. **Recommendation:** Add `portfolio_state_at_entry: {total_exposure_pct, net_direction, num_positions, correlated_positions: list[str]}` to TradeEvent.

3. **No Docker Compose for instrumentation.** The bot runs in Docker but instrumentation data lives on the filesystem. If the container restarts, unsent events could be lost if the sidecar buffer isn't mounted as a volume. **Recommendation:** Ensure `instrumentation/data/` is a Docker volume mount.

4. **PostgreSQL trade data not forwarded.** The bot writes trades to a PostgreSQL `trades` table via a TradeRecorder, but this data doesn't flow through the instrumentation layer. There are potentially two sources of truth for trade data. **Recommendation:** Either make the instrumentation layer the canonical source (and populate PG from it), or add a PG -> JSONL export for sidecar forwarding.

5. **Hooks module lacks documentation on which hooks are implemented.** `hooks.py` exists but without clear indication of which strategy events are actually hooked. This makes it unclear whether all trades are being captured. **Recommendation:** Add a `HOOKED_EVENTS` manifest that maps strategy classes to their instrumented events.

6. **5 strategies, single bot_id.** All 5 strategies (ATRSS, S5_PB, S5_DUAL, SWING_BREAKOUT_V3, AKC_HELIX) share `bot_id: swing_multi_01`. The orchestrator can't distinguish performance by strategy within this bot. **Recommendation:** Use per-strategy bot_ids like k_stock_trader does, or add a `strategy_id` field to all events.

---

## 4. TRADING_ASSISTANT (Orchestrator)

### What It Does Well

- **Deterministic pre-processing.** The entire pipeline up to Claude invocation is deterministic: event routing (brain), severity classification, quality gate, daily metrics builder, portfolio risk computation, WFO fold generation, and strategy engine suggestions. Claude only handles interpretation and synthesis. This is architecturally sound — it keeps costs zero when idle and makes the system testable.
- **Rich curated data pipeline.** `DailyMetricsBuilder` in `skills/build_daily_metrics.py` produces 8 curated files per bot per day: summary, winners, losers, process failures, notable missed, regime analysis, filter analysis, root cause summary. This gives Claude excellent pre-digested context.
- **PromptPackage abstraction.** All prompt assemblers return a unified `PromptPackage` with system prompt, task prompt, data, instructions, corrections, and metadata. This ensures consistent context assembly and makes it easy to audit what Claude sees.
- **WFO pipeline.** Full walk-forward optimization with fold generation, per-fold optimization, OOS validation, cost sensitivity analysis, robustness testing (neighborhood + regime stability), leakage audit, and safety flags. This is a production-quality WFO implementation.
- **Feedback handler.** `analysis/feedback_handler.py` can parse structured corrections from user messages and store them in `corrections.jsonl`. The context builder loads these into every prompt, implementing a basic Ralph Loop.

### Critical Gaps — Data Utilization

1. **Filter analysis is value-only, not threshold-aware.** `DailyMetricsBuilder.filter_analysis()` counts blocks and computes saved/missed PnL per filter, but doesn't know what the threshold was or how close the blocked trade was to passing. With the current data from bots (filters as strings), it can only say "volume_gate blocked 8 trades, net cost $420." It cannot say "volume_gate threshold is 1.5x — 6 of 8 blocks had volume between 1.2x and 1.5x, suggesting a threshold reduction to 1.2x would capture these." **This is the single biggest analysis bottleneck.** Without threshold context from the bots, filter optimization is blunt.

2. **No slippage trend analysis.** Slippage data is captured per trade but never aggregated into trends. Over time, empirical slippage distributions should inform WFO cost models. Currently the WFO cost model uses fixed/spread-proportional/empirical modes, but the empirical mode has no data feed. **Recommendation:** Add a `SlippageAnalyzer` skill that computes per-symbol, per-timeofday slippage distributions from historical trades and feeds this into WFO cost models.

3. **No time-of-day analysis.** The orchestrator has no concept of intraday patterns. Many strategies perform very differently in the first vs. last hour of trading. Neither the daily metrics builder nor the strategy engine analyzes performance by time-of-day. **Recommendation:** Add time-of-day buckets to the curated data pipeline: `hourly_performance.json` with PnL, win rate, and process quality by hour.

4. **No drawdown attribution.** When a bot experiences drawdown, the system doesn't trace which specific decisions contributed most. Was it one large loss? A series of small losses in an adverse regime? Position sizing too aggressive during a losing streak? **Recommendation:** Add a `DrawdownAnalyzer` that segments drawdown periods into episodes and attributes each to root causes.

### Critical Gaps — Improvement Capability

5. **Strategy engine is extremely limited.** `StrategyEngine` in `analysis/strategy_engine.py` has only 3 rules:
   - Tier 1: Detect tight stops (loss/win ratio < 0.3)
   - Tier 2: Detect costly filters (net_impact_pnl < 0)
   - Tier 3: Detect regime underperformance (losing for 3+ weeks in a regime)

   This is far too narrow. The engine cannot detect: convergence/divergence of signal types, decay in strategy alpha over time, seasonality effects, correlation breakdown, position sizing inefficiencies, or any structural pattern that isn't the 3 hardcoded rules. **Recommendation:** Expand the strategy engine with at least:
   - **Alpha decay detection:** Rolling 30/60/90-day Sharpe comparison. If 90d Sharpe is significantly higher than 30d, the strategy may be losing edge.
   - **Signal quality trending:** Track signal_strength -> outcome correlation over time. If correlation is declining, the signal may need recalibration.
   - **Exit timing analysis:** Compare actual exit timing to optimal exit (using post-exit price data). If exits are consistently premature, suggest trailing stop or wider TP.
   - **Correlation breakdown detection:** Track cross-bot return correlation. Rising correlation = rising systemic risk.
   - **Regime transition detection:** Don't wait 3 weeks — detect regime shifts intraday and flag expected strategy impact.

6. **Cannot propose structural changes.** The entire improvement pipeline (strategy engine + WFO) operates within the existing parameter space. It can tune stops, adjust filter thresholds, and enable/disable regime gates. It cannot:
   - Propose a new filter that doesn't exist yet
   - Suggest combining signals from different strategies
   - Identify that a strategy's core signal has decayed and needs replacement
   - Recommend adding a new indicator to the signal confluence
   - Suggest position sizing model changes (e.g., from fixed % to Kelly criterion)

   **This is the fundamental ceiling on the system's improvement capability.** The WFO can find the best parameters for a given structure, but it can't change the structure.

   **Recommendation:** This is where Claude's interpretive ability is most needed. The weekly prompt should explicitly ask Claude to assess structural weaknesses:
   - "Given the pattern of root causes over the past 30 days, are there structural changes to [bot]'s signal logic that could address the most common failure modes?"
   - "The filter analysis shows [filter] blocking high-quality signals — should this filter be restructured rather than just threshold-adjusted?"
   - "Signal strength correlation with outcome has declined from 0.72 to 0.41 over 60 days — does the signal need recalibration or replacement?"

   The key is giving Claude the right data to reason about structure, not just parameters.

7. **WFO cannot test new parameter dimensions.** The parameter space is fixed at configuration time (`WFOConfig.parameter_space`). If the system discovers that a new parameter should be optimized (e.g., "time_of_day_gate_start"), it can't add it to the WFO config autonomously. **Recommendation:** Allow Claude to propose parameter space expansions in weekly reports, with human approval required before adding new dimensions.

8. **No A/B testing framework.** When the system suggests a change (e.g., widen stop by 0.5 ATR), there's no mechanism to test it in a controlled way. The change either gets applied to all trades or not. **Recommendation:** Design a "shadow mode" where a parameter change can run in parallel with the current parameters, logging hypothetical outcomes for comparison without affecting real trading.

### Critical Gaps — Feedback Loop

9. **Corrections not written by handlers.** `FeedbackHandler.parse_correction()` can process user feedback, but no handler actually calls it. The feedback handler is wired into the analysis layer but not into the communication layer (Telegram/Discord callback handlers). **Recommendation:** Wire Telegram button callbacks and reply parsing to `FeedbackHandler`.

10. **Failure log not read by prompt assemblers.** The triage prompt assembler reads past rejections from the failure log, but the daily and weekly prompt assemblers don't. If a user rejected a suggestion last week (e.g., "don't widen that stop"), Claude will suggest it again. **Recommendation:** Load recent rejected suggestions into the daily/weekly prompt context.

11. **No suggestion outcome tracking.** When the system suggests "relax volume_gate threshold from 1.5x to 1.2x" and the user implements it, there's no mechanism to measure the impact. Did net PnL improve? Did drawdown increase? **Recommendation:** Add a `SuggestionTracker` that records: suggestion_id, suggestion_text, implemented_date, 7d/30d performance delta. This closes the loop and lets the system learn which types of suggestions produce positive outcomes.

12. **Proactive scanner is a skeleton.** `ProactiveScanner` in `skills/proactive_scanner.py` has 3 methods that format notifications from pre-computed inputs. It doesn't actually scan for anything — it just wraps data it's given. No anomaly detection, no pattern recognition, no correlation monitoring. **Recommendation:** Implement actual scanning logic: repeated error detection (wired to ErrorRateTracker), unusual loss detection (PnL > 2 sigma from 30d mean), heartbeat monitoring, regime shift detection.

### Critical Gaps — Integration

13. **Relay schema.sql missing from relay/ directory.** The relay service references a schema but the file isn't present. The relay uses an in-memory SQLite database — this should be disk-backed for crash resilience.

14. **Bot error events vs instrumentation errors.** The sidecar forwards `errors/instrumentation_errors_*.jsonl` as `error` event type. But these are instrumentation failures (e.g., "failed to capture snapshot"), not trading bot errors (e.g., "API connection lost", "order rejected"). The brain routes on event_type=error but there's a semantic mismatch. **Recommendation:** Bots should emit explicit error events with severity classification, separate from instrumentation error logs.

15. **No end-to-end event tracing.** An event goes: bot -> JSONL file -> sidecar -> relay -> orchestrator queue -> brain -> worker -> handler. If an event is lost or delayed, there's no way to trace where it dropped. **Recommendation:** Add a `trace_id` that follows the event through every layer, logged at each hop.

---

## 5. CROSS-CUTTING ASSESSMENT

### What Data Is Being Captured vs. What's Needed for Optimal Improvement

| Data Category | Captured | Quality | Impact on Improvement |
|---|---|---|---|
| Trade PnL & execution | Yes | Excellent | Enables basic performance analysis |
| Signal identification | Partial | Signal name only, no confluence | **Blocks signal quality analysis** |
| Filter decisions | Partial | Filter name, no thresholds/values | **Blocks precise filter optimization** |
| Market regime | Yes | Single-timeframe only | Limits regime-based strategy gating |
| Process quality | Yes | Excellent (21 root causes, per-strategy rules) | Enables process vs outcome separation |
| Missed opportunities | Yes | Excellent (backfilled outcomes, simulation transparency) | Enables filter cost analysis |
| Slippage/execution | Yes | Good | Can inform cost models |
| Position sizing inputs | No | Not captured | **Blocks sizing optimization** |
| Cross-strategy coordination | No | Not captured | **Blocks portfolio-level optimization** |
| Post-exit price movement | No | Not captured | **Blocks exit timing analysis** |
| Signal confluence factors | No | Not captured | **Blocks structural signal improvement** |
| Time-of-day patterns | No | Not aggregated | Blocks session-based optimization |
| Suggestion outcomes | No | Not tracked | **Blocks learning from past suggestions** |

### The Fundamental Limitation

The system is well-designed to answer: **"Given the current strategy structure, what are the best parameters?"** (via WFO) and **"Which existing filters are costing money?"** (via filter analysis).

It is poorly equipped to answer: **"Should the strategy structure itself change?"** — e.g., should we add a new indicator, change the signal logic, modify the exit strategy, or restructure the filter chain? These are the highest-value questions, and they require:

1. **Signal confluence data** — to assess whether individual signal components are still predictive
2. **Post-exit tracking** — to assess whether the exit logic is leaving money on the table
3. **Cross-strategy correlation** — to identify which strategy combinations are redundant
4. **Alpha decay metrics** — to detect when a strategy's edge is eroding before it becomes obvious in PnL
5. **A structured prompt** — that explicitly asks Claude to reason about structural changes, not just parameter tweaks

The good news is that the architecture supports this expansion. The bot instrumentation layers can be extended to capture more data, the curated data pipeline can be extended to aggregate new metrics, and the prompt assemblers can be extended to include structural analysis context. None of this requires architectural changes — only implementation.

---

## 6. PRIORITIZED RECOMMENDATIONS

### Highest Impact (Bot-Side Data Capture)

1. **Add signal confluence logging to all bots.** Change `entry_signal: str` to `entry_signal: str` + `signal_factors: list[dict]` with `{factor_name, factor_value, threshold, contribution}`. This is the single change that would most expand the system's improvement capability.

2. **Add filter threshold context.** Change `active_filters: list[str]` to `filter_decisions: list[dict]` with `{filter_name, threshold, actual_value, passed, margin_pct}`. This transforms filter optimization from "should we keep this filter?" to "what threshold maximizes edge?"

3. **Add position sizing inputs.** Log `target_risk_pct`, `account_equity`, `volatility_basis`, `sizing_model` on every trade.

4. **Create InstrumentationKit facades for momentum_trader and swing_trader.** All 3 bots should have the same clean integration API.

5. **Add post-exit price tracking.** Backfill 1h/4h post-exit price movement on completed trades.

### Highest Impact (Orchestrator Analysis)

6. **Expand the strategy engine** from 3 rules to 8+ detectors: alpha decay, signal quality decay, exit timing, correlation breakdown, regime transition, time-of-day patterns, position sizing efficiency, drawdown attribution.

7. **Wire the feedback loop.** Connect Telegram callbacks -> FeedbackHandler -> corrections.jsonl -> prompt assembler context. Also wire failure_log reading into daily/weekly assemblers.

8. **Add suggestion outcome tracking.** Record which suggestions were implemented and measure their 7d/30d impact.

9. **Enhance weekly prompts for structural analysis.** Explicitly ask Claude to assess structural weaknesses, not just parameter optimization opportunities.

10. **Implement the proactive scanner.** Transform from notification formatter to actual pattern detector.

### Foundation (Integration)

11. **Standardize error event emission.** Separate bot runtime errors from instrumentation errors. Add explicit `emit_error()` to all facades.

12. **Add per-strategy bot_ids for swing_trader.** Currently all 5 strategies share one ID.

13. **Add end-to-end event tracing.** `trace_id` through every pipeline stage.

14. **Deploy relay to VPS with disk-backed SQLite.**
