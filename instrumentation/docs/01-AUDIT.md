# Task 1: Audit the Existing Codebase

## Goal

Before writing any instrumentation code, you must understand exactly how this bot works. The audit produces a structured report that informs every subsequent task.

## What to Find

Search the entire codebase and document the following. Be precise — include file paths, class names, function names, and line numbers.

### 1.1 — Entry Logic

Find where the bot decides to enter a trade. Document:

- **File(s) and function(s)** where entry decisions are made
- **Signal generation:** what indicators/conditions trigger an entry signal? List each one.
- **Signal strength:** is there any concept of signal confidence or strength? If yes, where is it computed? If no, note this — we will need to add it.
- **Filter chain:** what filters can block an entry after a signal fires? List each filter with its file/function location.
  - Examples: volume filter, spread filter, time-of-day filter, correlation filter, max position filter
- **Order placement:** which function actually places the order? What exchange API does it call?
- **Entry confirmation:** how does the bot confirm a fill? Polling? Websocket? Callback?

### 1.2 — Exit Logic

Find where the bot decides to exit. Document:

- **Exit triggers:** list every possible exit reason:
  - Take profit (fixed %, ATR-based, trailing?)
  - Stop loss (fixed %, ATR-based, trailing?)
  - Signal-based exit (opposite signal, indicator cross, etc.)
  - Timeout (max hold duration?)
  - Manual / emergency
- **For each trigger:** file, function, and how the target price/condition is calculated
- **Order placement for exits:** same questions as entry

### 1.3 — Position Sizing

- Where is position size calculated?
- What inputs does it use? (account balance, risk %, volatility, etc.)
- Are there risk limits? (max position, max exposure, max drawdown kill switch?)

### 1.4 — Data Sources

- **Price data:** where does the bot get candles/ticks? Exchange websocket? REST polling? Which library?
- **Order book data:** does it use bid/ask/spread? If yes, where?
- **Funding rate:** does it track funding? Where?
- **Open interest:** does it track OI? Where?
- **Other data:** any external data sources (news, sentiment, on-chain, etc.)?

### 1.5 — Existing Logging

- What does the bot currently log? Where? (stdout, file, database, remote service?)
- What format? (plain text, JSON, structured, unstructured?)
- Does it log trade entry/exit? With how much detail?
- Does it log errors? How?
- Does it log signals that were generated but not acted on?

### 1.6 — Configuration

- Where is the bot's configuration stored? (YAML, JSON, env vars, hardcoded?)
- What parameters are configurable? (indicator periods, TP/SL levels, filters, etc.)
- Is there a concept of strategy "profiles" or parameter sets?

### 1.7 — State Management

- How does the bot track open positions? (in-memory, database, file?)
- What happens on restart? Does it recover state?
- Is there a heartbeat or health check mechanism?

### 1.8 — Dependencies

- What Python version?
- Key libraries (ccxt, python-binance, pandas, numpy, ta-lib, etc.)?
- Exchange-specific SDKs?

### 1.9 — Architecture Pattern

Classify the bot's architecture:
- **Event-driven loop:** bot subscribes to market data and reacts
- **Polling loop:** bot periodically checks conditions on a timer
- **Callback-based:** exchange SDK triggers callbacks on events
- **Hybrid:** combination

This matters because it determines WHERE we hook in the instrumentation.

---

## Output

Create `instrumentation/audit_report.md` with the following structure:

```markdown
# Instrumentation Audit Report

## Bot Identity
- Bot ID: (assign a short, unique identifier, e.g., "trend_btc_01")
- Strategy type: (e.g., "ema_cross_trend_follow")
- Exchange(s): (e.g., "Binance Futures")
- Pairs traded: (e.g., ["BTC/USDT", "ETH/USDT"])
- Architecture: (event-driven / polling / callback / hybrid)

## Entry Logic
- Signal generation: [file:line] function_name — description
- Signal strength available: YES / NO (if NO, describe what proxy we can use)
- Filters:
  - Filter 1: [file:line] function_name — what it checks
  - Filter 2: ...
- Order placement: [file:line] function_name

## Exit Logic
- Exit triggers:
  - TAKE_PROFIT: [file:line] — how calculated (fixed % / ATR-based / trailing)
  - STOP_LOSS: [file:line] — how calculated
  - SIGNAL: [file:line] — what triggers it
  - TIMEOUT: [file:line] — duration, or N/A
  - MANUAL: supported? how?

## Position Sizing
- Calculation: [file:line] function_name
- Inputs: [list]
- Risk limits: [list with locations]

## Data Sources
- Price: [source, method, library]
- Bid/Ask: [available? where?]
- Funding: [available? where?]
- OI: [available? where?]

## Existing Logging
- Current format: [text/JSON/structured]
- Current location: [stdout/file/db]
- Trade logging detail level: [none/basic/detailed]
- Error logging: [description]
- Signal logging (including blocked): [YES/NO]

## Configuration
- Config location: [path]
- Configurable params: [list]

## State Management
- Position tracking: [in-memory/db/file]
- Restart recovery: [YES/NO — how]

## Dependencies
- Python: [version]
- Key packages: [list with versions]

## Integration Plan

### Hook Points (where to attach instrumentation)
1. **Pre-entry hook:** [file:line] — wrap this function to capture entry signals
2. **Post-entry hook:** [file:line] — capture fill confirmation
3. **Pre-exit hook:** [file:line] — capture exit decision
4. **Post-exit hook:** [file:line] — capture fill confirmation
5. **Signal generation hook:** [file:line] — capture all signals (including blocked)
6. **Filter hooks:** [one per filter, file:line each]
7. **Error hook:** [file:line] — where exceptions are caught
8. **Main loop hook:** [file:line] — where to attach the snapshot service

### Missing Data (must be added)
- [ ] Signal strength (no current concept)
- [ ] Bid/ask spread at entry/exit
- [ ] Funding rate at entry
- [ ] etc.

### Risks
- [list anything that could break if instrumentation is added wrong]
```

---

## Done Criteria

- [ ] `instrumentation/audit_report.md` exists and is complete
- [ ] Every section has specific file paths and line numbers (not vague descriptions)
- [ ] Integration plan identifies exact hook points
- [ ] Missing data section is honest about what the bot doesn't currently capture
- [ ] Risks section identifies anything fragile
