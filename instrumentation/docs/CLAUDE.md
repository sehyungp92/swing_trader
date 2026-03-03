# CLAUDE.md — Trading Bot Instrumentation

> **Purpose:** This file tells Claude Code how to instrument this trading bot for the Trading Assistant Agent System. Read this file completely before making any changes.

## Context

This bot is one of several trading bots running on VPSes. We are instrumenting all bots to emit structured event data that feeds into a centralized analysis system. The analysis system uses Claude Code to produce daily/weekly reports, walk-forward optimization, bug triage, and strategy refinement.

**The quality of all downstream analysis depends entirely on the quality of data this bot emits.** Cutting corners here means weeks of debugging bad reports later.

## What You Need to Do

Execute these tasks **in order**. Each task has a dedicated instruction file in `instrumentation/docs/`. Read each file fully before starting that task.

### Task 1: Audit the Existing Codebase

Before writing any code, understand how this bot currently works.

**Read:** `instrumentation/docs/01-AUDIT.md`

Produce: `instrumentation/audit_report.md` — a structured summary of what exists, what's missing, and where to hook in.

### Task 2: Implement Market Snapshot Service

A lightweight service that captures and stores market state at regular intervals and on-demand for trade events.

**Read:** `instrumentation/docs/02-MARKET-SNAPSHOTS.md`

### Task 3: Implement Trade Event Logger

Wrap existing entry/exit logic to capture structured trade events with full context: why the trade was entered, why it was exited, what the market looked like, and what filters were active.

**Read:** `instrumentation/docs/03-TRADE-LOGGER.md`

### Task 4: Implement Missed Opportunity Logger

Log every signal that fired but was blocked by a filter or risk limit, including hypothetical outcome backfill.

**Read:** `instrumentation/docs/04-MISSED-OPPORTUNITIES.md`

### Task 5: Implement Process Quality Scorer

A deterministic rules engine that scores every trade's process quality (independent of PnL) and tags root causes from a controlled taxonomy.

**Read:** `instrumentation/docs/05-PROCESS-SCORER.md`

### Task 6: Implement Daily Aggregate Snapshots

End-of-day rollup computed locally on the VPS.

**Read:** `instrumentation/docs/06-DAILY-SNAPSHOTS.md`

### Task 7: Implement the Sidecar Forwarder

A lightweight service that reads local event files and forwards them to the central relay with signing, buffering, and retry logic.

**Read:** `instrumentation/docs/07-SIDECAR.md`

### Task 8: Add Regime Classifier

A simple, deterministic market regime classifier that tags each trade and snapshot.

**Read:** `instrumentation/docs/08-REGIME-CLASSIFIER.md`

### Task 9: Write Tests

Unit tests for every new component. Integration test that simulates a full trade lifecycle and verifies all events are emitted correctly.

**Read:** `instrumentation/docs/09-TESTS.md`

### Task 10: Validation

Run the full validation checklist to confirm everything works end-to-end.

**Read:** `instrumentation/docs/10-VALIDATION.md`

### Task 11: Deploy the Relay Service (Conditional)

> **Only perform this task if this bot's VPS has been designated as the relay host.** If not, skip it — the relay will be deployed on a different VPS.

Deploy and configure the central relay service on this VPS. The relay buffers events from ALL bots and serves them to the home orchestrator.

**Read:** `instrumentation/docs/11-RELAY-DEPLOYMENT.md`

---

## Critical Rules

1. **Do not change trading logic.** You are adding instrumentation only. No changes to entry signals, exit logic, position sizing, risk management, or filter behavior. If you need to wrap a function to capture data, the wrapper must be transparent — same inputs, same outputs, same side effects.

2. **Do not break existing functionality.** Every instrumentation addition must be fault-tolerant. If the logger fails, the trade must still execute. Use try/except around all instrumentation code with fallback to a degraded log entry.

3. **All timestamps must include both exchange time and local time.** See the EventMetadata schema.

4. **All events must have deterministic event_ids.** See the schema — this prevents duplicate processing downstream.

5. **All new files go in the `instrumentation/` directory** unless they are modifications to existing files. Keep the instrumentation layer cleanly separated.

6. **Use JSONL format** for all event output files. One JSON object per line. One file per day per event type.

7. **Log to disk first, forward later.** The sidecar reads completed files. Never depend on network availability for logging.

---

## File Structure After Instrumentation

```
<bot_root>/
  instrumentation/
    docs/                          # instruction files (already present)
    audit_report.md                # your audit output (Task 1)
    config/
      instrumentation_config.yaml  # all instrumentation settings
      simulation_policies.yaml     # missed opportunity simulation assumptions
      regime_classifier_config.yaml
      process_scoring_rules.yaml
    src/
      event_metadata.py            # EventMetadata + event_id generation
      market_snapshot.py           # MarketSnapshot service
      trade_logger.py              # TradeEvent logger
      missed_opportunity.py        # MissedOpportunity logger + backfiller
      process_scorer.py            # deterministic quality scorer
      daily_snapshot.py            # end-of-day aggregator
      regime_classifier.py         # market regime tagger
      sidecar.py                   # forwarder to relay
    data/
      snapshots/                   # market snapshots (JSONL, rotated daily)
      trades/                      # trade events (JSONL)
      missed/                      # missed opportunity events (JSONL)
      daily/                       # daily aggregate snapshots (JSON)
      errors/                      # error events (JSONL)
    tests/
      test_event_metadata.py
      test_market_snapshot.py
      test_trade_logger.py
      test_missed_opportunity.py
      test_process_scorer.py
      test_daily_snapshot.py
      test_regime_classifier.py
      test_sidecar.py
      test_integration.py          # full lifecycle test
```

---

## Configuration

All instrumentation settings live in `instrumentation/config/instrumentation_config.yaml`. Claude Code: create this file during Task 2 and extend it as you work through subsequent tasks.

```yaml
# instrumentation/config/instrumentation_config.yaml

bot_id: "BOT_ID_PLACEHOLDER"          # unique identifier for this bot
bot_name: "BOT_NAME_PLACEHOLDER"       # human-readable name
strategy_type: "STRATEGY_PLACEHOLDER"  # e.g. "trend_follow", "mean_reversion"

data_dir: "instrumentation/data"
rotation:
  max_file_age_days: 30               # delete local files older than this
  max_disk_mb: 500                    # max disk usage for instrumentation data

market_snapshots:
  interval_seconds: 60                 # store a snapshot every N seconds
  symbols: []                          # auto-populated from bot's active symbols

sidecar:
  relay_url: "https://RELAY_PLACEHOLDER/events"
  hmac_secret_env: "INSTRUMENTATION_HMAC_SECRET"  # read from env var
  batch_size: 50
  retry_max: 5
  retry_backoff_base_seconds: 10
  buffer_dir: "instrumentation/data/.sidecar_buffer"

logging:
  level: "INFO"
  file: "instrumentation/data/instrumentation.log"
  max_size_mb: 50
  backup_count: 3
```

---

## How to Read This as Claude Code

1. Start with Task 1 (audit). This tells you where to hook into the existing code.
2. Work through Tasks 2–8 sequentially. Each builds on the previous.
3. Task 9 (tests) can be written incrementally as you go.
4. Task 10 (validation) is your final check.
5. Task 11 (relay deployment) is conditional — only if this VPS is the relay host.

Each task doc follows the same structure:
- **Goal:** what this component does and why
- **Schema:** exact data structures with types
- **Implementation:** step-by-step instructions with code patterns
- **Integration points:** where and how to hook into the existing bot
- **Done criteria:** how to verify it works

## Critical Protocol Detail: HMAC Signing

The relay verifies HMAC signatures against **canonicalized JSON** (`json.dumps(data, sort_keys=True)`). The sidecar MUST use `sort_keys=True` when serializing the request body before signing. See `07-SIDECAR.md` for the exact implementation. A mismatch here causes silent 401 rejections.
