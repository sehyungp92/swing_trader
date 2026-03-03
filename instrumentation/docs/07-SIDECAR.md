# Task 7: Implement the Sidecar Forwarder

## Goal

Create a lightweight service that reads local event files and forwards them to the central relay VPS. It must handle the bot's PC being offline, network failures, and duplicate delivery gracefully.

**Principles:**
- Log to disk first, forward later (never depend on network for logging)
- Buffer unsent events and retry with exponential backoff
- Every event has a deterministic `event_id` — the relay deduplicates
- Sign every payload with HMAC-SHA256

## Relay API Contract

The relay expects `POST /events` with this format:

```json
{
  "bot_id": "bot_alpha",
  "events": [
    {
      "event_id": "a1b2c3d4e5f6...",
      "bot_id": "bot_alpha",
      "event_type": "trade",
      "payload": "{\"trade_id\": \"abc\", ...}",
      "exchange_timestamp": "2026-03-01T10:00:00+00:00"
    }
  ]
}
```

**Critical details:**
- Each event in the array MUST have: `event_id`, `bot_id`, `event_type`, `payload` (JSON string), `exchange_timestamp`
- The outer envelope has `bot_id` and `events`
- HMAC signature is computed over the **canonicalized** (sorted-keys) JSON body
- Header: `X-Signature: <hmac-sha256-hex>`

The sidecar's job is to read local JSONL event files (which contain full dataclass objects) and wrap each one in the relay envelope format before sending.

## Implementation

```python
# instrumentation/src/sidecar.py

import hashlib
import hmac
import json
import os
import time
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

try:
    import requests
except ImportError:
    requests = None  # graceful degradation if requests not installed


logger = logging.getLogger("sidecar")

# Map data subdirectory names to event types for the relay envelope
_DIR_TO_EVENT_TYPE = {
    "trades": "trade",
    "missed": "missed_opportunity",
    "errors": "error",
    "scores": "process_quality",
    "daily": "daily_snapshot",
}


class Sidecar:
    """
    Forwards events from local JSONL files to the central relay.

    Runs as a background thread or standalone process.

    Usage:
        sidecar = Sidecar(config)
        sidecar.start()  # starts background forwarding thread

    Or standalone:
        sidecar = Sidecar(config)
        sidecar.run_once()  # forward all pending events, then exit
    """

    def __init__(self, config: dict):
        self.bot_id = config["bot_id"]
        self.data_dir = Path(config["data_dir"])

        sidecar_config = config.get("sidecar", {})
        self.relay_url = sidecar_config.get("relay_url", "")
        self.batch_size = sidecar_config.get("batch_size", 50)
        self.retry_max = sidecar_config.get("retry_max", 5)
        self.retry_backoff_base = sidecar_config.get("retry_backoff_base_seconds", 10)
        self.buffer_dir = Path(sidecar_config.get("buffer_dir", str(self.data_dir / ".sidecar_buffer")))
        self.buffer_dir.mkdir(parents=True, exist_ok=True)

        # HMAC secret from environment
        hmac_env = sidecar_config.get("hmac_secret_env", "INSTRUMENTATION_HMAC_SECRET")
        self.hmac_secret = os.environ.get(hmac_env, "").encode()
        if not self.hmac_secret:
            logger.warning("HMAC secret not set in %s — events will be unsigned", hmac_env)

        # Watermark: tracks what's been sent
        self.watermark_file = self.buffer_dir / "watermark.json"
        self.watermarks = self._load_watermarks()

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.poll_interval = sidecar_config.get("poll_interval_seconds", 60)

    # --- Watermark management ---

    def _load_watermarks(self) -> Dict[str, int]:
        """Load watermarks: {filepath: last_sent_line_number}."""
        if self.watermark_file.exists():
            try:
                return json.loads(self.watermark_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_watermarks(self):
        self.watermark_file.write_text(json.dumps(self.watermarks, indent=2))

    # --- Event collection ---

    def _get_event_files(self) -> List[tuple[Path, str]]:
        """Find all JSONL event files that may have unsent events.

        Returns list of (filepath, event_type) tuples.
        """
        files: List[tuple[Path, str]] = []
        for subdir, event_type in _DIR_TO_EVENT_TYPE.items():
            dir_path = self.data_dir / subdir
            if not dir_path.exists():
                continue

            if subdir == "daily":
                for f in sorted(dir_path.glob("daily_*.json")):
                    files.append((f, event_type))
            else:
                for f in sorted(dir_path.glob("*.jsonl")):
                    files.append((f, event_type))

        return files

    def _read_unsent_events(self, filepath: Path, event_type: str) -> List[dict]:
        """Read events from a file that haven't been sent yet.

        Each raw event is wrapped in the relay envelope format:
        {event_id, bot_id, event_type, payload, exchange_timestamp}
        """
        key = str(filepath)
        last_sent = self.watermarks.get(key, 0)

        events = []
        try:
            if filepath.suffix == ".jsonl":
                lines = filepath.read_text().strip().split("\n")
                for i, line in enumerate(lines):
                    if i >= last_sent and line.strip():
                        try:
                            raw = json.loads(line)
                            wrapped = self._wrap_event(raw, event_type)
                            wrapped["_source_file"] = key
                            wrapped["_line_number"] = i
                            events.append(wrapped)
                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning("Skipping bad line %d in %s: %s", i, filepath, e)
            elif filepath.suffix == ".json":
                if last_sent == 0:  # not yet sent
                    raw = json.loads(filepath.read_text())
                    wrapped = self._wrap_event(raw, event_type)
                    wrapped["_source_file"] = key
                    wrapped["_line_number"] = 1
                    events.append(wrapped)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read %s: %s", filepath, e)

        return events

    def _wrap_event(self, raw_event: dict, event_type: str) -> dict:
        """Wrap a local event in the relay envelope format.

        The relay expects each event to have:
          - event_id: deterministic hash
          - bot_id: this bot's ID
          - event_type: "trade", "missed_opportunity", etc.
          - payload: the full event serialized as a JSON string
          - exchange_timestamp: ISO 8601 timestamp

        The event_id and exchange_timestamp are extracted from the event's
        embedded metadata, or generated from available fields.
        """
        # Extract event_id from embedded metadata
        metadata = raw_event.get("event_metadata", {})
        event_id = metadata.get("event_id", "")

        # Extract exchange_timestamp from metadata or top-level fields
        exchange_ts = (
            metadata.get("exchange_timestamp", "")
            or raw_event.get("entry_time", "")
            or raw_event.get("timestamp", "")
            or datetime.now(timezone.utc).isoformat()
        )

        # If no event_id found, generate a deterministic one
        if not event_id:
            # Use bot_id + timestamp + event_type + a distinguishing key
            key = raw_event.get("trade_id", raw_event.get("date", raw_event.get("snapshot_id", "")))
            raw_str = f"{self.bot_id}|{exchange_ts}|{event_type}|{key}"
            event_id = hashlib.sha256(raw_str.encode()).hexdigest()[:16]

        return {
            "event_id": event_id,
            "bot_id": self.bot_id,
            "event_type": event_type,
            "payload": json.dumps(raw_event, default=str),
            "exchange_timestamp": exchange_ts,
        }

    # --- Signing ---

    def _sign_payload(self, canonical_json: str) -> str:
        """HMAC-SHA256 signature of the canonicalized JSON payload.

        CRITICAL: The relay verifies against json.dumps(data, sort_keys=True).
        The input to this method MUST be the sort_keys=True serialization.
        """
        if not self.hmac_secret:
            return ""
        return hmac.new(self.hmac_secret, canonical_json.encode(), hashlib.sha256).hexdigest()

    # --- Sending ---

    def _send_batch(self, events: List[dict]) -> bool:
        """
        Send a batch of events to the relay.
        Returns True if acknowledged.
        """
        if not self.relay_url:
            logger.warning("No relay_url configured — skipping send")
            return False

        if requests is None:
            logger.error("requests library not installed — cannot forward events")
            return False

        # Strip internal metadata before sending
        clean_events = []
        for e in events:
            clean = {k: v for k, v in e.items() if not k.startswith("_")}
            clean_events.append(clean)

        envelope = {
            "bot_id": self.bot_id,
            "events": clean_events,
        }

        # CRITICAL: use sort_keys=True — the relay verifies the signature
        # against the canonicalized (sorted-keys) JSON representation.
        canonical = json.dumps(envelope, sort_keys=True)
        signature = self._sign_payload(canonical)

        headers = {
            "Content-Type": "application/json",
            "X-Bot-ID": self.bot_id,
            "X-Signature": signature,
        }

        for attempt in range(self.retry_max):
            try:
                response = requests.post(
                    self.relay_url,
                    data=canonical.encode(),
                    headers=headers,
                    timeout=30,
                )
                if response.status_code == 200:
                    return True
                elif response.status_code == 409:
                    # Duplicate — already received, treat as success
                    return True
                elif response.status_code == 401:
                    logger.error("Authentication failed — check HMAC secret")
                    return False  # don't retry auth failures
                elif response.status_code == 429:
                    logger.warning("Rate limited by relay — backing off")
                else:
                    logger.warning(
                        "Relay returned %d (attempt %d/%d)",
                        response.status_code, attempt + 1, self.retry_max,
                    )
            except requests.RequestException as e:
                logger.warning("Send failed (attempt %d/%d): %s", attempt + 1, self.retry_max, e)

            # Exponential backoff
            backoff = self.retry_backoff_base * (2 ** attempt)
            time.sleep(min(backoff, 300))  # cap at 5 minutes

        return False

    # --- Main loop ---

    def run_once(self):
        """Collect and forward all unsent events. Call this periodically."""
        all_files = self._get_event_files()
        total_sent = 0

        for filepath, event_type in all_files:
            unsent = self._read_unsent_events(filepath, event_type)
            if not unsent:
                continue

            # Send in batches
            for i in range(0, len(unsent), self.batch_size):
                batch = unsent[i:i + self.batch_size]
                if self._send_batch(batch):
                    # Update watermark to the highest line number sent
                    key = str(filepath)
                    max_line = max(e["_line_number"] for e in batch)
                    self.watermarks[key] = max_line + 1
                    self._save_watermarks()
                    total_sent += len(batch)
                else:
                    # Stop trying this file, retry next cycle
                    logger.warning("Failed to send batch from %s, will retry", filepath)
                    break

        if total_sent > 0:
            logger.info("Forwarded %d events to relay", total_sent)

    def start(self):
        """Start the sidecar as a background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Sidecar started (poll every %ds)", self.poll_interval)

    def stop(self):
        """Stop the background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _run_loop(self):
        while self._running:
            try:
                self.run_once()
            except Exception as e:
                logger.error("Sidecar run_once failed: %s", e)
            time.sleep(self.poll_interval)

    def cleanup_old_watermarks(self):
        """Remove watermarks for files that no longer exist."""
        to_remove = [key for key in self.watermarks if not Path(key).exists()]
        for key in to_remove:
            del self.watermarks[key]
        if to_remove:
            self._save_watermarks()
```

### Integration

Start the sidecar when the bot starts:

```python
# In bot startup:
from instrumentation.src.sidecar import Sidecar

sidecar = Sidecar(config)
sidecar.start()  # runs in background thread

# On shutdown:
sidecar.stop()
```

Or run as a separate process via cron:
```bash
# crontab: every 2 minutes
*/2 * * * * cd /path/to/bot && python -c "
from instrumentation.src.sidecar import Sidecar
import yaml
with open('instrumentation/config/instrumentation_config.yaml') as f:
    config = yaml.safe_load(f)
s = Sidecar(config)
s.run_once()
"
```

### Prerequisites

```bash
pip install requests pyyaml
```

Set the HMAC secret (must match the relay's configured secret for this bot_id):
```bash
export INSTRUMENTATION_HMAC_SECRET="your-secret-here"
```

### Configuration

In `instrumentation/config/instrumentation_config.yaml`, the sidecar section:

```yaml
sidecar:
  relay_url: "https://relay.yourvps.com/events"   # the relay's POST endpoint
  hmac_secret_env: "INSTRUMENTATION_HMAC_SECRET"   # env var name holding the secret
  batch_size: 50                                    # events per HTTP request
  retry_max: 5                                      # max retries per batch
  retry_backoff_base_seconds: 10                    # initial backoff (doubles each retry)
  poll_interval_seconds: 60                         # how often to check for new events
  buffer_dir: "instrumentation/data/.sidecar_buffer"
```

---

## Done Criteria

- [ ] `instrumentation/src/sidecar.py` exists
- [ ] Events are read from all data subdirectories (trades, missed, scores, errors, daily)
- [ ] Events are wrapped in relay envelope format (`event_id`, `bot_id`, `event_type`, `payload`, `exchange_timestamp`)
- [ ] Watermarks track sent events per file — no duplicates on re-run
- [ ] HMAC signatures use `sort_keys=True` canonicalization (matches relay verification)
- [ ] Retry with exponential backoff works (test by pointing to unreachable URL)
- [ ] Auth failures (401) are not retried (avoids hammering relay with bad credentials)
- [ ] Background thread mode works
- [ ] Events actually arrive at the relay (test with a simple HTTP echo server or the real relay)
- [ ] Sidecar failure never affects the bot's trading operation
