"""Tests for the relay service."""
import hashlib
import hmac
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from relay.app import create_relay_app
from relay.auth import HMACAuth
from relay.db.store import EventStore
from relay.rate_limiter import RateLimiter


# --- EventStore tests ---

class TestEventStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmpdir) / "test.db")
        self.store = EventStore(db_path=self.db_path)

    def test_insert_and_retrieve(self):
        events = [
            {"event_id": "e1", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
            {"event_id": "e2", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
        ]
        result = self.store.insert_events(events)
        assert result["accepted"] == 2
        assert result["duplicates"] == 0

        fetched = self.store.get_events()
        assert len(fetched) == 2

    def test_duplicate_rejection(self):
        events = [{"event_id": "e1", "bot_id": "bot1", "event_type": "trade", "payload": "{}"}]
        self.store.insert_events(events)
        result = self.store.insert_events(events)
        assert result["accepted"] == 0
        assert result["duplicates"] == 1

    def test_ack_removes_from_pending(self):
        self.store.insert_events([
            {"event_id": "e1", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
            {"event_id": "e2", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
        ])
        count = self.store.ack_up_to("e1")
        assert count == 1
        pending = self.store.get_events()
        assert len(pending) == 1
        assert pending[0]["event_id"] == "e2"

    def test_get_events_with_since(self):
        self.store.insert_events([
            {"event_id": "e1", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
            {"event_id": "e2", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
            {"event_id": "e3", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
        ])
        events = self.store.get_events(since="e1")
        assert len(events) == 2
        assert events[0]["event_id"] == "e2"

    def test_count_pending(self):
        self.store.insert_events([
            {"event_id": "e1", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
        ])
        assert self.store.count_pending() == 1
        self.store.ack_up_to("e1")
        assert self.store.count_pending() == 0

    def test_filter_by_bot_id(self):
        self.store.insert_events([
            {"event_id": "e1", "bot_id": "bot1", "event_type": "trade", "payload": "{}"},
            {"event_id": "e2", "bot_id": "bot2", "event_type": "trade", "payload": "{}"},
        ])
        events = self.store.get_events(bot_id="bot1")
        assert len(events) == 1
        assert events[0]["bot_id"] == "bot1"


# --- HMACAuth tests ---

class TestHMACAuth:
    def test_disabled_when_no_secrets(self):
        auth = HMACAuth()
        assert not auth.enabled
        assert auth.verify(b"anything", "bad", "bot1") is True

    def test_valid_signature(self):
        secret = "test-secret"
        auth = HMACAuth({"bot1": secret})
        body = json.dumps({"test": True}, sort_keys=True).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert auth.verify(body, sig, "bot1") is True

    def test_invalid_signature(self):
        auth = HMACAuth({"bot1": "real-secret"})
        body = b'{"test": true}'
        assert auth.verify(body, "badsignature", "bot1") is False

    def test_unknown_bot_id(self):
        auth = HMACAuth({"bot1": "secret"})
        assert auth.verify(b"body", "sig", "unknown_bot") is False


# --- RateLimiter tests ---

class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert limiter.is_allowed("bot1") is True

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        assert limiter.is_allowed("bot1") is True
        assert limiter.is_allowed("bot1") is True
        assert limiter.is_allowed("bot1") is False

    def test_separate_per_bot(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.is_allowed("bot1") is True
        assert limiter.is_allowed("bot2") is True
        assert limiter.is_allowed("bot1") is False

    def test_remaining(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        assert limiter.remaining("bot1") == 5
        limiter.is_allowed("bot1")
        assert limiter.remaining("bot1") == 4


# --- FastAPI integration tests ---

class TestRelayAPI:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmpdir) / "test.db")
        self.secret = "test-secret-key"
        app = create_relay_app(
            db_path=self.db_path,
            shared_secrets={"test_bot": self.secret},
        )
        self.client = TestClient(app)

    def _sign_and_post(self, payload: dict) -> "TestClient":
        body = json.dumps(payload, sort_keys=True)
        sig = hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        return self.client.post(
            "/events",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )

    def test_health(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ingest_with_valid_signature(self):
        payload = {
            "bot_id": "test_bot",
            "events": [{
                "event_id": "evt-001",
                "bot_id": "test_bot",
                "event_type": "trade",
                "payload": "{}",
                "exchange_timestamp": "2026-03-02T00:00:00Z",
            }],
        }
        resp = self._sign_and_post(payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 1
        assert data["duplicates"] == 0

    def test_ingest_rejects_bad_signature(self):
        payload = {
            "bot_id": "test_bot",
            "events": [],
        }
        resp = self.client.post(
            "/events",
            json=payload,
            headers={"X-Signature": "badsig"},
        )
        assert resp.status_code == 401

    def test_ingest_duplicate_rejected(self):
        payload = {
            "bot_id": "test_bot",
            "events": [{
                "event_id": "evt-dup",
                "bot_id": "test_bot",
                "event_type": "trade",
                "payload": "{}",
            }],
        }
        self._sign_and_post(payload)
        resp = self._sign_and_post(payload)
        assert resp.status_code == 200
        assert resp.json()["duplicates"] == 1

    def test_get_events(self):
        payload = {
            "bot_id": "test_bot",
            "events": [{
                "event_id": "evt-get-1",
                "bot_id": "test_bot",
                "event_type": "trade",
                "payload": "{}",
            }],
        }
        self._sign_and_post(payload)
        resp = self.client.get("/events")
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 1
        assert events[0]["event_id"] == "evt-get-1"

    def test_get_events_with_since(self):
        for i in range(3):
            payload = {
                "bot_id": "test_bot",
                "events": [{
                    "event_id": f"evt-since-{i}",
                    "bot_id": "test_bot",
                    "event_type": "trade",
                    "payload": "{}",
                }],
            }
            self._sign_and_post(payload)

        resp = self.client.get("/events?since=evt-since-0")
        events = resp.json()["events"]
        assert len(events) == 2
        assert events[0]["event_id"] == "evt-since-1"

    def test_ack_events(self):
        payload = {
            "bot_id": "test_bot",
            "events": [
                {"event_id": "evt-ack-1", "bot_id": "test_bot", "event_type": "trade", "payload": "{}"},
                {"event_id": "evt-ack-2", "bot_id": "test_bot", "event_type": "trade", "payload": "{}"},
            ],
        }
        self._sign_and_post(payload)

        resp = self.client.post("/ack", json={"watermark": "evt-ack-1"})
        assert resp.status_code == 200
        assert resp.json()["acked_count"] == 1

        # Verify only unacked events remain
        resp = self.client.get("/events")
        events = resp.json()["events"]
        assert len(events) == 1
        assert events[0]["event_id"] == "evt-ack-2"

    def test_rate_limiting(self):
        # Create app with very low rate limit
        app = create_relay_app(
            db_path=self.db_path,
            shared_secrets={"test_bot": self.secret},
            max_requests_per_minute=2,
        )
        client = TestClient(app)

        payload = {"bot_id": "test_bot", "events": []}
        body = json.dumps(payload, sort_keys=True)
        sig = hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-Signature": sig}

        # First two should succeed
        assert client.post("/events", content=body, headers=headers).status_code == 200
        assert client.post("/events", content=body, headers=headers).status_code == 200
        # Third should be rate limited
        assert client.post("/events", content=body, headers=headers).status_code == 429

    def test_empty_events_list(self):
        payload = {"bot_id": "test_bot", "events": []}
        resp = self._sign_and_post(payload)
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 0
