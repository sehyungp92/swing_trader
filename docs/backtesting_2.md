Below is a **detailed, implementation-oriented backtest/optimization spec** for ATRSS v4.5 using **IBKR historical data + Backtrader**, with a strong focus on (1) **parameter optimization**, and (2) **measuring which filters/conditions add edge vs gate profitable trades**.

---

# 1) Goals and principles

## 1.1 What you want to learn

1. **Does the strategy have a stable edge** across MNQ/MCL/MGC/MBT?
2. Which rules are **core alpha drivers** vs **unnecessary gating**?
3. Sensitivity: how fragile are results to small changes?
4. Can you improve returns **without reducing trade frequency**?

## 1.2 Non-negotiables for scientific testing

* Separate **in-sample optimization** from **out-of-sample validation**
* Use **walk-forward** or time-split evaluation
* Track **why trades were not taken** (gating analysis)
* Model execution realistically:

  * stop-limit mechanics + post-fill slippage abort
  * commissions + realistic slippage
  * session-specific liquidity differences
* Evaluate performance with both:

  * **return metrics** and **trade-frequency constraints**
  * **risk metrics** (DD, tail risk, heat utilization)

---

# 2) Data specification (IBKR + preprocessing)

## 2.1 Data sources

* Use **IBKR historical bars** via `ib_async` for:

  * Hourly OHLCV for each instrument
  * Daily OHLCV for each instrument
* Caveat: IBKR historical data requests are slow and rate-limited. For extensive optimization runs, cache aggressively.

## 2.2 Bar requirements

* **Hourly bars**: enough history to cover multiple regimes:

  * minimum: 3–5 years per instrument
  * ideal: 8–12 years for MNQ/MCL/MGC; MBT may have less
* **Daily bars** for the same period to compute daily regime/bias/score.

## 2.3 Preprocessing rules

* Normalize to a consistent timezone (UTC recommended).
* Ensure contiguous hourly bars (IBKR gaps exist). Handle:

  * missing bars: forward-fill *timestamps only*, not prices
  * when missing data is substantial, mark as “data invalid block” and forbid new entries
* Roll contracts:

  * micro futures continuous series is non-trivial.
  * For rigorous testing, create a **continuous back-adjusted series** or a **panama-canal** series.
  * If you cannot roll properly, use a single active contract window and restrict period accordingly.

## 2.4 Caching design

* Persist raw and processed data by symbol/timeframe in Parquet:

  * `data/raw/IBKR/{symbol}_{tf}.parquet`
  * `data/processed/{symbol}_{tf}.parquet`
* Cache indicator columns too for speed in repeated runs:

  * daily: ema_fast, ema_slow, adx, di, atr, score, regime, bias
  * hourly: ema_mom, ema_pull variants, atrh, donchian levels

---

# 3) Backtrader architecture

## 3.1 Multi-timeframe wiring

You’ll run Backtrader with **hourly as the main data** plus a **daily resample**.

Options:

1. Load hourly data and `cerebro.resampledata(hourly, timeframe=Days, compression=1)` for daily
2. Or load daily as a second feed and synchronize timestamps

Recommendation: **resample hourly → daily inside Backtrader** so your daily state is aligned.

## 3.2 Strategy structure

Implement ATRSS v4.5 as a Backtrader `Strategy` with:

* Per-symbol state objects:

  * `confirmed_bias`, `raw_bias`, `regime`, `score`, `adx_slope`
  * cooldown timers, reset flags, voucher status
  * position legs status (base/addonA/addonB), net qty, protective stop reference
* Order tracking:

  * entry orders (stop-limit)
  * protective stops
  * addonB pending entry order
  * cancellation logic on exit
* Execution rules:

  * stop-limit bracket style: “entry stop-limit” + “protective stop order”
  * slippage abort logic after fill

Backtrader has limited native stop-limit behavior simulation across gaps; you’ll implement custom order fill logic via a custom broker/slippage model or by post-processing fill events.

---

# 4) Execution modeling spec (critical)

## 4.1 Commission model

* Use realistic IBKR commissions per contract (and exchange fees if known).
* Treat MBT/MCL/MGC/MNQ per-contract commission.

## 4.2 Slippage model (baseline)

You need a repeatable slippage model for optimization:

* Base: 1–2 ticks in normal conditions
* Increase slippage during:

  * low liquidity hours
  * high ATR expansion
* Define:

  * `slip_ticks_normal`
  * `slip_ticks_illiquid`
  * `illiquid_hours` per instrument (e.g., MNQ outside RTH; MBT always varies)

## 4.3 Stop-limit fill simulation

For each bar:

* For LONG stop-limit with `stop=S`, `limit=L>S`:

  * if `high < S`: no trigger
  * if `high >= S` and `low <= L`: fill at `max(S, open)` or a modeled price within [S, L]
  * if triggered but bar trades above limit without trading back: no fill (order remains)
    Same for SHORT.

This is more realistic than treating stop-limit as market.

## 4.4 Post-fill “slippage tolerance abort”

After fill:

* Compute `fill_slip = abs(fill - intended_stop)`
* Abort if `fill_slip > max_slip = min(0.15*R_base, 0.50*ATRh)`
  Abort action in backtest:
* immediate flatten at next tradable price (use open of next bar + slippage)

This is important to stop “gap-to-awful-fill” distortions.

## 4.5 Halts / limit-up/limit-down approximation

IBKR bar data won’t explicitly label halts well. Approximate:

* If price is unchanged for N consecutive bars while volume collapses OR IB returns “halted” status (if you have it)
* In backtest:

  * if a halt condition is detected: forbid new entries
  * if protective stop should have triggered but cannot fill due to halt:

    * exit at the first bar after halt ends (worst-case slippage model)

This will be imperfect, but better than ignoring it.

---

# 5) Parameter optimization plan

## 5.1 What to optimize (key variables)

Split into **core** and **execution** parameters.

### Core parameters

1. **Bias confirmation**

* `fast_confirm_score`: [55, 60, 65, 70]
* `confirm_days_when_not_fast`: [1, 2, 3]
  (You may test whether 1 day is enough when score < threshold)

2. **Regime thresholds**

* `ADX_ON`: [18, 20, 22, 25]
* `ADX_OFF`: [ADX_ON-2, ADX_ON-4] (constraint)
* `ADX_STRONG`: [28, 30, 35]

3. **Hourly EMA mapping**

* `EMA_pull_strong`: [21, 34, 40]
* `EMA_pull_trend`: [34, 50, 55]
* `EMA_mom`: [15, 20, 30]

4. **Breakout definition**

* `donchian_len_hours`: [12, 20, 30, 40]
* `dist_cap_base`: [1.8, 2.0, 2.2]
* `dist_cap_strong`: [2.2, 2.4, 2.6]
* `dist_cap_hard`: [2.6, 2.8, 3.0]
* `adx_slope_gate`: [0, -1, +1] (threshold for “strengthening”)

5. **Stops / trailing**

* `daily_mult`: per instrument ranges:

  * MNQ: [1.8–2.6]
  * MCL: [2.0–3.0]
  * MGC: [1.6–2.4]
  * MBT: [2.5–3.5]
* `hourly_mult`: [2.5–4.5]
* `chand_mult`: [2.5–4.0]
* `regime_collapse_mult`: [1.0–2.0]
* `breakeven_trigger_R`: [1.0, 1.5, 2.0]
* `breakeven_cushion_atr`: [0.05, 0.1, 0.15]

6. **Churn controls**

* cooldown hours by regime:

  * STRONG: [2, 4, 6]
  * TREND: [8, 12, 16]
  * RANGE: [16, 24, 36]
* Reset logic variation:

  * baseline: close crosses EMA_pull
  * alternative: require “touch+close beyond” in opposite direction (stricter)

7. **Voucher**

* `voucher_valid_hours`: [12, 24, 36]
* `voucher_requires_reset`: fixed True (per v4.5), but you can ablate it

8. **Stop-and-reverse**

* `reverse_min_score`: [55, 60, 65, 70]
* `reverse_requires_pullback_or_breakout`: True vs False (immediate eligibility)

### Execution parameters

* stop-limit offset multipliers:

  * `limit_offset_ticks_min`: [2, 4, 6]
  * `limit_offset_atr_frac`: [0.10, 0.15, 0.20, 0.25]
* max slippage:

  * `max_slip_R_frac`: [0.10, 0.15, 0.20]
  * `max_slip_ATRh_frac`: [0.35, 0.50, 0.65]
* expiry hours:

  * `entry_expiry_hours`: [3, 6, 12]

## 5.2 Optimization method

Do **two-stage optimization**:

### Stage A: coarse search (broad coverage)

* Use random search or Latin Hypercube sampling (500–2000 trials)
* Objective is a **composite score** (see below)
* Identify top ~50–100 parameter sets

### Stage B: local refinement (focused)

* Use Bayesian optimization (Optuna/TPE) around top regions
* 200–500 more trials

Backtrader can be slow; parallelize across CPU cores and cache indicators.

## 5.3 Objective function (must include frequency constraints)

You want “maximize returns without killing frequency.” Use a multi-objective or weighted objective.

Example **single scalar** objective:

```
Objective =
  + 0.45 * CAGR
  + 0.25 * (Sharpe or Sortino)
  + 0.20 * (ProfitFactor normalized)
  - 0.30 * MaxDrawdown
  - 0.10 * TailLossMetric (e.g., avg worst 5 trades)
  - Penalty if trades/month < target
```

Frequency penalty:

* set a minimum trades/month per instrument or portfolio:

  * e.g., `>= 2 trades/month portfolio` or `>= 0.5 trades/month/instrument`
* penalty grows steeply below target (so optimizers don’t “cheat” by trading rarely)

Also enforce:

* minimum sample size (e.g., at least 200 total trades across history for parameter set validity)

---

# 6) Ablation testing (which filters help vs gate profits)

This is the most important part for your question.

## 6.1 Design: build a “feature flag” strategy

Implement each major condition as a toggle. For each run you log:

* signals generated
* candidate filtered out (and why)
* orders placed
* fills, slippage aborts
* exits and reasons

## 6.2 Major components to ablate

Run the strategy under these variants:

### Daily layer

A1) No fast-confirm (always 2-day confirm)
A2) Fast-confirm enabled (baseline)
A3) Always 1-day confirm (test if 2-day is overkill)

### Regime gating

B1) ADX regime ON/OFF active (baseline)
B2) regime disabled (always trade bias)
B3) hysteresis disabled (single threshold)

### Entry logic

C1) Pullback only (no breakouts)
C2) Breakout only
C3) Pullback + breakout (baseline)

### Momentum filter

D1) EMA_mom filter on (baseline)
D2) EMA_mom filter off (to see if it gates winners)

### Micro confirmation

E1) require close > prior_high / < prior_low (baseline)
E2) remove micro confirmation

### Churn controls

F1) cooldown + reset (baseline)
F2) cooldown only
F3) reset only
F4) none (measure churn + expectancy collapse)

### Voucher

G1) voucher enabled (baseline)
G2) voucher disabled

### Stop-and-reverse

H1) enabled (baseline)
H2) disabled (measure impact)

### Pyramiding

I1) no add-ons
I2) add-on A only
I3) add-on B only
I4) both (baseline)

### Execution protections

J1) stop-limit + slippage abort (baseline)
J2) stop-market (optimistic)
J3) stop-limit without abort
(this tells you how much edge depends on realistic execution)

### Breakout overextension guard

K1) adaptive guard (baseline)
K2) weaker guard
K3) no guard
(see if guard is filtering the best legs)

## 6.3 How to quantify “gating profitable trades”

You need two additional logs:

### 1) “Would-have-been” shadow candidates

When a candidate is rejected by a filter, record:

* symbol/time
* candidate direction
* intended entry price (stop)
* hypothetical initial stop (same as if accepted)
  Then simulate a **shadow trade** (not executed) to see what R-multiple it *would* have reached under the same exit rules.

This directly answers: “this filter blocked a trade that would have made +3R.”

### 2) Filter attribution table

For each filter, compute:

* count rejected
* average shadow R of rejected trades
* % rejected that would have been > +1R, > +2R
* net “missed expectancy” vs “avoided losses”

This tells you which filter is too strict.

---

# 7) Walk-forward validation spec (avoid overfitting)

## 7.1 Anchored walk-forward

Example:

* Train/optimize: 2017–2019
* Test: 2020
* Train/optimize: 2017–2020
* Test: 2021
* …
  This keeps training expanding and produces realistic deployment behavior.

## 7.2 Purged split around boundaries

Because indicators (ATR/EMA) leak state, apply a small “purge” window around split boundaries:

* e.g., remove 30 trading days around boundary in evaluation or reset indicator warmup.

## 7.3 Robustness constraints

A parameter set is “acceptable” only if:

* positive expectancy in most test windows
* max DD within acceptable band
* trade frequency not collapsing out-of-sample
* not dependent on a single instrument

---

# 8) Reporting (what you output per run)

## 8.1 Standard performance report

* CAGR, volatility, Sharpe/Sortino
* Max drawdown, Calmar
* Profit factor, win rate, avg win/loss
* Expectancy per trade in $ and R
* Trades/month (portfolio + per instrument)
* Heat utilization stats (avg heat, peak heat, time at cap)

## 8.2 Strategy behavior report

* Entry type breakdown: pullback vs breakout vs reverse
* Exit reason breakdown: stop, chandelier, regime collapse, bias flip, time decay, slippage abort
* Average hold time per trade and per winner/loser
* MAE/MFE distributions

## 8.3 Filter effectiveness report (the key deliverable)

For each filter/condition:

* how many candidates it rejected
* avg shadow R of rejected
* % rejected that would have become > +1R / +2R
* avoided-loss estimate (how many rejected would have hit stop)
* net contribution estimate

This is how you decide which rules to relax.

---

# 9) Practical implementation plan (step-by-step)

## Phase 1: Build deterministic backtest engine (no optimization yet)

1. Data downloader via `ib_async`, caching to parquet
2. Backtrader feeds: hourly + resampled daily
3. Implement ATRSS v4.5 baseline with full logging + order lifecycle correctness
4. Validate on short period manually (spot-check signal generation)

## Phase 2: Add execution realism

1. Stop-limit fill logic + expiry
2. Slippage model
3. Slippage abort behavior
4. Commissions

## Phase 3: Add ablation framework

1. Feature flags per filter
2. Candidate rejection logging + reasons
3. Shadow trades for rejected candidates

## Phase 4: Optimization framework

1. Parameter interface + ranges
2. Parallel run harness
3. Objective function including frequency constraints
4. Walk-forward evaluation pipeline

## Phase 5: Synthesis

1. Identify filters that reduce net expectancy
2. Propose a “v4.6” streamlined rule set
3. Re-test with walk-forward to confirm improved generalization

---

# 10) Tooling recommendations

* Use Backtrader for core simulation, but wrap it with:

  * **Optuna** for optimization
  * **Pandas** for results aggregation
  * **Parquet** for caching
* Keep the strategy deterministic:

  * fixed random seeds
  * exact same slippage settings per run

---

# Appendix

## Variables to Optimize

**Regime Detection**
- ADX_ON (20), ADX_OFF (18), ADX_STRONG (30) — the thresholds and hysteresis gap
- ADX_slope_3 floor (−2) for STRONG_TREND maintenance
- EMA_fast (20) and EMA_slow (55) periods

**Conviction Score**
- Component weights (30/30/40 split across ADX, EMA_sep, DI_diff)
- Confirmation threshold (60) for fast-confirm and reversal eligibility
- The "2 consecutive closes" fallback — is 2 the right number?

**Hourly Indicators**
- EMA_mom period (20)
- ATRh period (48)
- EMA_pull periods (34 for STRONG_TREND, 50 for TREND)
- Donchian channel length (20h)

**Breakout Overextension Guard**
- Base cap (2.0), relaxed cap (2.4), hard block (2.8)

**Churn Control**
- Cooldown durations (24h / 12h / 4h by regime)
- Voucher validity window (24h)

**Stops & Targets**
- Per-instrument multipliers (daily_mult, hourly_mult, chand_mult)
- BE trigger (1.5R) and offset (0.1 × ATR)
- Chandelier activation (2R) and lookback (20d)
- Regime collapse tightening factor (1.5 × ATRh)

**Pyramiding**
- Add-on A trigger (1.5R), sizing (0.5 × base)
- Add-on B trigger (2R), sizing rules
- Max legs (3)

**Portfolio**
- Per-trade risk (1.0% / 0.75%)
- Portfolio heat cap (6%), single-asset cap (3.5%)

**Execution**
- Limit offset formula (0.15 vs 0.20 × ATRh)
- Max slippage (0.15 × R_base, 0.50 × ATRh)
- Order expiry (6h)

**Time Decay**
- Hold limit (480h) and minimum profit threshold (1.0R)

---

## Filters to Ablate

Test performance **with vs. without** each of these:

| # | Filter / Condition | Rationale for Ablation |
|---|---|---|
| 1 | **Momentum-state filter** (close vs EMA_mom) | May be redundant given EMA/DI bias already confirmed on daily |
| 2 | **Overextension guard** (dist caps) | Could be filtering the highest-expectancy breakout trades |
| 3 | **Conviction score gating** entirely | Does the composite score add value over regime ON alone? |
| 4 | **Fast-confirm path** (score ≥ 60 = same-day) | Does skipping the 2-close wait actually improve timing? |
| 5 | **Reset requirement** (price must revisit EMA_pull) | Is this too restrictive — does it cause missed re-entries? |
| 6 | **Voucher system** | Does it add enough re-entries to justify the complexity? |
| 7 | **Cooldown** (time-based, separate from reset) | Is the reset alone sufficient churn control? |
| 8 | **Post-fill slippage abort** | How often does it fire, and do aborted trades end up being winners? |
| 9 | **"Close > prior_high" on pullback entries** | Does this confirmation bar filter add edge or just delay? |
| 10 | **Regime collapse stop tightening** | Does forcing tighter stops on ADX drop improve or hurt (premature exits)? |
| 11 | **Time decay exit** (480h rule) | Are time-stopped trades actually underperformers, or do some recover? |
| 12 | **Add-on B** (third leg) | Does the complexity and added risk of a third leg pay for itself? |
| 13 | **Breakout entries entirely** (pullback-only variant) | Are breakouts net-positive after the overextension filter? |
| 14 | **Hysteresis gap** (ADX_ON vs ADX_OFF) | Does the 2-point buffer matter vs. a single threshold? |

**Suggested ablation priority:** Start with 1, 2, 3, and 9 — these are the filters most likely to be either redundant or value-destroying, and they're easy to toggle independently.