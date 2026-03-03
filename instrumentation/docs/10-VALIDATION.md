# Task 10: Validation

## Goal

Verify that the complete instrumentation layer works end-to-end before deploying to the live bot. This is a manual + automated validation checklist.

## Prerequisites

- All previous tasks (1–9) are complete
- Tests pass: `pytest instrumentation/tests/ -v`
- Bot can run in a test/paper-trading mode (if available)

## Validation Checklist

### A. File Structure Verification

Run this from the bot root:

```bash
#!/bin/bash
# instrumentation/validate_structure.sh

echo "=== Validating instrumentation file structure ==="
FAIL=0

check() {
    if [ ! -e "$1" ]; then
        echo "MISSING: $1"
        FAIL=1
    else
        echo "OK: $1"
    fi
}

# Config files
check "instrumentation/config/instrumentation_config.yaml"
check "instrumentation/config/simulation_policies.yaml"
check "instrumentation/config/regime_classifier_config.yaml"
check "instrumentation/config/process_scoring_rules.yaml"

# Source files
check "instrumentation/src/event_metadata.py"
check "instrumentation/src/market_snapshot.py"
check "instrumentation/src/trade_logger.py"
check "instrumentation/src/missed_opportunity.py"
check "instrumentation/src/process_scorer.py"
check "instrumentation/src/daily_snapshot.py"
check "instrumentation/src/regime_classifier.py"
check "instrumentation/src/sidecar.py"

# Tests
check "instrumentation/tests/test_event_metadata.py"
check "instrumentation/tests/test_market_snapshot.py"
check "instrumentation/tests/test_trade_logger.py"
check "instrumentation/tests/test_missed_opportunity.py"
check "instrumentation/tests/test_process_scorer.py"
check "instrumentation/tests/test_integration.py"

# Data directories
check "instrumentation/data"

# Audit report
check "instrumentation/audit_report.md"

if [ $FAIL -eq 0 ]; then
    echo ""
    echo "=== All files present ==="
else
    echo ""
    echo "=== VALIDATION FAILED: missing files ==="
    exit 1
fi
```

### B. Configuration Validation

Check that all config files have been adapted (no placeholder values remaining):

```bash
#!/bin/bash
# Check for unfilled placeholders
echo "=== Checking for placeholder values ==="
grep -rn "PLACEHOLDER" instrumentation/config/ && echo "FAIL: Found placeholder values" || echo "OK: No placeholders found"
grep -rn "ADAPT" instrumentation/src/ && echo "WARNING: Found ADAPT comments — verify these have been customized" || echo "OK: No ADAPT comments found"
```

### C. Live Data Validation

Start the bot (paper trading or live) and let it run for 10–15 minutes. Then check:

```bash
#!/bin/bash
echo "=== Checking live data output ==="

DATA_DIR="instrumentation/data"
TODAY=$(date -u +%Y-%m-%d)

# Market snapshots should exist
SNAP_FILE="${DATA_DIR}/snapshots/snapshots_${TODAY}.jsonl"
if [ -f "$SNAP_FILE" ]; then
    SNAP_COUNT=$(wc -l < "$SNAP_FILE")
    echo "OK: ${SNAP_COUNT} market snapshots captured"

    # Validate JSON format
    python3 -c "
import json
with open('$SNAP_FILE') as f:
    for i, line in enumerate(f):
        data = json.loads(line.strip())
        assert 'snapshot_id' in data, f'Line {i}: missing snapshot_id'
        assert 'symbol' in data, f'Line {i}: missing symbol'
        assert 'bid' in data, f'Line {i}: missing bid'
        assert 'timestamp' in data, f'Line {i}: missing timestamp'
        assert data['bid'] > 0, f'Line {i}: bid is zero (degraded snapshot?)'
print('OK: All snapshots valid JSON with required fields')
"
else
    echo "WARNING: No snapshot file yet — wait for first capture interval"
fi

# Check for trades (may not exist if no trades occurred)
TRADE_FILE="${DATA_DIR}/trades/trades_${TODAY}.jsonl"
if [ -f "$TRADE_FILE" ]; then
    TRADE_COUNT=$(wc -l < "$TRADE_FILE")
    echo "OK: ${TRADE_COUNT} trade events recorded"

    python3 -c "
import json
with open('$TRADE_FILE') as f:
    for i, line in enumerate(f):
        data = json.loads(line.strip())
        assert 'trade_id' in data, f'Line {i}: missing trade_id'
        assert 'event_metadata' in data, f'Line {i}: missing event_metadata'
        assert 'entry_signal' in data, f'Line {i}: missing entry_signal'
        stage = data.get('stage', '')
        if stage == 'entry':
            assert data.get('entry_price', 0) > 0, f'Line {i}: entry_price is zero'
            assert data.get('entry_signal', ''), f'Line {i}: entry_signal is empty'
        elif stage == 'exit':
            assert data.get('exit_price', 0) > 0, f'Line {i}: exit_price is zero'
            assert data.get('exit_reason', ''), f'Line {i}: exit_reason is empty'
            assert data.get('pnl') is not None, f'Line {i}: pnl is None'
print('OK: All trade events valid')
"
else
    echo "INFO: No trades yet — this is normal if no signals fired"
fi

# Check for missed opportunities
MISSED_FILE="${DATA_DIR}/missed/missed_${TODAY}.jsonl"
if [ -f "$MISSED_FILE" ]; then
    MISSED_COUNT=$(wc -l < "$MISSED_FILE")
    echo "OK: ${MISSED_COUNT} missed opportunities logged"

    python3 -c "
import json
with open('$MISSED_FILE') as f:
    for i, line in enumerate(f):
        data = json.loads(line.strip())
        assert 'blocked_by' in data, f'Line {i}: missing blocked_by'
        assert 'simulation_policy' in data, f'Line {i}: missing simulation_policy'
        assert 'assumption_tags' in data, f'Line {i}: missing assumption_tags'
        assert len(data['assumption_tags']) > 0, f'Line {i}: empty assumption_tags'
print('OK: All missed opportunity events valid')
"
else
    echo "INFO: No missed opportunities yet — this is normal if no signals were blocked"
fi

# Check for instrumentation errors
ERROR_FILE="${DATA_DIR}/errors/instrumentation_errors_${TODAY}.jsonl"
if [ -f "$ERROR_FILE" ]; then
    ERROR_COUNT=$(wc -l < "$ERROR_FILE")
    echo "WARNING: ${ERROR_COUNT} instrumentation errors — review these"
    head -5 "$ERROR_FILE"
else
    echo "OK: No instrumentation errors"
fi

echo ""
echo "=== Live data validation complete ==="
```

### D. Fault Tolerance Validation

Verify that instrumentation failure does NOT affect trading:

1. **Break the snapshot service temporarily:**
   - Modify `MarketSnapshotService.capture_now` to raise an exception
   - Run the bot — confirm trades still execute
   - Confirm degraded snapshots are logged
   - Restore the service

2. **Break the trade logger temporarily:**
   - Modify `TradeLogger.log_entry` to raise an exception
   - Run the bot — confirm trades still execute
   - Confirm errors are logged to `instrumentation/data/errors/`
   - Restore the logger

3. **Disconnect the sidecar:**
   - Set `relay_url` to an unreachable address
   - Run the bot — confirm local logging works
   - Confirm sidecar retries and buffers events
   - Restore the URL and confirm buffered events are sent

### E. Event ID Idempotency Validation

```python
# Run this script to verify idempotency:
import json
from collections import Counter
from pathlib import Path

data_dir = Path("instrumentation/data")

for subdir in ["trades", "missed", "snapshots"]:
    dir_path = data_dir / subdir
    if not dir_path.exists():
        continue

    all_ids = []
    for f in dir_path.glob("*.jsonl"):
        for line in f.read_text().strip().split("\n"):
            if line.strip():
                data = json.loads(line)
                eid = data.get("event_metadata", {}).get("event_id") or data.get("snapshot_id")
                if eid:
                    all_ids.append(eid)

    dupes = {k: v for k, v in Counter(all_ids).items() if v > 1}
    if dupes:
        print(f"WARNING: {subdir} has duplicate event_ids: {dupes}")
    else:
        print(f"OK: {subdir} — {len(all_ids)} events, all unique IDs")
```

### F. Regime Classifier Validation

```python
# Verify regime classifier produces reasonable results
from instrumentation.src.regime_classifier import RegimeClassifier

classifier = RegimeClassifier(
    config_path="instrumentation/config/regime_classifier_config.yaml",
    data_provider=bot.exchange  # or your data provider
)

for symbol in bot.active_symbols:
    regime = classifier.classify(symbol)
    print(f"{symbol}: {regime}")
    assert regime in ["trending_up", "trending_down", "ranging", "volatile", "unknown"]
```

### G. Final Checklist

Before deploying to production, confirm:

- [ ] `bash instrumentation/validate_structure.sh` passes
- [ ] No PLACEHOLDER values in configs
- [ ] `pytest instrumentation/tests/ -v` — all tests pass
- [ ] Bot ran for 15+ minutes with instrumentation active
- [ ] Market snapshots are being captured at the configured interval
- [ ] At least one trade event logged correctly (if trades occurred)
- [ ] At least one missed opportunity logged correctly (if signals were blocked)
- [ ] Process quality scores are computed for completed trades
- [ ] Regime classifier returns valid regimes
- [ ] Event IDs are unique (no duplicates)
- [ ] Fault tolerance verified: broken logger does not crash bot
- [ ] Sidecar buffers events when relay is unreachable
- [ ] Daily snapshot builder produces correct aggregates
- [ ] No original trading logic was modified (only wrappers added)
- [ ] All `# ADAPT` comments in source code have been customized

---

## Deployment

Once validation passes:

1. Commit all instrumentation code to the bot's repository
2. Set the `INSTRUMENTATION_HMAC_SECRET` environment variable on the VPS
3. Update `instrumentation_config.yaml` with production `relay_url`
4. Deploy and monitor `instrumentation/data/errors/` for the first 24 hours
5. Verify events arrive at the relay VPS

The instrumentation layer is now complete. The central analysis system can begin consuming this bot's data.
