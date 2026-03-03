# Strategy Combination Analysis

## Complementarity Analysis

### Trade Volume & Performance (fixed_qty=10, all symbols)

| Strategy | Trades | Avg R | Total R | Net $ |
|----------|--------|-------|---------|-------|
| ATRSS | 262 | +0.691 | +181.2 | +$3,534 |
| Helix | 606 | +0.303 | +183.8 | +$5,249 |
| Breakout | 51 | +0.166 | +8.5 | +$1,373 |
| **Combined** | **919** | | **+373.5** | **+$10,156** |

### Entry Overlap: Very Low

- ATRSS vs Helix: **31 same-day entries out of 685 unique** (4.5%)
- ATRSS vs Breakout: **4 out of 175** (2.3%)
- Helix vs Breakout: **7 out of 632** (1.1%)
- All three on same day: **0**

### Monthly R Correlation: Low (Complementary)

- ATRSS vs Helix: **r = +0.067** (essentially uncorrelated)
- ATRSS vs Breakout: r = +0.351 (moderate)
- Helix vs Breakout: r = +0.243 (low)

### Holding Periods

- **ATRSS**: mean 346h (14.4 days) -- swing trades
- **Helix**: mean 40h (1.7 days) -- short-term
- **Breakout**: mean 129h (5.4 days) -- medium-term

### Market Coverage

- **ATRSS**: 91% long / 9% short -- primarily trend follower
- **Helix**: 64% long / 36% short -- bidirectional
- **Breakout**: 69% long / 31% short -- bidirectional

### Year-by-Year: They Cover Each Other's Weak Periods

| Year | ATRSS R (n) | Helix R (n) | Breakout R (n) | Combined |
|------|-------------|-------------|----------------|----------|
| 2021 | +6.0 (35) | +19.7 (78) | +0.3 (6) | +26.0 |
| 2022 | +26.1 (29) | +57.8 (125) | +1.7 (11) | +85.6 |
| 2023 | +38.5 (55) | +25.1 (113) | +1.9 (9) | +65.5 |
| 2024 | +45.9 (71) | +57.0 (141) | +2.2 (12) | +105.1 |
| 2025 | +55.9 (69) | +15.5 (137) | +1.7 (12) | +73.2 |
| 2026 | +8.7 (3) | +8.8 (12) | +0.7 (1) | +18.2 |

### Verdict: Strongly Complementary

1. **Low entry overlap** -- almost never signal the same trade on the same day (4.5% max)
2. **Uncorrelated monthly returns** -- ATRSS vs Helix r=0.067, meaning one profits when the other doesn't
3. **Different timeframe niches** -- ATRSS holds 2 weeks, Helix holds 1.7 days, Breakout holds 5 days
4. **Different directional exposure** -- ATRSS is 91% long; Helix/Breakout add short coverage
5. **Year-by-year balance** -- they take turns carrying the portfolio (Helix in 2022, ATRSS in 2025)
6. **Breakout is marginal** -- only 51 trades at +0.166 avg R. Adds +$1,373 but barely justifies complexity. ATRSS and Helix together are the core portfolio (+$8,783, 868 trades, near-zero correlation).

---

## Sequencing & Interaction Patterns

### 1. ATRSS as Regime Confirmation for Helix (strongest finding)

Helix performs significantly better when ATRSS has a concurrent position in the same symbol:

| Condition | Helix n | Avg R | WR | Total R |
|-----------|---------|-------|-----|---------|
| With concurrent ATRSS | 304 | **+0.443** | 40% | +134.5 |
| Without concurrent ATRSS | 302 | +0.163 | 34% | +49.3 |
| Same direction confirmed | 297 | **+0.450** | 40% | +133.6 |
| Opposite direction (contra) | 7 | +0.138 | 43% | +1.0 |

USO is the most dramatic: Helix with ATRSS open = +0.376 avg R (47% WR); without = -0.014 (29% WR).

### 2. ATRSS Performs Terribly Without Concurrent Helix

| Condition | ATRSS n | Avg R | WR | Total R |
|-----------|---------|-------|-----|---------|
| With concurrent Helix | 204 | **+0.927** | **73%** | +189.2 |
| Without concurrent Helix | 58 | **-0.138** | 41% | -8.0 |

Not causal -- both strategies fire when market conditions are good. ATRSS alone during quiet periods tends to fail.

### 3. Helix Trades Just Before ATRSS Entry Are Losers

Helix trades in the 1-5 days before an ATRSS entry have negative R:

| Window Before | Helix Avg R | Baseline |
|---------------|-------------|----------|
| 1 day | -0.035 | +0.303 |
| 3 days | -0.181 | +0.303 |
| 5 days | -0.029 | +0.303 |

The market chop preceding an ATRSS pullback-to-EMA entry tends to stop out Helix's short-term momentum trades.

### 4. Both Strategies Perform Best Solo

| Condition | Avg R |
|-----------|-------|
| Helix after recent ATRSS close | +0.210 to +0.231 |
| Helix with no recent ATRSS | **+0.331** |
| ATRSS with no recent Helix | **+0.800** |

Clusters happen during volatile, choppy transitions where signals are noisier.

### 5. Dry Spell Coverage: Helix Fills ATRSS Gaps

During ATRSS dry spells (>14 days with no position):

| Symbol | Gap Days | Helix Trades | Helix R |
|--------|----------|-------------|---------|
| QQQ | 686 | 61 | **+31.5** |
| GLD | 1,108 | 106 | **+24.6** |
| USO | 1,095 | 91 | +7.6 |
| IBIT | 195 | 21 | -3.6 |
| **Total** | **3,084** | **279** | **+60.1** |

Helix earns +60.1R during ATRSS idle periods. Genuine complementarity, though IBIT is slightly negative during gaps.

### 6. Breakout as Leading Indicator: Not Confirmed

Helix within 7d after a Breakout entry: avg R = +0.070 (vs +0.303 baseline, -0.233 delta). n=47. Breakout entries do not predict favorable Helix conditions.

---

## Deep Dive: Pre-ATRSS Helix Losers

### Profile of the Pre-ATRSS Helix Trades

Helix trades entering 1-5 days before an ATRSS entry on the same symbol:

| Cohort | n | Avg R | WR | Total R |
|--------|---|-------|-----|---------|
| PRE-ATRSS (1-5d before) | 63 | **-0.026** | **22%** | -1.6 |
| All other Helix | 543 | +0.342 | 39% | +185.5 |

The effect is strongest in LONG trades and BULL regimes. SHORT pre-ATRSS trades are actually positive:

| Breakdown | PRE-ATRSS Avg R | Other Avg R |
|-----------|-----------------|-------------|
| Class D | +0.026 | +0.373 |
| Class A | -0.030 | +0.311 |
| LONG | **-0.183** | +0.395 |
| SHORT | +0.436 | +0.251 |
| 1H origin | -0.035 | +0.353 |
| 4H origin | -0.009 | +0.293 |
| BULL regime | **-0.183** | +0.413 |

USO is worst: PRE-ATRSS Helix avg R = -0.313 (20% WR) vs +0.146 (36% WR) otherwise.

### Why "Skip After Consecutive Losses" Does NOT Work

Simulating skipping Helix trades after 2 consecutive losses **hurts** -- the skipped trades average positive R:

| Symbol | Skipped n | Skipped Avg R | R Lost by Skipping |
|--------|-----------|---------------|--------------------|
| QQQ | 65 | +0.432 | -28.1 |
| GLD | 79 | +0.342 | -27.0 |
| IBIT | 25 | +0.958 | -23.9 |
| USO | 65 | +0.103 | -6.7 |

Consecutive Helix losses do not predict the next trade is bad. The rebound trade after a streak is often a winner.

### Why "Helix Loss Predicts ATRSS Entry" Is Too Weak

Within 1 day of a Helix loss, ATRSS enters 4.7% of the time vs 1.3% after a Helix win -- a 3.6x ratio. But 4.7% is too infrequent for a reliable trigger. By 7 days the signal disappears (17.1% vs 16.4%).

Consecutive Helix loss streaks are also poor predictors: only 4-17% of 2+ loss streaks are followed by an ATRSS entry within 5 days.

### What IS Actionable: ATRSS Entry as Helix Stop Trigger

When ATRSS enters while Helix already has an open position on the same symbol:

| Helix Outcome | n | Avg R | WR |
|---------------|---|-------|-----|
| Eventually WON | 18 | +1.409 | -- |
| Eventually LOST | **32** | **-0.242** | **0%** |

**64% of these Helix trades end as losers, all with 0% WR.** The 18 winners are big (+1.409 avg R) so you can't blindly flatten.

Timing matters -- early ATRSS signal catches deeper losers:

| ATRSS Entry Timing | Helix Losers n | Avg R | WR |
|---------------------|----------------|-------|-----|
| First half of Helix trade | 20 | **-0.282** | 0% |
| Second half | 12 | -0.176 | 0% |

All 32 losers were in the same direction as ATRSS (no opposite-direction losers).

**Proposed mechanism: When ATRSS enters on the same symbol while Helix is in a position, tighten the Helix stop to breakeven.**

- The 32 losers would get capped at ~0R instead of averaging -0.242R, saving ~8R
- The 18 winners still run because a BE stop doesn't flatten profitable positions
- No trades are skipped -- only the stop level changes on positions already in trouble
- This is an observable cross-strategy event, not a prediction

### What Does NOT Work

- **Helix stop-out as ATRSS booster**: ATRSS after a Helix stop-out performs worse than baseline (+0.284 vs +0.734 avg R), especially USO (-0.403). Don't boost ATRSS size after Helix losses.
- **Direction-based filtering**: PRE-ATRSS Helix trades in the same direction as the upcoming ATRSS (-0.030 avg R) and opposite direction (+0.004 avg R) are both near zero. The pullback chop primarily kills LONG momentum trades (LONG PRE-ATRSS avg R = -0.183).

---

## Practical Implications

1. **Tighten Helix stop to BE when ATRSS enters same symbol** -- saves ~8R by capping 32 losers at 0R while preserving 18 big winners. Implementable as a cross-strategy event at the portfolio level. Modest but free improvement.

2. **Consider a Helix confidence boost when ATRSS is active** -- Helix performs 2.7x better with a concurrent ATRSS position (+0.443 vs +0.163 avg R). Could increase Helix position size or lower its entry threshold when ATRSS confirms.

3. **USO Helix should possibly require ATRSS confirmation** -- Helix solo on USO is -0.014 avg R (break-even). Only trading Helix on USO when ATRSS is also positioned would cut USO Helix from 158 to ~53 trades but shift avg R from -0.014 to +0.376.

4. **Do NOT skip Helix trades after consecutive losses** -- backtested and proven harmful. The rebound trades are often winners.

5. **Expect Helix whipsaws before ATRSS entries** -- the 1-5 day pre-ATRSS Helix drawdown is a consistent pattern, concentrated in LONG trades during BULL regimes. SHORT pre-ATRSS trades are not affected. The market chop preceding pullback-to-EMA bounces kills LONG Helix momentum trades.

6. **Helix is the gap-filler** -- it generates +60R during ATRSS dry spells, confirming its role as the "always on" strategy that keeps the portfolio active between ATRSS's less frequent swing trades. IBIT is the exception (-3.6R during gaps).
