# ETF Trend-Regime Swing System (ETRS vFinal)

## 0) Objective

Capture dominant **medium-term trends** (days → weeks) in both directions using:

* **Daily timeframe** to define trend/regime and reduce chop
* **Hourly timeframe (RTH bars)** for high-quality entries
* Strict churn control and risk normalization across assets

Universe is now **ETFs**:

* **QQQ** (equity beta)
* **USO** (oil proxy)
* **GLD** (gold proxy)
* **IBIT** (bitcoin proxy)

This is a **long/short swing trend-following system** with:

* pullback entries in-trend
* breakout participation via breakout→pullback (not extension chasing)
* pyramiding winners
* portfolio heat controls

---

## 1) Instruments, Session, Data

### 1.1 Trading session

**Default:** **RTH only** (recommended for robust liquidity and clean fills)

* US equities session: **09:30–16:00 ET**
* No entries outside RTH
* Position management (exits, stop updates) also RTH-only **except** emergency open-gap handling (see below)

**Rationale:** ETFs have materially worse liquidity and noisier prints outside RTH.

> Optional later extension: allow extended-hours trading for IBIT only, but keep off initially.

### 1.2 Bar construction

* Daily bars: regular daily close (RTH close)
* Hourly bars: **RTH-only hourly bars**

  * e.g., 9:30–10:30, …, 15:30–16:00 (last bar is 30m; either merge or treat separately)
* Ensure indicators are computed consistently on the same bar set (RTH-only).

### 1.3 Corporate actions

ETFs rarely have splits; still:

* Use adjusted data for backtests
* In live, rely on raw prices but handle IB corporate action notices if needed (rare).

---

## 2) Daily Regime, Bias, and Confirmation

### 2.1 Daily indicators

Per symbol compute:

* `EMA_fast` (default 20)
* `EMA_slow` (default 55)
* `ATR20`
* `ADX14`, `+DI`, `-DI`, `DI_diff = |+DI - -DI|`
* `EMA_sep_pct = |EMA_fast - EMA_slow| / close * 100`
* `EMA_fast_slope_5 = EMA_fast - EMA_fast.shift(5)` (or pct slope)

### 2.2 Regime ON/OFF with hysteresis (per symbol)

Define:

* `regime_on` turns ON when `ADX ≥ ADX_ON`
* remains ON until `ADX < ADX_OFF`
* `ADX_OFF = ADX_ON - 2`

Starting defaults (optimize later):

* QQQ: `ADX_ON=18`, `ADX_OFF=16`
* GLD: `ADX_ON=20`, `ADX_OFF=18`
* IBIT: `ADX_ON=18`, `ADX_OFF=16`
* USO: `ADX_ON=20`, `ADX_OFF=18`

### 2.3 STRONG_TREND classification

When `regime_on`:

* STRONG_TREND if `ADX ≥ ADX_STRONG` (default 30)
  Optionally require `ADX_slope_3 > -2` to avoid late-stage regime fade.

### 2.4 Raw bias (direction)

Daily `raw_bias`:

* **LONG** if:

  * `EMA_fast > EMA_slow`
  * `close > EMA_fast`
  * `+DI > -DI`
* **SHORT** if:

  * `EMA_fast < EMA_slow`
  * `close < EMA_fast`
  * `-DI > +DI`
* else **FLAT**

### 2.5 Conviction score (0–100)

A monotonic composite score:

* ADX strength component
* EMA separation component
* DI_diff component

(Keep your existing scoring formula; ensure scores rise with trend quality.)

### 2.6 Bias confirmation (frequency-optimized)

Confirmed bias requires `regime_on` and **one of**:

**Path A (standard):** `hold_count(raw_bias) ≥ 2`

**Path B (fast-confirm):**

* `hold_count ≥ 1`
* `score ≥ 55 AND ADX ≥ 22`

**Path C (1-day + structure confirm, optional):**

* `hold_count ≥ 1`
* `DI_diff ≥ DI_MIN` and `EMA_sep_pct ≥ SEP_MIN` and `ADX ≥ ADX_MIN_STRUCT`

Suggested starting values:

* `DI_MIN=10`, `SEP_MIN=0.20`, `ADX_MIN_STRUCT=20`

---

## 3) Hourly Indicators (RTH)

Compute on RTH-hourly bars:

* `EMA_mom = EMA(20)` (momentum state)
* `EMA_pull` (regime-adaptive):

  * STRONG_TREND → EMA(34)
  * TREND → EMA(50)
* `ATR_hourly = ATR(48)` on hourly bars (RTH-only)

### Momentum-state filter (replaces RSI)

* Long allowed only if `close > EMA_mom`
* Short allowed only if `close < EMA_mom`

---

## 4) Short Safety Filter

To reduce “shorting into bull acceleration” losses:

Allow SHORT only if daily fast EMA slope is not strongly rising:

Default rule:

* `EMA_fast_slope_5 ≤ 0` for shorts

(Optimize threshold later; can allow small positive slope.)

Applies to:

* pullback entries
* breakout-pullback entries
* stop-and-reverse shorts

---

## 5) Entry Types

Candidates are generated only when:

* no open position in that symbol
* symbol not halted / LULD paused
* cooldown/reset satisfied OR voucher/stop-and-reverse rules allow bypass (as specified)

Entry types:
A) **Pullback to EMA_pull**
B) **Impulse Breakout → Breakout-Pullback** (STRONG_TREND participation)
C) **Stop-and-reverse** on bias flip (score≥60)

---

## 6) Entry A — Pullback to EMA_pull (primary)

### Long setup

Requires:

* daily confirmed bias LONG
* `regime_on`
* hourly conditions:

  1. touch: `low ≤ EMA_pull`
  2. reclaim: `close > EMA_pull`
  3. momentum-state: `close > EMA_mom`
  4. micro confirmation: `close > prior_high`

### Short setup (mirror)

* touch: `high ≥ EMA_pull`
* reclaim: `close < EMA_pull`
* momentum: `close < EMA_mom`
* micro: `close < prior_low`
* plus short safety filter

### Execution mechanics (ETF-appropriate)

Submit **STOP-LIMIT** for next-bar continuation:

* Long trigger: `signal_high + tick`
* Long limit: `trigger + limit_band`
* Short trigger: `signal_low - tick`
* Short limit: `trigger - limit_band`

Where:

* `tick` = $0.01 (most ETFs)
* `limit_band` = max(`limit_ticks * tick`, `limit_pct * price`)

  * start: `limit_pct = 0.10%` for QQQ/GLD, `0.15%` for USO/IBIT
  * optimize later

**Order expiry:** 6 hours (RTH hours only)
If RTH ends before expiry, cancel at close.

**Entry slippage tolerance:**
If fill price deviates beyond:

* `max_entry_slip_pct` (e.g., 0.15% QQQ/GLD, 0.25% USO/IBIT)
  OR
* `max_entry_slip_atr * ATR_hourly` (e.g., 0.25× ATR_hourly)
  → **panic-flatten immediately** (market/marketable) and tag “bad fill”.

---

## 7) Entry B — Impulse Breakout → Breakout-Pullback (redesigned)

### 7.1 Donchian lookback (RTH-aligned)

Because only ~6.5 RTH hours/day, hourly Donchian should reflect multi-day structure.

Use:

* `DonchianHigh(L)` / `DonchianLow(L)` on hourly RTH bars
* start: `L = 26` (≈ ~4 RTH days)
* optimize 13–52

### 7.2 Impulse breakout event (arming)

Only in **STRONG_TREND** + confirmed bias.

Long arm event:

* `high > DonchianHigh(L)` AND `close > EMA_mom`

Short arm event:

* `low < DonchianLow(L)` AND `close < EMA_mom`
* plus short safety filter

On arm:

* set `breakout_armed_dir`
* set `breakout_armed_until = now + ARM_WINDOW_HOURS`

  * start: 12 hours (RTH hours), optimize 8–24

### 7.3 Breakout-pullback entry trigger

While armed, STRONG_TREND persists and daily bias matches:

Enter on pullback to **EMA_mom** (faster “value” after impulse):

Long:

* `low ≤ EMA_mom` and `close > EMA_mom`
* `close > prior_high`

Short:

* `high ≥ EMA_mom` and `close < EMA_mom`
* `close < prior_low`
* plus short safety filter

Execution uses the same STOP-LIMIT + expiry + slippage abort rules as Entry A.

---

## 8) Churn Controls: Cooldown + Reset + Voucher

### 8.1 Adaptive cooldown (post-exit)

After exiting a position (including stop or exit signal):

* STRONG_TREND exit regime → 4 RTH hours cooldown
* TREND → 12 RTH hours
* RANGE/regime_off → 24 RTH hours

Cooldown counts only during RTH hours.

### 8.2 Reset requirement (structural anti-churn)

Same-direction re-entry requires a “reset”:

* Long reset: hourly close **below** current `EMA_pull`
* Short reset: hourly close **above** current `EMA_pull`

**Documented intent:** reset uses **current EMA_pull**, so if regime downgrades (EMA34→EMA50), reset becomes harder.

### 8.3 Re-entry voucher

If stopped out after **MFE ≥ +1R**, grant voucher valid for 24 hours:

* voucher bypasses cooldown time
* **reset is still required** (simple consistent rule)

---

## 9) Stop-and-Reverse (kept)

If in position and daily confirmed bias flips opposite:

* Exit existing position at next tradable opportunity
* Generate reverse candidate immediately **if**:

  * new direction score ≥ 60
  * regime_on
  * momentum-state aligns (hourly close vs EMA_mom)
  * short safety filter for shorts

Reverse competes in portfolio allocation with other candidates (no forced priority).

---

## 10) Stops & Position Management

### 10.1 Initial stop: structure-aware + ATR hybrid

At entry:

Compute ATR-based stop distance:

* `Stop_ATR = max(daily_mult * ATR20, hourly_mult * ATR_hourly)`

Compute structure stop:

* Long: `Stop_STRUCT = signal_low - tick`
* Short: `Stop_STRUCT = signal_high + tick`

Use the wider stop distance.

Starting multipliers (optimize):

* QQQ: daily 2.1, hourly 2.7, chandelier 3.0
* GLD: daily 2.0, hourly 2.6, chandelier 3.0
* IBIT: daily 2.5, hourly 3.0, chandelier 3.2
* USO: daily 2.3, hourly 3.0, chandelier 3.2

### 10.2 Break-even at 1.5R

When MFE ≥ 1.5R:

* move stop to `entry ± 0.1*ATR20` cushion

### 10.3 Chandelier trail at 2R

When MFE ≥ 2R:

* Long: `HH(20d) - chand_mult*ATR20`
* Short: `LL(20d) + chand_mult*ATR20`
  Ratchet only favorable direction.

### 10.4 Profit floor (prevents huge MFE turning negative)

Enforce minimum locked profit:

* if MFE ≥ 2R: stop ≥ +0.5R
* if MFE ≥ 3R: stop ≥ +1.5R
* if MFE ≥ 4R: stop ≥ +2.5R

---

## 11) Pyramiding (Add-ons)

### 11.1 Add-on A (“Free Ride”) at +1.5R

Trigger: MFE crosses 1.5R.

Sequencing (must be enforced):

1. submit stop modification to BE+cushion
2. wait for IB ACK / order status confirmed
3. submit Add-on A **market** (or marketable limit)

Eligibility:

* momentum-state alignment only
* quantity = `ceil(0.5 * base_shares)` (min 1 allowed)

After fill:

* ensure protective stop covers total net shares.

### 11.2 Add-on B at +2R in STRONG_TREND on fresh valid pullback

When:

* MFE ≥ 2R
* STRONG_TREND
* fresh pullback Entry A signal fires
  Submit Add-on B using STOP-LIMIT.

### 11.3 Exit policy

All legs exit together on any exit trigger.
If base exits: cancel pending add-on orders immediately.

---

## 12) Mandatory Time Decay

Exit if:

* held ≥ 480 RTH hours (optimize 240–480)
* profit < +1R

(Recommended to test 360h as well.)

---

## 13) Portfolio Risk & Allocation

### 13.1 Risk per trade (shares)

Per symbol:

* `risk_pct` default 1.0%
* IBIT: 0.75% (higher vol)

Sizing:

* `shares = floor((equity*risk_pct) / (abs(entry-stop)))`

### 13.2 Heat caps

* total portfolio heat ≤ 6% of equity
* compute incremental heat dynamically:

  * `heat = shares * abs(entry-stop) / equity`

Rank candidates each hour:

* primary: daily conviction score
* tie-break: lower estimated slippage / smaller stop distance
* final: deterministic symbol order

---

## 14) Halts / LULD / Auction & Gap Policy (ETF explicit)

### 14.1 New entries

* No new entries during:

  * trading halts / LULD pauses
  * first 15 minutes after market open (optional but recommended)
  * last 10 minutes before close (optional, reduces bad fills)

### 14.2 Stops during halts

If halted and stop cannot execute:

* mark “unfilled protective”
* on reopen:

  * if price beyond stop, exit immediately at market/marketable

### 14.3 Overnight gap policy (critical for ETFs)

At RTH open:

* If open price crosses stop level:

  * exit immediately at market/marketable
  * record gap slippage

No new entries on the first bar after an overnight gap stop-out (cooldown still applies unless voucher).

---

## 15) IB (ib_async) Implementation Notes

### Orders you will use

* Entry: STOP-LIMIT (transmit=True), GTC=False, DAY with manual expiry logic
* Protective stop: STOP (market) during RTH (or STOP-LIMIT with wide band + emergency rule)
* Add-on A: Market or marketable limit
* OCA groups:

  * tie entry order and its protective stop if helpful
  * cancel pending add-on orders when base exits (explicit logic)

### Required broker acknowledgements

* BE stop modification must be acknowledged before Add-on A submission
* Always reconcile net position shares vs stop order quantity

---