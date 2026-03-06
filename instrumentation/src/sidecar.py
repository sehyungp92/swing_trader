"""Sidecar Forwarder — reads local events and forwards to the central relay.

Handles offline periods, network failures, and duplicate delivery gracefully.
Every payload is HMAC-SHA256 signed with canonicalized JSON (sort_keys=True).
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import logging
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

logger = logging.getLogger("instrumentation.sidecar")

_DIR_TO_EVENT_TYPE = {
    "trades": "trade",
    "missed": "missed_opportunity",
    "errors": "error",
    "scores": "process_quality",
    "daily": "daily_snapshot",
    "post_exit": "post_exit",
    "coordination": "coordinator_action",
}


class Sidecar:
    """Forwards events from local JSONL files to the central relay.

    Usage::

        sidecar = Sidecar(config)
        sidecar.start()       # background thread
        # ...
        sidecar.stop()
    """

    def __init__(self, config: dict):
        self.bot_id = config["bot_id"]
        self.data_dir = Path(config["data_dir"])

        sc = config.get("sidecar", {})
        self.relay_url = os.environ.get("RELAY_URL", "") or sc.get("relay_url", "")
        self.batch_size = sc.get("batch_size", 50)
        self.retry_max = sc.get("retry_max", 5)
        self.retry_backoff_base = sc.get("retry_backoff_base_seconds", 10)
        self.buffer_dir = Path(sc.get("buffer_dir", str(self.data_dir / ".sidecar_buffer")))
        self.buffer_dir.mkdir(parents=True, exist_ok=True)

        hmac_env = sc.get("hmac_secret_env", "INSTRUMENTATION_HMAC_SECRET")
        self.hmac_secret = os.environ.get(hmac_env, "").encode()
        if not self.hmac_secret:
            logger.warning("HMAC secret not set in %s — events will be unsigned", hmac_env)

        self.watermark_file = self.buffer_dir / "watermark.json"
        self.watermarks = self._load_watermarks()

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.poll_interval = sc.get("poll_interval_seconds", 60)

    # --- Watermarks ---

    def _load_watermarks(self) -> Dict[str, int]:
        if self.watermark_file.exists():
            try:
                return json.loads(self.watermark_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_watermarks(self) -> None:
        try:
            self.watermark_file.write_text(json.dumps(self.watermarks, indent=2))
        except OSError as e:
            logger.warning("Failed to save watermarks: %s", e)

    # --- Event collection ---

    def _get_event_files(self) -> List[tuple]:
        files: List[tuple] = []
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
        key = str(filepath)
        last_sent = self.watermarks.get(key, 0)
        events: List[dict] = []
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
                if last_sent == 0:
                    raw = json.loads(filepath.read_text())
                    wrapped = self._wrap_event(raw, event_type)
                    wrapped["_source_file"] = key
                    wrapped["_line_number"] = 1
                    events.append(wrapped)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read %s: %s", filepath, e)
        return events

    def _wrap_event(self, raw_event: dict, event_type: str) -> dict:
        metadata = raw_event.get("event_metadata", {})
        event_id = metadata.get("event_id", "")

        exchange_ts = (
            metadata.get("exchange_timestamp", "")
            or raw_event.get("entry_time", "")
            or raw_event.get("timestamp", "")
            or datetime.now(timezone.utc).isoformat()
        )

        if not event_id:
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
        """HMAC-SHA256 of the canonicalized (sort_keys=True) JSON."""
        if not self.hmac_secret:
            return ""
        return hmac_mod.new(self.hmac_secret, canonical_json.encode(), hashlib.sha256).hexdigest()

    # --- Sending ---

    def _send_batch(self, events: List[dict]) -> bool:
        if not self.relay_url:
            logger.warning("No relay_url configured — skipping send")
            return False
        if requests is None:
            logger.error("requests library not installed — cannot forward events")
            return False

        clean_events = [{k: v for k, v in e.items() if not k.startswith("_")} for e in events]
        envelope = {"bot_id": self.bot_id, "events": clean_events}

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
                    return True  # duplicate, treat as success
                elif response.status_code == 401:
                    logger.error("Authentication failed — check HMAC secret")
                    return False
                elif response.status_code == 429:
                    logger.warning("Rate limited by relay — backing off")
                else:
                    logger.warning("Relay returned %d (attempt %d/%d)",
                                   response.status_code, attempt + 1, self.retry_max)
            except Exception as e:
                logger.warning("Send failed (attempt %d/%d): %s", attempt + 1, self.retry_max, e)

            backoff = self.retry_backoff_base * (2 ** attempt)
            time.sleep(min(backoff, 300))

        return False

    # --- Main loop ---

    def run_once(self) -> None:
        all_files = self._get_event_files()
        total_sent = 0

        for filepath, event_type in all_files:
            unsent = self._read_unsent_events(filepath, event_type)
            if not unsent:
                continue

            for i in range(0, len(unsent), self.batch_size):
                batch = unsent[i:i + self.batch_size]
                if self._send_batch(batch):
                    key = str(filepath)
                    max_line = max(e["_line_number"] for e in batch)
                    self.watermarks[key] = max_line + 1
                    self._save_watermarks()
                    total_sent += len(batch)
                else:
                    logger.warning("Failed to send batch from %s, will retry", filepath)
                    break

        if total_sent > 0:
            logger.info("Forwarded %d events to relay", total_sent)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Sidecar started (poll every %ds)", self.poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _run_loop(self) -> None:
        while self._running:
            try:
                self.run_once()
            except Exception as e:
                logger.error("Sidecar run_once failed: %s", e)
            time.sleep(self.poll_interval)

    def cleanup_old_watermarks(self) -> None:
        to_remove = [key for key in self.watermarks if not Path(key).exists()]
        for key in to_remove:
            del self.watermarks[key]
        if to_remove:
            self._save_watermarks()
