# Multi-Strategy Portfolio: $10,000 Account Illustrative Example

A complete walkthrough tracing every calculation through the system — from account bootstrap to position sizing, OMS risk gates, cross-strategy coordination rules, and final P&L.

---

## Phase 0: Account Bootstrap

When `main_multi.py` starts, it derives all parameters from the $10,000 equity:

**OMS-level R units** (used for portfolio heat tracking):
```
ATRSS  urd = $10,000 × 1.00%  = $100    →  1 ATRSS-R  = $100
Helix  urd = $10,000 × 0.50%  = $50     →  1 Helix-R  = $50
Breakout urd = $10,000 × 0.50% = $50    →  1 Breakout-R = $50
```

**Strategy-level sizing** (used internally by each engine for position sizing):
```
ATRSS QQQ:  unit1 = $10,000 × 0.60% × vol_factor  =  $60 × vf
ATRSS GLD:  unit1 = $10,000 × 0.65% × vol_factor  =  $65 × vf
Helix:      unit1 = $10,000 × 0.50% × vol_factor  =  $50 × vf
```

These differ because the OMS R-unit is the portfolio-level reference for heat accounting, while the strategy-level risk drives the actual share count.

**Heat budget at start:**
```
Portfolio cap:   1.50R   (hard limit across all strategies)
ATRSS ceiling:   1.00R   (soft, per-strategy)
Helix ceiling:   0.85R   (soft, per-strategy)
Breakout ceiling: 0.65R   (soft, per-strategy)

Daily stops:  ATRSS -2.0R,  Helix -2.5R,  Breakout -2.0R
Portfolio daily stop: -3.0R
```

**What does 1.50R mean in dollars?** It depends on who's using it. If the entire budget were consumed by ATRSS alone: 1.5 x $100 = $150 at risk. If by Helix alone: 1.5 x $50 = $75. In practice the mix is ~$100-$120 total dollar risk across strategies — about 1.0-1.2% of equity.

---

## Day 1 (Tuesday): Helix Enters QQQ Long

**Market state:**
QQQ $522.00 | ATR_1H $2.80 | ATR_4H $5.40 | 4H regime: CHOP | ADX 22

Helix detects a Class B hidden bullish divergence on the 1H chart — MACD made a higher low while price made a lower low. The pivot separation is 12 bars (> 8 minimum).

**Stop placement:**
```
L2 (pivot low) = $520.80
stop = L2 - STOP_1H_STD x ATR_1H
     = $520.80 - 0.50 x $2.80
     = $520.80 - $1.40
     = $519.40
```

**Entry:** BoS (break-of-structure) level at $522.20 (1H swing high)

**Risk per share** = $522.20 - $519.40 = **$2.80**

**Position sizing:**
```
vol_factor     = 1.0  (ATR in normal range)
unit1_risk     = $10,000 x 0.005 x 1.0 = $50.00
setup_size_mult = CLASS_B_SIZE_CHOP = 0.65  (chop regime -> reduced)

Coordinator check: has_atrss_position("QQQ", "LONG")?
  -> ATRSS position book is empty — no boost

qty = (unit1 x size_mult) / (risk_per_share x point_value)
    = ($50 x 0.65) / ($2.80 x 1.0)
    = $32.50 / $2.80
    = 11.6 -> round to 12 shares
```

**OMS risk gate (6 checks):**
```
1. Global standdown?          No                 PASS
2. Event blackout?            No                 PASS
3. Session block?             No (within 09:35-15:45 ET)  PASS
4. Helix daily halt?          realized_R = 0.00 > -2.5R   PASS
5. Portfolio daily halt?      realized_R = 0.00 > -3.0R   PASS
5.5 Max working orders?       0 < 4              PASS
6. Portfolio heat cap?
   risk_dollars = 12 x $2.80 = $33.60
   new_risk_R  = $33.60 / $50 = 0.672R
   total       = 0.000 + 0.000 + 0.672 = 0.672R <= 1.50R  PASS
6b. Helix ceiling?
   strat_heat  = 0.000 + 0.672 = 0.672R <= 0.85R  PASS
7. Priority reservation?
   remaining   = 1.50 - 0.00 = 1.50R
   1.50R >= 2 x 0.672R = 1.344R — plenty of room  PASS
-> APPROVED
```

**Fill:** 12 shares QQQ @ $522.20 | Notional: $6,266.40 (63% of equity)

**Portfolio state:**
| | open_risk_R | open_risk_$ |
|---|---|---|
| ATRSS | 0.000 | $0.00 |
| Helix | 0.672 | $33.60 |
| **Portfolio** | **0.672** | **$33.60** |
| Remaining heat | 0.828R | |

Coordinator book: `("AKC_HELIX", "QQQ") -> LONG, 12 shares @ $522.20`

---

## Day 2 (Wednesday): ATRSS Enters QQQ Long -> Rule 1 Fires

**Market state:**
QQQ $523.50 | Daily EMA20 $518 | EMA55 $512 | ADX 26 (trend regime) | ATR_daily $8.00 | ATR_hourly $2.80

ATRSS detects: price pulled back to EMA_pull_normal (40-period hourly), recovered with momentum confirmation, entry quality score passes gate.

**Stop placement:**
```
Daily stop:  $523.50 - 2.1 x $8.00 = $523.50 - $16.80 = $506.70
Hourly stop: $523.50 - 2.7 x $2.80 = $523.50 - $7.56  = $515.94
Tighter (higher) = $515.94
```

**Risk per share** = $523.50 - $515.94 = **$7.56**

**Position sizing:**
```
unit1_risk     = $10,000 x 0.006 x 1.0 = $60.00  (QQQ base_risk_pct)
setup_size_mult = 1.0
qty = $60 / ($7.56 x 1.0) = 7.94 -> 8 shares
```

**OMS risk gate:**
```
risk_dollars = 8 x $7.56 = $60.48
new_risk_R   = $60.48 / $100 = 0.605R

6.  Portfolio heat: 0.672 + 0 + 0.605 = 1.277R <= 1.50R     PASS
6b. ATRSS ceiling: 0 + 0.605 = 0.605R <= 1.00R               PASS
7.  Priority: ATRSS is priority 0 (highest) -> no reservation  PASS
-> APPROVED
```

**Fill:** 8 shares QQQ @ $523.50 | Notional: $4,188

**Rule 1 fires immediately.** In the fill callback, the coordinator sees:
```
coordinator.on_fill(strategy_id="ATRSS", symbol="QQQ", role="ENTRY")
  -> checks position_book for ("AKC_HELIX", "QQQ")
  -> FOUND: Helix has 12 shares QQQ LONG
  -> emits TIGHTEN_STOP_BE to Helix
```

Helix engine receives the coordination event and computes breakeven:
```
fill_price = $522.20  (Helix's entry)
BE_ATR1H_OFFSET = 0.15
atr_offset = 0.15 x $2.80 = $0.42
be_level   = $522.20 + $0.42 = $522.62  (LONG -> above entry)
current_stop = $519.40

$519.40 < $522.62 -> tighten stop from $519.40 to $522.62
```

Helix protective stop replaced: **$519.40 -> $522.62**

**Portfolio state:**
| | open_risk_R | open_risk_$ |
|---|---|---|
| ATRSS | 0.605 | $60.48 |
| Helix | 0.672 | $33.60 |
| **Portfolio** | **1.277** | **$94.08** |
| Remaining heat | 0.223R | |

---

## Day 3 (Thursday): QQQ Dips -> Helix Stopped at Breakeven

QQQ drops to $522.50 intraday, touching Helix's tightened stop at $522.62.

**Helix stop fill:** 12 shares @ $522.62

**Helix P&L:**
```
entry     = $522.20
exit      = $522.62
P&L/share = $522.62 - $522.20 = +$0.42
total P&L = 12 x $0.42 = +$5.04

pnl_R = $5.04 / $50 = +0.101R  (tiny winner instead of a loss)
```

**Without Rule 1**, Helix's stop was at $519.40. If QQQ continued dropping to $520 (which it did — it bounced at $519.80):
```
Hypothetical exit at stop = $519.40
Hypothetical P&L = 12 x ($519.40 - $522.20) = 12 x (-$2.80) = -$33.60
That would have been -0.672R
```

**Rule 1 saved: $33.60 + $5.04 = $38.64 (0.773R)**

**Risk state update — Helix position released:**
```
released_R       = 0.672R  (Helix open risk drops to 0)
daily_realized_R += 0.101R (Helix)
portfolio daily_realized_R += 0.101R
```

**Portfolio state:**
| | open_risk_R | daily_realized_R |
|---|---|---|
| ATRSS | 0.605 | 0.000 |
| Helix | 0.000 | +0.101 |
| **Portfolio** | **0.605** | **+0.101** |
| Remaining heat | 0.895R | |

---

## Day 5 (Monday): Helix Enters QQQ Again — Rule 2 Boost

QQQ has recovered to $525.00. Helix detects a Class D momentum entry (1H, no divergence required — pure trend continuation). ATRSS is still holding QQQ long from Day 2.

**Stop:** $524.10 - 0.50 x $2.60 = **$522.80** (ATR_1H has compressed slightly)

**Risk per share** = $525.00 - $522.80 = **$2.20**

**Position sizing with Rule 2 boost:**
```
unit1_risk      = $50.00
setup_size_mult = CLASS_D_SIZE_TREND = 0.80

Coordinator: has_atrss_position("QQQ", "LONG")?
  -> ("ATRSS", "QQQ") = LONG, 8 shares — YES
  -> effective_size_mult = 0.80 x 1.25 = 1.00  (25% boost)

qty = ($50 x 1.00) / ($2.20 x 1.0) = 22.7 -> 23 shares
```

Without the boost: qty = ($50 x 0.80) / $2.20 = 18.2 -> 18 shares. **Boost added 5 shares.**

**OMS risk gate:**
```
risk_dollars = 23 x $2.20 = $50.60
new_risk_R   = $50.60 / $50 = 1.012R

6.  Portfolio: 0.605 + 0 + 1.012 = 1.617R > 1.50R  DENIED — portfolio heat cap!
```

23 shares is too many. The OMS rejects. Helix engine receives `RISK_DENIAL`.

On the next signal bar, Helix re-scans and this time the allocator's internal heat check (which also runs before OMS submission) sizes more conservatively. With the portfolio headroom known:

```
Available portfolio heat: 1.50 - 0.605 = 0.895R
Helix ceiling:                            0.85R
Binding constraint: 0.85R  (Helix ceiling is tighter)

Max risk_dollars: 0.85 x $50 = $42.50
Max qty: $42.50 / $2.20 = 19.3 -> 19 shares
```

**Re-armed at 19 shares:**
```
risk_dollars = 19 x $2.20 = $41.80
new_risk_R   = $41.80 / $50 = 0.836R

6.  Portfolio: 0.605 + 0.836 = 1.441R <= 1.50R  PASS
6b. Helix ceiling: 0.836R <= 0.85R               PASS
-> APPROVED
```

Without the Rule 2 boost, qty would have been floor-limited even further: $50 x 0.80 / $2.20 = 18 shares, risk_R = 0.792R. The boost added 1 share.

**Portfolio state:**
| | open_risk_R | open_risk_$ | shares | symbol |
|---|---|---|---|---|
| ATRSS | 0.605 | $60.48 | 8 | QQQ @ $523.50 |
| Helix | 0.836 | $41.80 | 19 | QQQ @ $525.00 |
| **Portfolio** | **1.441** | **$102.28** | | |
| Remaining heat | 0.059R | | | |

Both strategies are now long QQQ simultaneously — 27 shares total, $14,243 notional (142% of equity — margin account required). Total dollar risk is $102.28 (1.02% of equity).

---

## Day 6 (Tuesday): Breakout Signal -> Denied

Breakout detects a compression breakout on QQQ. Score passes, entry ready.

**OMS risk gate:**
```
Breakout QQQ risk_R ~ 0.50R (typical)
Portfolio: 1.441 + 0.50 = 1.941R > 1.50R -> DENIED (heat cap)

Even without portfolio cap:
Breakout ceiling: 0.50R <= 0.65R -> would pass this check
Priority reservation: remaining = 0.059R < 2 x 0.50R = 1.0R
  -> higher-priority ATRSS exists -> DENIED (reservation)
```

Breakout is blocked on two counts. This is correct — with only 0.059R remaining, the portfolio has no room, and what little remains is reserved for ATRSS.

---

## Day 10 (Monday): Helix Exits QQQ at +1.0R

QQQ has rallied to $527.64. Helix's trailing stop tightens and gets hit.

**Helix P&L:**
```
entry = $525.00, exit = $527.64
P&L/share = +$2.64
total P&L = 19 x $2.64 = +$50.16

pnl_R = $50.16 / $50 = +1.003R

With Rule 2 boost (19 shares):  +$50.16
Without boost (18 shares):      18 x $2.64 = +$47.52
Boost added: $2.64 extra profit
```

**Helix cumulative realized_R:** +0.101 (Day 3) + 1.003 (Day 10) = **+1.104R = +$55.20**

---

## Day 14 (Friday): ATRSS Exits QQQ via Multi-Stage TP

ATRSS has been managing the position through its multi-stage exit:

```
Day 6:  QQQ hits +1.0R ($531.06) -> TP1: sell 33% = 3 shares @ $531.06
         remaining: 5 shares
         Stop moved to breakeven: $523.50 + 0.1 x $8 = $524.30

Day 9:  QQQ hits +2.0R ($538.62) -> TP2: sell 33% of remaining = 2 shares @ $538.62
         remaining: 3 shares
         Chandelier trailing stop activated

Day 14: Trailing stop hit at $534.80
         Exit: 3 shares @ $534.80
```

**ATRSS P&L calculation:**
```
Tranche 1: 3 shares x ($531.06 - $523.50) = 3 x $7.56  = +$22.68
Tranche 2: 2 shares x ($538.62 - $523.50) = 2 x $15.12 = +$30.24
Tranche 3: 3 shares x ($534.80 - $523.50) = 3 x $11.30 = +$33.90

Total P&L = $22.68 + $30.24 + $33.90 = +$86.82

ATRSS risk at entry = $60.48
pnl_R = $86.82 / $100 = +0.868R
```

Alternatively expressed: the trade returned 1.44x the dollar risk ($86.82 / $60.48).

---

## Two-Week Summary

**Trade log:**

| # | Day | Strategy | Symbol | Dir | Shares | Entry | Exit | P&L | R |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1-3 | Helix | QQQ | LONG | 12 | $522.20 | $522.62 | +$5.04 | +0.10 |
| 2 | 2-14 | ATRSS | QQQ | LONG | 8 | $523.50 | mixed | +$86.82 | +0.87 |
| 3 | 5-10 | Helix | QQQ | LONG | 19 | $525.00 | $527.64 | +$50.16 | +1.00 |
| | | Breakout | QQQ | — | — | — | denied | — | — |

**Portfolio P&L:**
```
Gross P&L:    +$142.02
As % of NAV:  +1.42%
Total R earned: +1.97R  (0.87 ATRSS-R + 1.10 Helix-R)
```

**Coordination impact:**
```
Rule 1 (stop tighten): Saved $38.64 — trade #1 was +$5 instead of -$34
Rule 2 (size boost):   Added $2.64 — trade #3 had 1 extra share
Net coordination value: +$41.28 over 2 weeks
```

**Risk utilization:**
```
Peak portfolio heat:  1.441R / 1.50R  (96% — near capacity)
Peak dollar risk:     $102.28 / $10,000  (1.02% of equity)
Max drawdown (intraday): -$0 (no losing trades this period)
Strategies blocked:   1 (Breakout, correctly — no room)
```

**Account balance:** $10,000 -> $10,142.02

---

## Key Takeaways for a $10,000 Account

**It's tight.** The 1.5R heat cap means the portfolio can hold roughly $75-$150 at risk depending on the strategy mix. One ATRSS trade + one Helix trade nearly fills the budget. Breakout will almost never fire.

**The R units are not interchangeable.** 1 ATRSS-R = $100 but 1 Helix-R = $50. When ATRSS uses 0.6R, that's $60. When Helix uses 0.8R, that's $40. The portfolio sum (1.4R) represents $100 of actual dollar risk — 1% of the account. This is conservative by design.

**Margin matters.** Both strategies long QQQ simultaneously = 27 shares x ~$525 = $14,175 notional (142% of equity). A Reg-T margin account is required. With a cash account, you'd need to reduce the heat cap or only run one strategy at a time.

**Scaling linearly.** At $50,000 equity, all numbers multiply by 5x: ATRSS-R = $500, Helix-R = $250, positions are 40 shares ATRSS / 95 shares Helix, and the coordination rules produce 5x the dollar impact. The architecture is the same.
