## AKC-Helix ETF Swing v1.5 — ETF-Only Execution Spec

**(Optimized for QQQ / USO / GLD / IBIT execution via IBKR; preserves v1.3 signal edge while adapting microstructure, hours, gaps, and sizing.)**
**Execution venue:** Interactive Brokers
**Execution vehicle:** ETFs only (QQQ, USO, GLD, IBIT)
**Timezone:** ET (America/New_York)
**Primary objective:** capture dominant medium-term trends (days→weeks) **both directions**, without starving signals, while controlling gap/liq risk inherent to ETFs.

---

# 0) Instruments, Data, Timeframes

## 0.1 Instruments

* **QQQ** (Nasdaq-100 proxy)
* **USO** (WTI crude proxy)
* **GLD** (Gold proxy)
* **IBIT** (Bitcoin proxy)

## 0.2 Price increment, value

* `tick_size = $0.01`
* `point_value = $1 per $1 move per share`
* Position sizing in **shares**.

## 0.3 Timeframes

* **Daily:** regime, ATRd, vol_pct, VolFactor, trend strength
* **4H:** primary setup generation
* **1H:** triggers, execution, trailing, management

## 0.4 Data source

* Bars and quotes from IBKR.
* Use real-time **bid/ask** for spread checks at order placement.

---

# 1) Session Rules, Entry Windows, Queueing, Gap Handling

## 1.1 Entry window (New risk only)

* New entries and adds only: **09:35–15:45 ET**

Outside window:

* No new entries
* No adds
* Management allowed (stop updates, trailing updates, exits)

## 1.2 End-of-window protocol

At **15:45 ET**:

* Cancel all unfilled entry and add orders immediately.
* Keep protective stops active.

## 1.3 Overnight queue rule (critical for ETF-only)

If a setup confirms outside the entry window:

* Mark as **QUEUED**.
  At next session open (09:35):
* Revalidate structure (§12) and gates (§7) using **fresh ETF data**.
* If still valid, arm orders.
* If ETF opens with a gap that violates the **gap rule** (§1.4), skip.

## 1.4 Gap rule (prevents bad open fills)

When arming queued setups at 09:35:

* If price is already beyond the trigger by more than:

  * `gap_overshoot_cap = 0.20 × ATR1H`
    then **skip** the setup instance (no catch-up at the open).
* If within cap, catch-up is allowed per §11.5.

---

# 2) Deterministic Pivots (Non-Repainting)

For TF ∈ {1H, 4H}, define 5-bar confirmed pivots:

* Pivot High at (t−2) if `High[t−2] == max(High[t−4..t])`
* Pivot Low at (t−2) if `Low[t−2]  == min(Low[t−4..t])`

Pivot confirmed at time t (2 bars after pivot bar). Store at pivot bar:

* timestamp, type, price
* MACD line at pivot bar
* ATR_TF at pivot bar

**Buffer**

* `buffer = max($0.01, 0.05 × ATR_TF)`

---

# 3) Momentum Engine

Compute **MACD(8, 21, 5)** on 1H and 4H:

* **MACD line** used for divergence logic and momentum confirmations.
* **Histogram** used only for trailing momentum hold and optional continuation assist.

---

# 4) News Guard (ETF-Scoped)

Block **new entries/adds** (management allowed) within:

* CPI, NFP: **[-60m, +30m]**
* FOMC decision + presser: **[-60m, +60m]**
* Fed Chair scheduled speeches: **[-30m, +30m]**

**ETF scope rule**

* Only enforce blocks when the market is within (or about to enter) ETF trading hours.

**Optional (more conservative)**

* On CPI/NFP days, do not place new entries until **10:00 ET**.

---

# 5) Daily Regime + Trend Strength (Soft Gate)

Daily:

* `EMA_fast = EMA(20, close)`
* `EMA_slow = EMA(50, close)`
* `ATRd = ATR(14)`

Regime:

* bull: `EMA_fast > EMA_slow` AND `close > EMA_fast`
* bear: `EMA_fast < EMA_slow` AND `close < EMA_fast`
* chop: otherwise

Trend strength:

* `trend_strength = abs(EMA_fast - EMA_slow) / ATRd_today`

**Regime use**

* Not a global block.
* Controls setup enabling and sizing multipliers (§10.1).
* “No-div continuation” disabled unless trend-aligned.

---

# 6) Volatility Engine (VolFactor) + Extreme-Vol Quality Mode

Daily:

* `ATR_base = median(ATRd, 60)`
* `vol_pct = percentile_rank(ATRd_today, 60)`

VolFactor:

```python
VolFactor_raw = ATR_base / ATRd_today
VolFactor = clamp(VolFactor_raw, 0.4, 1.5)
if vol_pct < 20:
    VolFactor = min(VolFactor, 1.0)
```

**Extreme-vol quality mode**
If `vol_pct > 95`:

* Disable **1H-origin setup classes** (Class B and D) for that ETF until next Daily close.
* Allow 4H classes only (A and C).
* Tighten portfolio cap to **1.25R** (see §8.3 override).

---

# 7) Quote Quality + Minimum Stop Gates (ETF-Optimized)

## 7.1 Spread gate (bps + ticks, time-aware)

At order placement:

* `spread = ask - bid`
* `spread_bps = (spread / mid_price) * 10_000`

Allow entries/adds only if BOTH pass:

* `spread ≤ SpreadMax_$`
* `spread_bps ≤ SpreadMax_bps`

Defaults (starting point; calibrate):

* **QQQ:** SpreadMax_$=0.02, SpreadMax_bps=2
* **GLD:** SpreadMax_$=0.02, SpreadMax_bps=2
* **USO:** SpreadMax_$=0.05, SpreadMax_bps=5
* **IBIT:** SpreadMax_$=0.10, SpreadMax_bps=10

**Re-check grace (signal-preserving)**
If spread fails at placement:

* recheck for up to **2 consecutive 1H bars** while setup remains valid.
* if still failing → block.

**Fail-closed rule**
If bid/ask missing or stale → treat as **spread fail** (eligible for recheck).

## 7.2 Minimum stop distance gate (ATR-based)

For ETF per-share stop distance:

* `stop_dist = abs(EntryPrice_ref - Stop0)`
* `MinStop = max(0.10, 0.30 × ATR1H)`

Allow only if:

* `stop_dist ≥ MinStop`

Rationale: avoids micro-noise stops without forcing huge stops.

---

# 8) Risk, Sizing, Heat Caps, Position Limits (ETF Mode)

## 8.1 Base risk

Define:

* `Unit1Risk_$ = 0.50% equity × VolFactor`

## 8.2 Shares sizing (deterministic)

For each entry fill:

* `risk_per_share_$ = abs(fill_price - Stop0)`
* `shares = floor((Unit1Risk_$ × SetupSizeMult) / risk_per_share_$)`

If `shares < MinShares` → skip (default MinShares=1; optionally 5–10 for liquidity).

## 8.3 Heat caps (portfolio and per symbol)

Default:

* Portfolio: `OpenRisk_R + PendingWorstCaseRisk_R ≤ 1.40R`
* Per ETF: `≤ 0.85R`

Extreme-vol override (if any ETF has vol_pct>95):

* Portfolio cap = **1.25R** until next Daily close.

### Risk-on basket rule (QQQ + IBIT)

* If both are **1H-class** (B or D), only one may be armed at a time (priority order).
* If both are **4H-class** (A or C), allow both but second position uses:

  * `SetupSizeMult_second = 0.60×SetupSizeMult`.

## 8.4 Position limits (mandatory for ETFs)

Per ETF define:

* `MaxShares`
* `MaxNotional = MaxShares × mid_price`

Apply after sizing:

* `shares = min(shares, MaxShares)`
* Optional: cap by notional.

---

# 9) Divergence Magnitude Filter (Hybrid)

For divergence candidate (P1,P2) on TF:

* `div_mag_norm = abs(macd(P1) - macd(P2)) / ATR_TF_at_P2`

History per ETF per TF updated on every qualifying divergence event.

Threshold:

```python
if len(history) < 20: threshold = 0.05
else: threshold = max(0.04, percentile(history, 25))
```

Accept only if `div_mag_norm ≥ threshold`.

---

# 10) Setup Classes (ETF-Optimized, Signal-Rich)

## 10.1 SetupSizeMult

**Hidden divergence continuation**

* trend-aligned: 1.00
* chop: 0.65
* countertrend: 0.50

**Classic divergence reversal**

* chop: 1.00
* countertrend (reversing into trend): 0.85
* trend-aligned (fading trend): 0.40

**No-div continuation**

* trend-aligned only: 0.80
* else disabled

Trend-aligned:

* long aligned when regime_bull
* short aligned when regime_bear

---

## 10.2 Class A — 4H Hidden Divergence Continuation (Primary)

**Long**
On confirmation of 4H pivot low L2:

* `L2 > L1`
* hidden divergence: `macd(L2) < macd(L1)`
* magnitude passes §9
* Optional assist: `hist(L2) > hist(L1)` (not required)

Let `H_last_4h` = pivot high between L1 and L2.

Trigger (on 1H):

* `EntryStop = H_last_4h + buffer`

Stop0:

* `Stop0 = L2 − 0.75×ATR4H`

**Adaptive corridor cap**
Let `stop_dist = EntryStop - Stop0` (long; mirror for short):

* if chop: cap = 1.3×ATRd
* if trend-aligned: cap = 1.6×ATRd
* else: cap = 1.4×ATRd
  Skip if `stop_dist > cap`.

Short mirrored.

---

## 10.3 Class B — 1H Hidden Divergence Continuation (DISABLED)

**Status: Disabled** pending quality filter tuning. Backtest showed Class B is a consistent net loser (avg R = -0.306 QQQ, -0.288 USO) across all instruments.

When re-enabled, the following **quality filter** must pass before arming:

* Regime must NOT be CHOP
* Must be trend-aligned (no long in BEAR, no short in BULL)
* `ADX >= 20` (minimum trend strength)

If any condition fails, the Class B setup is rejected.

**Long**
On confirmation of 1H pivot low L2:

* `L2 > L1`
* hidden divergence: `macd(L2) < macd(L1)`
* magnitude passes §9
* `H_last_1h`: pivot high between L1 and L2

Trigger:

* `EntryStop = H_last_1h + buffer`

Stop0:

* Standard: `Stop0 = L2 − 0.50×ATR1H`
* High-vol (vol_pct>80): `Stop0 = L2 − 0.75×ATR1H`

Short mirrored.

---

## 10.4 Class C — 4H Classic Divergence Reversal (Gated)

Allowed only if any:

* regime == chop, OR
* trend_strength today < trend_strength 3 days ago, OR
* `abs(close - EMA_fast) > 1.5×ATRd` (extension)

Short at potential top:

* `H2 > H1`
* classic divergence: `macd(H2) < macd(H1)`
* `L_last_4h` pivot low between H1 and H2
* Trigger: `EntryStop = L_last_4h − buffer`
* Stop0: `Stop0 = H2 + 0.75×ATR4H`
  Corridor cap applies.

Long mirrored.

---

## 10.5 Class D — 1H No-Div Momentum Continuation (Trend-Only)

Enabled only when trend-aligned.
Long:

* `L2 > L1`
* `macd[t] > macd(L2)` AND `macd[t] > macd[t−3]`
* Trigger above `H_last_1h + buffer`
* Stop0 as in Class B

Short mirrored in regime_bear.

---

## 10.6 Priority

If multiple candidates and heat allows only one:

1. Class A (4H hidden continuation)
2. Class C (4H reversal if gated)
3. Class B (1H hidden continuation)
4. Class D (1H no-div continuation)

---

# 11) Execution (ETF-Only, Gap-Aware, Stop-Market Primary)

## 11.1 Primary order type (ETF mode)

**Primary entry is Stop-Market** (not stop-limit) to prevent missed fills on gaps/fast moves.

* Long: Buy Stop (market) at `EntryStop`
* Short: Sell Stop (market) at `EntryStop`

## 11.2 Slippage guard (ETF replacement for rescue complexity)

Record:

* `trigger_price = EntryStop`
* `fill_price`

Define slippage:

* `slip_$ = abs(fill_price - trigger_price)`
* `slip_bps = slip_$ / trigger_price * 10_000`

If slippage exceeds limits (defaults):

* QQQ/GLD: max(0.05, 5 bps)
* USO: max(0.08, 8 bps)
* IBIT: max(0.15, 15 bps)

Then:

* Log as “teleport fill”
* Do **not** add immediately; keep trade but apply tighter initial management:

  * move to +1R rules unchanged, but do not allow add until +2R (one-time penalty)

(You may alternatively “immediate flatten” on huge slips, but that can destroy expectancy; the above is more robust.)

## 11.3 Catch-up entry (still useful, but stricter at open)

If price is already beyond trigger by overshoot:

* `overshoot_cap = 0.15×ATR1H` intraday
* `overshoot_cap_open = 0.20×ATR1H` only at 09:35 re-arming queued setups
  If within cap:
* place marketable limit (buy at last+0.02; sell at last−0.02)
* TTL: 5 minutes
* OCA with primary stop order

## 11.4 End-of-bar backstop

If BoS trigger occurred but order not filled by close of next 1H bar (rare with stop-market):

* cancel setup instance

## 11.5 OCA duplication prevention

Primary stop and catch-up must be in the same OCA group for the same setup instance.

---

# 12) Pending Order Management (TTL + Structure Invalidation)

## 12.1 TTL (starts at placement)

* 1H entries: 6 hours
* 4H entries: 12 hours
* adds: 6 hours
* catch-up TTL: 5 minutes

## 12.2 Structure invalidation

Cancel pending if:

* New confirmed pivot invalidates structure:

  * Long: new pivot low ≤ L2 on the setup TF
  * Short: new pivot high ≥ H2 on the setup TF
* BoS superseded (new pivot materially redefines H_last/L_last)

## 12.3 Window cancellation

At 15:45 ET cancel all unfilled entry/add orders.

---

# 13) Position Management & Profit Capture

## 13.1 R accounting

At Unit1 fill define:

* `Unit1Risk_$` fixed (from placement-time VolFactor)

Definitions:

* `unrealized_R = unrealized_PnL / Unit1Risk_$`
* `R_state = (realized + unrealized) / Unit1Risk_$` (for trailing continuity)

## 13.1a Catastrophic loss cap (-2R hard floor)

On every bar, before any other management logic:

* If `R_state < -2.0` → **immediately flatten** entire position (exit reason: STOP).

Rationale: no trade with a planned -1R stop should ever lose more than -2R. This catches overnight/weekend gap events early. Trades that recover from -2R are rare and not worth the tail risk.

Backtested impact: +4.66R improvement. Worst single-trade loss reduced from -5.4R to -2.25R.

## 13.2 +1R transition (buffered BE)

When `unrealized_R ≥ +1.0`:

* Long: `Stop = max(Stop, AvgEntry − 0.15×ATR1H)`
* Short: mirrored
  Enable:
* trailing
* add_allowed (subject to thresholds §15)

## 13.3 +2.5R partial

When `unrealized_R ≥ +2.5`:

* sell/buy to cover **50%** of shares
* ratchet stop:

  * store `R_price = Unit1Risk_$ / shares_open` at fill for continuity
  * Long: `Stop = max(Stop, AvgEntry + 1.0×R_price)`
  * Short: mirrored

## 13.4 +5R runner (standard)

When `unrealized_R ≥ +5.0`:

* exit **25% of remaining**
* trailing multiplier bonus: `mult += 0.5` (capped)

## 13.5 Stale exit (setup-dependent)

**Early stale (losers stuck in no-man's land):**

Exit next 1H close if ALL:

* `bars_held_1h >= 20`
* trailing never activated (position never reached +1R)
* `R_state < 0`
* not in Class C minimum hold period (first 12 bars)

These are trades that never became profitable enough to activate trailing but were not stopped out either. Cutting early at 20 bars limits drift.

Backtested impact: +2.0R improvement vs 30-bar threshold.

**Standard stale (non-performing positions):**

Exit next 1H close if:

* **1H-origin (B/D):** after **40×1H bars**, and `R_state < +0.5`
* **4H-origin (A/C):** after **15×4H bars**, and `R_state < +0.5`

---

# 14) Trailing Engine (R-Adaptive Chandelier + Momentum Hold + Regime Tightening)

Active only after +1R.

## 14.1 Multiplier

```python
mult_base = max(2.0, 4.0 - (R_state / 5.0))
```

## 14.2 Momentum hold

If `R_state > 2`:

* `momentum_strong = macd_1h[t] > macd_1h[t−5] AND hist_1h[t] > 0`
  If momentum_strong:
* `mult = clamp(mult_base + 0.5, 2.0, 4.0)`
  Else:
* `mult = mult_base`
  Then apply +5R bonus if triggered.

## 14.3 Chandelier stop (1H)

Default lookback = 30×1H (optional per ETF: USO 24, IBIT 20)

* Long: `Chand = HighestHigh(lookback) − mult×ATR1H`

  * `Stop = max(Stop, Chand)`
* Short: `Chand = LowestLow(lookback) + mult×ATR1H`

  * `Stop = min(Stop, Chand)`

Stop never loosens.

## 14.4 Regime tightening

* If regime downgrades to **chop** from aligned:

  * `mult = max(2.0, mult − 0.25)`
* If regime **flips against** position:

  * `mult = max(2.0, mult − 0.5)`

---

# 15) Adds (2 Units Max) + ETF Close Add-Risk Control

Max units: Unit1 + 1 add.

## 15.1 Add thresholds (setup-dependent)

* Origin 4H (A/C): add allowed after `unrealized_R ≥ +1.0`
* Origin 1H (B/D): add allowed after `unrealized_R ≥ +1.5`

Additional prerequisites:

* entry window open
* time before **15:00 ET**
* not news-blocked
* spread + min-stop gates pass
* heat caps pass

## 15.2 Add setup

* new pivot in direction confirms (e.g., L3 > L2 for longs)
* price remains beyond last BoS level in trade direction
* momentum: `macd[t] > macd[t−3]` and `macd[t] > macd(L2)`

Trigger: break `H3_last + buffer` (short mirrored)

Add risk budget:

* `add_risk_$ = 0.50×Unit1Risk_$`
  Shares sized with same sizing rule as Unit1.

## 15.3 Single global stop + breakeven tighten on add fill

One stop only. It never loosens. Add stop is conceptual for risk accounting only.

**Breakeven tighten on add fill:**

When an add-on fills, the position has proven profitable (at least +0.6R for 4H, +1.5R for 1H). Move the protective stop to at least breakeven:

* `be_level = AvgEntry ± 0.15×ATR1H` (+ for long, - for short)
* Long: `Stop = max(Stop, be_level)`
* Short: `Stop = min(Stop, be_level)`

This ensures the add-on risk (~0.5R) is the ONLY remaining risk from that point forward. Without this, both original + add shares can still lose the full -1R to stop plus gap amplification (turning a -1R into -1.5R or worse).

Backtested impact: +7.35R improvement.

## 15.4 ETF close add-risk rule (replaces futures 16:25 rule)

At **15:40 ET**:

* If add unit active and `unrealized_R < 2.0` → flatten add only
* Else keep both units overnight

---

# 16) Implementation Requirements (IBKR via ib_async)

* Execution must be event-driven:

  * market data events
  * order status events
  * scheduled timers for TTL (async tasks), not sleep loops for critical sequencing
* OCA groups for mutually exclusive entry paths (primary vs catch-up)
* Protective stop management must never loosen
* Logging must capture:

  * gate decisions (spread, min-stop, news, window, heat, corridor)
  * slippage at fill (teleport classification)
  * trailing continuity around partials (+2.5R, +5R)
  * add/close add-risk actions

---

# 17) Validation & Reporting (Minimum)

Per ETF per setup class per regime:

* trades, win rate, avg R/trade, R/week, max DD (R), avg hold time
* fill rate (stop-market should be near 100% when triggered)
* catch-up usage rate and outcomes
* slippage distribution at entry (teleport rate)
* block rates by gate
* trailing continuity around partials
* portfolio overlap and drawdown clustering (QQQ+IBIT especially)

---

## Symmetry

All rules mirrored for shorts.

---
