Below is a **detailed backtest + optimization spec** for the v3.3-ETF campaign strategy using **IB + Backtrader**, with a strong focus on:

* **Optimizing key variables**
* **Ablating filters/conditions** to learn what helps vs what gates profitable trades
* **Avoiding backtest lies** (ETF gaps, RTH-only trading, VWAP anchors, past-only quantiles, stop gap-through)
* Producing **decision-grade diagnostics** (virtual trades for blocked signals, filter attribution, regime breakdowns)

---

# 1) High-level goals

### 1.1 What we want to learn

1. **Does the edge exist after realistic ETF costs + gaps?**
2. Which conditions/filters:

   * Improve expectancy / reduce DD *without killing frequency*
   * Are overly restrictive and gate good trades
3. Where returns actually come from:

   * **Entry type** (A vs B vs C)
   * **Runner contribution**
   * **Adds contribution**
   * **Regime states**
   * **Symbol contributions**
4. How robust is the strategy across time, instruments, regimes?

### 1.2 What “success” looks like

* Positive expectancy after costs across most years and symbols
* No single-year or single-symbol dependency
* Reasonable trading frequency (you can set a target band; e.g. ~4–10 trades/month total across the 4 ETFs including adds)
* Controlled drawdowns with realistic gap modeling

---

# 2) Data specification (IB + local store)

### 2.1 Don’t backtest directly against live IB streaming

IB is a **broker feed**, not a research feed:

* pacing limits
* occasional missing bars
* historical revisions
* DST/holiday intricacies

**Spec:** Use IB only for *data acquisition*, then backtest from a **local database**.

### 2.2 What to store per symbol

For each of QQQ/USO/GLD/IBIT:

* **1 hour bars (RTH only)**: timestamp (ET), OHLCV
* **1 day bars (ETF session)**: OHLCV
* Optional: 5m bars to validate gap/stop assumptions or to refine fill modeling later

**Minimum history:**

* Hourly: 12–18 months (better: 3–5 years if possible)
* Daily: as much as available (≥ 3 years preferred)

### 2.3 Session calendar + timestamps

* Convert all bars to **America/New_York** timezone
* Define hour slots exactly: 09:30–10:30, …, 15:00–16:00
* Confirm you have a bar that represents the 15:00–16:00 period (IB sometimes timestamps bars at end time)

### 2.4 Corporate actions & adjusted prices

* For ETFs, decide: **Adjusted vs unadjusted**.

  * For signal stability: adjusted is often cleaner for long history (QQQ dividends etc.)
  * For execution realism: unadjusted is closer to trading prices

**Spec recommendation:**

* Use **adjusted** for indicator computations (ATR/EMA/regime)
* Use **unadjusted** for fills/PNL
  …but only if your data pipeline can keep them consistent. Otherwise, use **unadjusted** everywhere and accept small indicator drift.

---

# 3) Backtrader architecture

### 3.1 Multi-timeframe setup per symbol

You need both daily and hourly in the strategy simultaneously.

**Two viable patterns:**

1. Load **hourly bars** as primary data, then `cerebro.resampledata(..., timeframe=Days)` to build daily inside backtrader.
2. Load both hourly + daily feeds separately (safer when daily bars come from a different source).

**Spec recommendation:** (1) resample hourly to daily inside Backtrader, because:

* consistent session definitions
* avoids mismatch between daily bar construction and hourly session

### 3.2 Strategy structure

Implement the algorithm as:

* A per-symbol **CampaignState** object (mirroring your spec)
* A per-symbol **IndicatorCache** object
* A **RiskManager** (portfolio heat, correlation penalty, max positions, etc.)
* An **OrderManager** (limit TTLs, bracket orders, pending entries, MOO behavior)

### 3.3 Intrabar execution realism (Backtrader)

Backtrader is bar-based; you must specify a conservative fill policy.

**ETF execution rules to implement:**

* **Limit fills**: fill if bar trades through limit (buy if low ≤ limit, sell if high ≥ limit)
* **Market orders**: fill at next bar open (or current bar close if using cheat-on-close—but that can be optimistic)
* **MOO orders**: fill at next day open (RTH open)

**Spec recommendation for realism:**

* Use market orders that fill at **next bar open** (or next day open for daily signals)
* Avoid cheat-on-close for performance claims; you can run it as a sensitivity mode only.

---

# 4) Cost, slippage, and gap-through-stop modeling

### 4.1 Costs model (bps-based)

Implement per-symbol:

* commission per share (IBKR tiered) or a flat estimate
* spread/slippage as **bps** of price (round-trip estimate)

In backtests, apply costs at execution time:

* `cost = notional * bps`
* bps may vary by order type:

  * Limit = lower (maker-like)
  * Market/MOO = higher

**Required sensitivity sweep:**
e.g. slippage_bps multiplier ∈ {0.5×, 1.0×, 1.5×, 2.0×}

### 4.2 Stop gap-through rule (critical)

Implement:

* If the market opens beyond your stop (gap through), fill stop at **open price**, not stop price.

You can implement this by:

* checking at each new session open whether open crosses stop level
* if yes, force-close at open

**Required reporting:**

* count of gap-stop events
* average slippage vs stop level
* contribution to drawdown tails

---

# 5) Strategy correctness checks (before optimization)

Before optimizing anything, run “correctness tests”:

### 5.1 Past-only quantile correctness

Your strategy relies heavily on:

* Disp_th quantiles
* squeeze quantiles
* slot medians for RVOL_H

**Spec requirement:**
Quantiles and medians must use **past-only** history.
In code, this means at time t you compute thresholds from data strictly < t.

### 5.2 VWAP anchoring correctness

* AVWAP must begin at anchor_time (next RTH open)
* WVWAP resets Monday 09:30 ET
* Ensure WVWAP does not include Sunday futures prints (ETFs don’t trade)

### 5.3 Campaign lifecycle correctness

Validate state transitions with unit tests / logs:

* INACTIVE → COMPRESSION (freeze bounds)
* COMPRESSION → BREAKOUT (structural + displacement)
* DIRTY triggers correctly and blocks same direction
* box_version increments only on DIRTY reset via box_shifted
* reentry counters reset per box_version only

### 5.4 Order TTL and pending correctness

* limit order TTL cancels correctly
* pending expires correctly and rechecks hourly
* pending uses **placement-time values**, but logs signal-time snapshot

---

# 6) Telemetry specification for filter evaluation (the core of your request)

To learn which conditions are effective vs gating profitable trades, you need **signal-level** and **blocked-signal** logging.

### 6.1 “Signal events” vs “Trade events”

Create an event record every time a candidate is evaluated:

**SignalEvent** (hourly and daily):

* timestamp, symbol, direction
* campaign_state, box_version, breakout_active, continuation_mode
* structural_breakout (daily), displacement_pass, Disp, Disp_th
* squeeze_metric, sq_good/sq_loose
* RVOL_D, vol_score, low-vol exception triggered
* RVOL_H, slot_id
* disp_mult, quality_mult, expiry_mult, score_total, score_threshold
* selected entry type (A/B/C/add) if any
* allowed/blocked decision + **blocked_reason**

**TradeEvent**

* entry fill, exit fill, partials, stop/TP hits, gap stops
* entry_type/subtype, add number, campaign_id
* realized R, MAE/MFE, time in trade, runner contribution

### 6.2 Blocked-signal “virtual trade” simulation (must-have)

To measure if a filter is gating profitable trades, you need a virtual outcome for candidates that were blocked.

When a candidate is blocked (by a filter or risk constraint), record a **VirtualTrade**:

* Assume the entry would have filled using the same execution model as the intended entry type (limit TTL or market)
* Simulate forward until:

  * stop hit
  * TP1/TP2 hit
  * stale exit
  * hard expiry
* Record:

  * virtual_R
  * virtual_MAE_R / MFE_R
  * time-to-TP1
  * whether it became a trend runner

This is the single most powerful diagnostic you can build.

### 6.3 Block reason taxonomy (granular)

When blocked, log exactly one primary reason and optionally secondary.

Example enumeration:

* `HardBlockDoubleCounter`
* `NoDisplacement`
* `BreakoutQualityReject`
* `ScoreBelowThreshold`
* `ChopHalt`
* `DegradedRequiresHigherScore`
* `EntryB_RVOLH_LowAndDispMultLow`
* `Adds_RVOLH_Low_NoAcceptance`
* `FrictionGate`
* `HeatCap`
* `MaxPositions`
* `SameDirectionCap`
* `PendingExpired`
* `TTLExpired`
* `DIRTY_SameDirectionBlocked`
* `MicroGuard`

This lets you build “filter effectiveness” tables.

---

# 7) Experimental design: optimization + ablation (what to run)

You want **two types** of experiments:

## 7.1 Optimization (tuning)

Tune continuous parameters to maximize risk-adjusted returns **without starving frequency**.

### 7.1.1 Primary optimization targets (most important)

1. `q_disp` (0.65–0.80, step 0.05)
2. `SQUEEZE_CEIL` (0.90–1.20, step 0.05)
3. Score thresholds:

   * normal score_th ∈ {1,2,3}
   * degraded score_th ∈ {2,3,4}
4. Stop buffer multipliers:

   * QQQ/GLD ATR_STOP_MULT ∈ [0.8, 1.2]
   * USO/IBIT ∈ [1.0, 1.6]
5. Expiry:

   * base_expiry days ∈ {4,5,6}
   * decay_step ∈ {0.08,0.12,0.16}
   * hard_extension ∈ {4,5,6}
6. Entry B gating:

   * RVOL_H min ∈ {0.7,0.8,0.9}
   * disp_mult override ∈ {0.88,0.90,0.92}
7. Adds:

   * add risk fraction ∈ {0.35, 0.5, 0.65}
   * low RVOL_H add risk multiplier ∈ {0.6,0.7,0.8}
   * CLV acceptance threshold ∈ {0.25,0.30,0.35}

### 7.1.2 Secondary optimization targets

* `DIRTY box_shift threshold` (0.30–0.45 ATR)
* `M_BREAK` (2 vs 3 across all, or per symbol)
* continuation trigger: R_proxy threshold and time component
* pending max hours ∈ {6,12,18}
* max portfolio heat (2.0%–3.0%)

### 7.1.3 Optimization objective function

Use a multi-objective score:

* maximize: expectancy (R) after costs, CAGR
* minimize: max drawdown, tail loss events (gap-stops)
* constraint: trade count must exceed a floor (e.g., ≥ 60 trades total OOS, or ≥ X per year)

Example composite:

* `objective = CAGR - 0.5*MaxDD - 0.1*GapStopPenalty`
  with a hard constraint on frequency.

## 7.2 Ablation (filter effectiveness)

This addresses your “which filters gate profitable trades” question.

### 7.2.1 Controlled ablation protocol

Pick a baseline tuned config, then run variants:

* Baseline = full strategy
* Remove/disable one filter at a time:

  1. disable Score threshold (keep displacement)
  2. disable Chop mode
  3. disable DIRTY
  4. disable Breakout Quality Reject
  5. disable Entry B RVOL_H gating
  6. disable add RVOL_H acceptance gating
  7. disable micro guard
  8. disable friction gate
  9. disable correlation heat penalty
  10. disable pending (immediate drop)
* Also run “tighten” experiments:

  * stricter score threshold
  * stricter squeeze ceiling
  * higher q_disp

### 7.2.2 What to measure per ablation

* Δ expectancy (R), PF, MaxDD
* Δ trade count and fill rate by entry type
* Δ runner contribution
* Δ gap-stop incidence
* **virtual trade uplift**: blocked trades that would have been profitable

This will tell you exactly which filters are “too expensive”.

---

# 8) Walk-forward and leakage control (critical)

### 8.1 Use walk-forward, not single split

Recommended:

* Train window: 2–3 years
* Test window: 3–6 months
* Roll forward by 3 months
* Repeat until end

### 8.2 Purging / embargo

Because your signals use rolling lookbacks (ATR50, quantiles), apply:

* **purge** overlapping periods
* **embargo** a few days after split boundaries if needed

### 8.3 Where tuning happens

* Do parameter search **only** in training windows
* Fix the chosen parameter set and report results on test windows

---

# 9) Backtrader implementation details that matter

### 9.1 Multiple symbols and correlation

Backtrader can do portfolio simulation, but correlation constraints require you to access:

* current open positions
* recent return series per symbol
* compute correlation on 4H or daily returns

Implement correlation in your RiskManager based on locally stored returns arrays (not indicator lines only).

### 9.2 Bracket orders

For ETF testing:

* Use bracket structure: entry + stop + TP1/TP2
* Runner managed by trailing logic after TP1

### 9.3 Partial fills

Backtrader doesn’t simulate partial fills well by default. Two options:

* Ignore partials (acceptable for liquid ETFs)
* Or implement a conservative partial fill model (optional)

Given liquidity of QQQ/GLD and decent for USO/IBIT, you can start with “full fills” but keep a sensitivity test that worsens slippage to approximate partials.

---

# 10) Output reports (what you should produce)

### 10.1 Standard performance

* equity curve
* CAGR, MaxDD, Sharpe-like metrics
* expectancy in R, PF, win rate
* trade count per month, average hold days

### 10.2 Breakdown reports (must-have)

By:

* symbol
* direction
* campaign state at entry (breakout vs continuation)
* entry type: A / B / C_standard / C_continuation / ADD
* regime class: bull/bear/range (4H) and double-counter block frequency
* RVOL_D bins and RVOL_H bins

### 10.3 Filter attribution & gating analysis

* Table of blocked reasons:

  * count blocked
  * avg virtual_R of blocked
  * % blocked that would have reached TP1/TP2
* “Cost of filter” charts:

  * remove filter → Δ expectancy vs Δ DD vs Δ frequency
* Pending effectiveness:

  * how often pending converts to entry
  * whether pending entries perform differently

### 10.4 Sensitivity surfaces

For key pairs:

* (q_disp, score_th)
* (SQUEEZE_CEIL, stop_mult)
* (entryB_rvol_min, disp_override)
  Plot heatmaps of expectancy and trade count.

---

# 11) Optimization approach (practical)

### 11.1 Don’t brute force everything

The space is big. Use staged search:

**Stage 1: coarse grid**

* q_disp, SQUEEZE_CEIL, score_th, stop_mult coarse steps

**Stage 2: refine around winners**

* narrower ranges
* include expiry decay and add logic tuning

**Stage 3: robustness filter**

* reject configs that fail in any major year or symbol
* reject configs with unstable performance (high variance between folds)

### 11.2 Use random search + constraints

Random search often finds good configs faster than full grids:

* sample parameter vectors
* enforce min-trade constraints
* keep top N per fold

---

# 12) IB + Backtrader integration plan

### 12.1 Data acquisition with IB + `ib_async`

* Write a “data downloader” that:

  * pulls historical bars for each symbol (1h and 1d)
  * stores to local DB
  * updates incrementally

### 12.2 Backtest runner

* Loads from DB into backtrader feeds
* Runs strategy
* Stores:

  * trade logs
  * signal logs
  * blocked logs
  * virtual trade logs

### 12.3 Repeatability

Every run writes:

* Git commit hash
* parameter set
* cost model settings
* date range
* random seed (if random search)
* walk-forward fold definitions

---

# 13) Minimum “must implement” features for your goals

If your goal is filter effectiveness and gating analysis, do not skip:

1. **SignalEvent logging for every evaluation**
2. **Blocked reason taxonomy**
3. **VirtualTrade simulation for blocked signals**
4. Walk-forward OOS with past-only quantiles

Without these, you’ll get performance numbers but you won’t be able to answer “which filters are gating profitable trades”.

---

## Suggested first experiments (fastest path to insight)

1. Implement correct costs + gap-stop rule + full telemetry + virtual trades.
2. Run a baseline tuned-ish configuration on 2019–2021 train, 2022 test (or similar).
3. Do ablation of the top 6 filters:

   * score threshold
   * chop mode
   * DIRTY
   * entry B RVOL_H gating
   * adds RVOL_H acceptance
   * friction gate
4. Only then do broader parameter optimization.

---
