## Multi-Asset Swing Breakout & Trend Campaign — v3.3-ETF (Final Consolidated Spec)

**Execution vehicles:** **QQQ, USO, GLD, IBIT** (ETF-only execution)
**Objective:** participate in dominant medium-term trends (days → weeks) across macro/high-beta assets with strong expectancy, without starving signals.

---

# 0) Edge Thesis

This system exploits a repeatable pattern in trending assets:

1. **Compression** (tight range) precedes large moves.
2. **Expansion** is confirmed by **displacement from campaign fair value** (box-anchored AVWAP).
3. Returns are maximized by **campaign management**: partial profits + a **runner** + controlled **pullback adds**.
4. To preserve frequency, most conditions are **tiering inputs** (size/permissions/exits), not vetoes.
5. ETF reality (RTH-only + gaps) is handled explicitly so backtests don’t overstate fill quality.

---

# 1) Universe, Mapping, Timeframes, Calendars

## 1.1 ETFs (Phase 1)

* **QQQ** (equities / NQ proxy)
* **USO** (crude proxy)
* **GLD** (gold proxy)
* **IBIT** (bitcoin proxy)

## 1.2 Timeframes

* **Context:** Daily + 4H (constructed from ETF RTH bars)
* **Signal/Campaign:** Daily (ETF daily bars)
* **Execution:** 1H (ETF hourly bars; RTH only)

## 1.3 Trading session definitions (ETF)

* **RTH session:** 09:30–16:00 ET
* **Daily bar:** ETF daily OHLCV (RTH + official close; use your data vendor’s standard)
* **Hourly bars:** only within RTH; first hour has special volume seasonality (handled in RVOL_H normalization)

**No trading outside RTH.** All entries, TTLs, and pending re-checks operate on RTH time.

---

# 2) Data Requirements (ETF)

Minimum (per ETF):

* Daily OHLCV: ≥ 3 years preferred (as available)
* 1H OHLCV: ≥ 18 months recommended (for slot normalization)
* 4H OHLCV: derived from 1H bars
* Corporate actions handled (splits, dividends) if using adjusted data (be consistent)

Recommended:

* NBBO or spread proxy for friction calibration
* “Expected open” / auction prints if available (for MOO modeling)

---

# 3) Core Definitions & Conventions

* **ATR14_D, ATR50_D** computed on ETF daily bars
* **ATR14_H** computed on ETF hourly bars
* **ATR14_4H** computed on ETF 4H bars (from hourly aggregation)
* **AVWAP_D / AVWAP_H:** campaign VWAP anchored on ETF volume (see §6)
* **WVWAP:** weekly VWAP anchored on ETF volume; resets Monday at **09:30 ET**
* **Disp:** `abs(close_D - AVWAP_D) / ATR14_D`
* **Disp_th:** past-only quantile threshold for Disp

Volume:

* **RVOL_D:** `volume_D / median(volume_D, past 20 sessions)` (past-only)
* **RVOL_H slot-normalized:**
  `RVOL_H = volume_H / median(volume_H for same (day_of_week, hour_slot) over past N weeks)` (past-only)

---

# 4) Campaign State Machine (Explicit)

Per ETF maintain:
`campaign_state ∈ {INACTIVE, COMPRESSION, BREAKOUT, POSITION_OPEN, CONTINUATION, DIRTY, EXPIRED, INVALIDATED}`

State variables:

* `campaign_id` (increment on new box activation)
* `box_version` (increments only on DIRTY reset via box_shifted)
* `reentry_count[direction][box_version]`
* `add_count`, `campaign_risk_used`
* `pending_entry` snapshot (if any)

---

# 5) Compression Detection (Daily Campaign Box)

## 5.1 Adaptive box length (daily) with hysteresis

`ATR_ratio = ATR14_D / ATR50_D`

Buckets:

* `<0.70 → L=8`
* `0.70–1.25 → L=12`
* `>1.25 → L=18`

Hysteresis: switch only if bucket holds ≥3 consecutive daily closes.

## 5.2 Candidate boundaries

* `range_high_roll = highest(high_D, L)`
* `range_low_roll  = lowest(low_D, L)`
* `box_height = range_high_roll - range_low_roll`
* `containment = count(range_low_roll ≤ close_D ≤ range_high_roll over L) / L`
* `squeeze_metric = box_height / ATR50_D`

## 5.3 Activation (binary; frequency-preserving)

Activate if:

* `containment ≥ 0.80`
* `squeeze_metric ≤ SQUEEZE_CEIL`

Default:

* `SQUEEZE_CEIL = 1.10` (validate; intentionally not tight)

## 5.4 Compression tiers (past-only bands; explicit lookback)

Maintain rolling history of squeeze_metric.
Defaults:

* `LOOKBACK_SQ = 60` daily bars (past-only)
* `sq_good  = squeeze_metric ≤ q_past_only(0.30, 60)`
* `sq_loose = squeeze_metric ≥ q_past_only(0.65, 60)`
* else neutral

These affect sizing/permissions/stop selection, not activation.

## 5.5 Freeze on activation (start campaign)

On transition INACTIVE→COMPRESSION:
Freeze:

* `box_high, box_low, box_height, box_mid`
* `anchor_time = next RTH open (09:30 ET) of the activation day`
  *(i.e., anchor VWAP begins at the first tradable ETF bar after the box activates)*
* set `campaign_id += 1`, `box_version = 0`, reset counters
* initialize AVWAP accumulators from anchor_time

---

# 6) VWAP Anchors (ETF-Volume-Based)

## 6.1 AVWAP (campaign fair value)

Computed from ETF prints only, anchored at `anchor_time`:

* `tp = (H+L+C)/3`
* `AVWAP = cumsum(tp*vol)/cumsum(vol)` from anchor_time

Compute:

* `AVWAP_H` updated each hourly close
* `AVWAP_D` computed from daily bars consistent with the anchor (or by resampling intraday to daily)

## 6.2 WVWAP (weekly VWAP)

Reset at **Monday 09:30 ET**.
Computed from ETF intraday bars (preferred) or daily approximation if intraday not available.

## 6.3 Pullback reference selector (adds)

For add-on pullbacks:

* Use WVWAP if within 2 ATR14_D
* Else use AVWAP_H if within 2 ATR14_D
* Else use EMA20_1H

```python
def pullback_ref(price, wvwap, avwap_h, ema20_h, ATR14_D):
    if abs(price-wvwap)/ATR14_D <= 2.0: return wvwap
    if abs(price-avwap_h)/ATR14_D <= 2.0: return avwap_h
    return ema20_h
```

---

# 7) Regime Classification (ETF Context)

## 7.1 4H regime (dominant trend)

Indicators:

* EMA50_4H, ATR14_4H, ADX14_4H
* slope_4H = EMA50_4H[t] - EMA50_4H[t-3]
* slope_th_4H = 0.10 * ATR14_4H

Regime:

* **Bull Trend:** slope_4H > th and price > EMA50_4H
* **Bear Trend:** slope_4H < -th and price < EMA50_4H
* **Range/Chop:** otherwise or ADX14_4H < 20

## 7.2 Daily strong slope (for hard block)

* daily_slope = EMA50_D - EMA50_D[5]
* strong oppose if magnitude > 0.08*ATR14_D and sign opposes direction

## 7.3 Hard block (double-counter)

Hard block a direction only if:

* 4H trend opposes AND daily slope opposes strongly

---

# 8) Breakout Qualification (Daily) — Core Eligibility

## 8.1 Structural breakout (required)

* Long: close_D > box_high
* Short: close_D < box_low

## 8.2 Displacement confirmation (required)

Compute:

* Disp = abs(close_D - AVWAP_D) / ATR14_D
* atr_expanding = ATR14_D > SMA(ATR14_D,50)
* q_disp_eff = q_disp - 0.05 if atr_expanding else q_disp
  Defaults: q_disp = 0.70 (sweep 0.65–0.80)

Threshold:

* Disp_th = q_past_only(Disp_hist, q_disp_eff, long lookback; past-only)

Require:

* Disp ≥ Disp_th

## 8.3 Breakout Quality Reject (rare hard veto)

Reject breakout day if:

* bar_range > 2.0*ATR14_D AND
* (body_ratio < 0.25 OR adverse_wick_ratio > 0.55)
  Optionally include `RVOL_D > 2.0` if reliable.

---

# 9) Evidence Score (Daily) — Tiering & Permissions (Not Eligibility)

Eligibility remains: structural + displacement + not rejected + risk controls + not hard-blocked.

Score is used to:

* mildly adjust `quality_mult`
* influence aggressive permissions (Entry B, adds)
* tune stop selection and exit tier downgrade likelihood

## 9.1 RVOL_D score component (A) + low-volume exception (C)

Compute RVOL_D:

* RVOL_D = volume_D / median(volume_D, past 20)

Volume score:

```python
def volume_score_component_daily(RVOL_D):
    if RVOL_D >= 1.5:
        return +1
    elif RVOL_D >= 1.1:
        return 0
    elif RVOL_D >= 0.8:
        return -1
    else:
        return -1
```

Low-volume day exception:

```python
vol_score = volume_score_component_daily(RVOL_D)
if displacement_pass and (Disp >= 1.15*Disp_th) and (RVOL_D < 0.8):
    vol_score = max(vol_score, -1)
```

## 9.2 Other score components (suggested default)

* +1 if sq_good
* -1 if sq_loose
* +1 if 4H regime aligns direction
* 0 if 4H range/mixed but not caution
* -1 if caution (opposes but not hard-blocked)
* +1 if two consecutive daily closes outside box in breakout direction
* +1 if atr_expanding (optional)

## 9.3 Score → mild quality adjustment

To avoid starving signals:

* score_adj = clamp(1.0 + 0.05*score, 0.85, 1.15)
* quality_mult *= score_adj

---

# 10) DIRTY Handling (Failed Breakouts)

## 10.1 DIRTY trigger (ETF; instrument-style defaults)

M_BREAK days after breakout closes back inside:

* QQQ/GLD: 2
* USO/IBIT: 3

DIRTY blocks same-direction entries until reset.

## 10.2 Opposite direction allowed while DIRTY

Allowed if opposite structural breakout + displacement pass AND:

* Disp ≥ 1.10 * Disp_th

## 10.3 DIRTY reset

Reset DIRTY→COMPRESSION if:

* squeeze_good on NEW rolling bounds AND
* (box_shifted OR dirty_duration ≥ 0.5*L_used)

box_shifted if both:

* abs(new_high - dirty_high) ≥ 0.35*ATR14_D
* abs(new_low  - dirty_low ) ≥ 0.35*ATR14_D

Re-entry counter reset semantics:

* if reset via box_shifted: increment box_version and reset reentry_count for that version
* if duration-only: keep counters

---

# 11) Breakout State, Expiry, Continuation Mode

## 11.1 Breakout state

When qualified:

* campaign_state = BREAKOUT
* breakout_date, breakout_direction, bars_since_breakout = 0

## 11.2 Expiry + decay

* ATR_pctl_60 = percentile_rank(ATR14_D, 60)
* expiry_bars = clamp(round(5*(ATR_pctl_60/50)), 3, 10)
* hard_expiry_bars = expiry_bars + 5
* expiry_mult:

  * 1.0 up to expiry
  * then decay step 0.12 to floor 0.30

## 11.3 Continuation mode (with time component)

MM:

* long MM = box_high + 1.5*box_height
* short MM = box_low  - 1.5*box_height

R_proxy:

* long (close_D - box_high)/ATR14_D
* short (box_low - close_D)/ATR14_D

Enable continuation if any:

* price ≥ MM OR R_proxy ≥ 2.0 OR
* (R_proxy ≥ 1.5 AND bars_since_breakout ≥ 5 AND (Aligned OR disp_mult ≥ 0.85))

In continuation:

* Entry A/B disabled
* C_continuation and adds allowed (subject to rules)

---

# 12) Hourly Execution (RTH) — Entries A/B/C

## 12.1 Slot-normalized RVOL_H (B)

Define hour_slot as one of:

* 09:30–10:30, 10:30–11:30, …, 15:00–16:00
  Slot key:
* (day_of_week, hour_slot)

Compute:

* RVOL_H = volume_H / median(volume_H for same slot over past N weeks)
  Default:
* LOOKBACK_WEEKS = 12 (past-only)

### Handling the first hour

Either:

* keep first hour as its own slot (recommended), or
* exclude it from RVOL_H gating (not recommended—better to normalize explicitly)

## 12.2 Common eligibility (for any entry attempt)

Require:

* campaign_state allows entry type
* breakout not invalidated/expired
* displacement_pass true
* not rejected
* not hard-blocked
* position limits/heat pass
* friction gate passes
* pending logic satisfied if used

## 12.3 Entry A (AVWAP_H retest + reclaim; limit-first)

Trigger:

* touch AVWAP_H and reclaim with buffer:

  * reclaim_buffer = max(0.12*ATR14_H, 0.03*ATR14_D)

Order:

* limit at AVWAP_H ± 0.03*ATR14_D
* TTL in RTH hours (e.g., 4–6 RTH hours)
* cancel if re-enters box meaningfully

## 12.4 Entry B (sweep + reclaim; aggressive) — RVOL_H gating (B)

Trigger:

* sweep_depth = 0.25*ATR14_D
* long: low_H < AVWAP_H - sweep_depth AND close_H > AVWAP_H
* short mirror

Permission:

* aligned regime
* quality_mult ≥ 0.55
* not continuation
* not DIRTY same-direction
* AND volume gating:

**Allow if** RVOL_H ≥ 0.8
**Else require** disp_mult ≥ 0.90

Execution:

* marketable limit or market-on-close (RTH only)
* slippage modeled in bps (see §15)

Stop:

* Entry B always uses edge stop (§16)

## 12.5 Entry C_standard (2-hour hold above/below AVWAP_H)

As v3.2.

## 12.6 Entry C_continuation (pause constraint)

As v3.2:

* requires hold + pause (max range of last two bars ≤ 0.40*ATR14_H)

---

# 13) Continuity Pullback Adds (RTH) — RVOL_H-controlled (B)

## 13.1 Add eligibility

Allowed only if:

* TP1 achieved or runner active
* aligned regime
* add limits pass (count + risk budget)
* pullback touches ref and resumes (first close back in direction)

## 13.2 Add trigger

Ref chosen by §6.3.
Resume condition:

* basic: close back across ref in trend direction
* if high vol regime: require reclaim beyond ref by reclaim_buffer

## 13.3 RVOL_H effect on adds (B)

If RVOL_H ≥ 0.8:

* add risk = 0.5× initial risk (scaled)

If RVOL_H < 0.8:

* reduce add risk: `add_risk *= 0.7`
* AND require “acceptance” on resume bar:

  * close-location value (CLV) strong:

    * long: close in top 30% of bar range
    * short: close in bottom 30%

## 13.4 Add stop + campaign risk cap

Add stop:

* long: min(pullback_low, ref) - 0.5*ATR14_D*atr_mult
* short mirror

Limits:

* MAX_ADDS_PER_CAMPAIGN = 2
* total campaign risk ≤ 1.5× initial risk

Partial fills:

* manage filled only; do not requeue remainder
* if fill_pct < 0.25: mark probe and block further adds

---

# 14) Sizing (Shares) + Tiering

## 14.1 Base risk per ETF (equity %)

Defaults (tune):

* QQQ: 0.60%
* GLD: 0.50%
* USO: 0.40%
* IBIT: 0.35%

## 14.2 Risk regime adjustment (capped)

risk_regime = ATR14_D / SMA(ATR14_D,50)
base_risk_adj = base_risk * clamp(1.0/risk_regime, 0.75, 1.05)

## 14.3 Quality multiplier (continuous)

quality_mult = clamp(regime_mult * disp_mult * squeeze_mult * corr_mult, 0.25, 1.0)
Then multiply by score_adj (§9.3)

Components:

* regime_mult: Aligned 1.00 / Neutral 0.65 / Caution 0.40
* disp_mult: 0.70 + 0.30*disp_norm (T70/T90 from past-only history)
* squeeze_mult: sq_good 1.05 / neutral 1.00 / sq_loose 0.85
* corr_mult: as §18

## 14.4 Expiry multiplier + fee/micro guard

expiry_mult from §11.2.

Micro guard:
If floored risk, expiry_mult < 0.60, and exit tier would be Caution → skip unless disp_mult ≥ 0.85.

## 14.5 Final risk dollars

final_risk_pct = base_risk_adj * quality_mult * expiry_mult
Clamp between 0.20×base_risk_adj and base_risk_adj (subject to micro guard).

final_risk_dollars = equity * final_risk_pct

## 14.6 Share sizing

risk_per_share = abs(entry - stop)
shares = floor(final_risk_dollars / (risk_per_share + cost_buffer_per_share))

* shares must be ≥ 1
* optional liquidity cap:

  * do not exceed X% of median hourly volume (e.g., 1–2%) for adds and entries

---

# 15) Friction Model (ETF bps-based) + Gate

## 15.1 Slippage/spread assumptions (initial; tune)

Use **round-trip bps estimate** by ETF:

* QQQ: 1–3 bps
* GLD: 2–4 bps
* USO: 4–8 bps (can widen)
* IBIT: 4–10 bps (varies)

Estimate round-trip friction in dollars:

* `friction_$ ≈ fee_bps_est * notional`

## 15.2 Friction gate (hard)

Block if:

* friction_$ > 0.10 * final_risk_dollars

This is critical for USO/IBIT in particular.

---

# 16) Stops & Gap Handling (ETF reality)

## 16.1 Stop selection (quality-aware, entry-type aware)

* Entry B always uses **edge stop**
* Entry A/C may use midpoint stop if sq_good

Buffer:

* buffer = ATR_STOP_MULT(symbol) * ATR14_D
  Defaults:
* QQQ/GLD: 1.0
* USO/IBIT: 1.3

Mid stop:

* long: box_mid - buffer; short: box_mid + buffer

Edge stop:

* long: box_low - buffer; short: box_high + buffer

Adds use add-stop logic (§13.4).

## 16.2 Gap-through-stop backtest/live rule (must-have)

If next session open gaps beyond stop:

* assume stop fill at **open price** (adverse)
* log gap_stop event

This prevents overstating risk control.

---

# 17) ETF Overnight & MOO Rules

## 17.1 Entry B MOO allowance

If Entry B trigger occurs after RTH close:

* may place MOO next session **only** if gap guard passes and still eligible at open.

## 17.2 A/C overnight handling

Do not place A/C overnight. Re-evaluate at next RTH open and place with placement-time AVWAP_H and conditions.

## 17.3 Gap guard (hard)

Skip MOO if:

* abs(expected_open - prior_close) > 1.5*ATR14_D

---

# 18) Correlation Controls (ETF portfolio)

## 18.1 corr_mult (same-direction exposure)

Compute rolling correlation (4H returns preferred; 60 bars past-only).
If any same-direction open peer corr > 0.70:

* corr_mult = 0.85 if both aligned-at-entry
* else corr_mult = 0.70
  Else 1.0

## 18.2 Correlation-aware heat penalty (hard in heat calc)

If candidate corr > 0.70 with any same-direction open position:

* effective incremental risk = risk * 1.25 for heat checks

---

# 19) Pending Mechanism (RTH-based)

If entry is valid but blocked only by MaxPositions/Heat/Ops:

* create PENDING up to **PENDING_MAX_RTH_HOURS = 12**
* recheck each hourly close
* place using placement-time values if block clears and trigger still holds
* expire on invalidation/expiry or time limit

Log snapshots at signal vs placement.

---

# 20) Exits (Tiered TP + Runner)

## 20.1 Exit tier

Base tier from trade regime; downgrade if quality_mult low (as prior spec). Freeze at entry.

## 20.2 TP schedule

Aligned: TP1 1.5R, TP2 3.0R, runner remainder
Neutral: TP1 1.0R, TP2 2.0R, runner smaller
Caution: TP1 0.8R, TP2 1.6R, runner minimal

After TP1:

* move stop to BE + small buffer (e.g., 0.1*ATR14_D)

## 20.3 Runner trailing (4H ATR; ratchet-only)

Trail distance tightens after MM or high R.
Optional EMA50_4H floor:

* long floor = EMA50_4H - 0.5*ATR14_4H; short mirror

## 20.4 Stale exit + tighten warning

* Warning: after 8 days if < +0.5R, tighten trail multiplier ×0.8
* Exit: after 10–12 days if < +0.5R

---

# 21) Re-entry (box-versioned)

Max 1 re-entry per direction per `box_version` after stop/stale, cooldown 3 days, realized_R ≥ -0.75, breakout still valid.

---

# 22) Portfolio Risk Controls (ETF-tuned)

* Max concurrent positions: 4
* Max same direction: 2
* **Max portfolio heat:** recommend **2.0–2.5%** (ETF gap risk)
* Weekly throttle: size ×0.5 if rolling 5 days ≤ −5R
* Monthly halt: HALT if rolling 20 days ≤ −8R

---

# 23) Telemetry (expanded for ETF + volume gating)

Log per attempt/trade:

* campaign_id, box_version, state transitions
* AVWAP anchor_time, WVWAP reset basis
* Disp, Disp_th, q_disp_eff, atr_expanding
* RVOL_D, vol_score, low-volume exception trigger
* RVOL_H slot_id, RVOL_H, Entry B permission path (RVOL_H pass vs disp_mult override)
* adds: RVOL_H gating path, CLV check, add risk multiplier
* friction bps estimate, friction_$, friction-to-risk
* gap_stop events and MOO gap guard outcomes
* realized slippage proxy if available (VWAP vs fill)

Aggregate reporting by:

* symbol, direction, entry type/subtype, regime, exit tier, year
* expectancy after costs, PF, max DD, trade count
* runner contribution and add contribution
* frequency sanity: target ~4–10 trades/month across the universe (incl adds)

---

# 24) Execution Precedence

1. Monthly halt
2. Weekly throttle
3. Daily loss limits (if used)
4. Position/heat limits (with corr heat penalty)
5. Ops/data integrity
6. Campaign state constraints
7. DIRTY constraints
8. Structural breakout + displacement
9. Breakout reject filter
10. Continuation gating
11. Entry A→B→C + adds rules
12. Micro/fee guard + friction gate
13. Sizing + stop selection
14. Exit management

---

# 25) Validation Protocol (ETF-specific)

* Include: commissions, spreads, bps slippage, MOO modeling, gap-through-stop rule
* Past-only quantiles everywhere
* Walk-forward OOS
* Sensitivity: q_disp, SQUEEZE_CEIL, RVOL thresholds, stop ATR mults, add RVOL_H rules
* Report: expectancy after costs, DD, trend participation, frequency

---

## Practical note (implementation choice)

For ETFs, the cleanest implementation is: **compute everything directly on ETF OHLCV** (including AVWAP/WVWAP, RVOL_D/H, ATR, EMA, ADX). That avoids futures↔ETF basis mismatches and makes the backtest/live behavior consistent.


---

Below is the **v3.3-ETF strategy** presented as (1) an implementation-oriented spec summary and (2) **relevant pseudocode** structured the way you’d build it in Python with **Interactive Brokers + `ib_async`**. It’s not runnable drop-in code; it’s the “blueprint” you implement from.

---
