"""FastAPI relay application — buffers events from trading bots."""
import gzip
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, Request, Response
from pydantic import BaseModel
from starlette.middleware.gzip import GZipMiddleware

from relay.auth import HMACAuth
from relay.db.store import EventStore
from relay.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


# --- Request / Response models ---

class EventIn(BaseModel):
    event_id: str
    bot_id: str
    event_type: str = "unknown"
    payload: str = "{}"
    exchange_timestamp: str = ""
    priority: int = 3


class IngestRequest(BaseModel):
    bot_id: str
    events: list[EventIn]


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int


class AckRequest(BaseModel):
    watermark: str


class AckResponse(BaseModel):
    status: str
    watermark: str
    acked_count: int = 0


# --- App factory ---

def create_relay_app(
    db_path: str = "data/relay.db",
    shared_secrets: dict[str, str] | None = None,
    max_requests_per_minute: int = 60,
) -> FastAPI:
    """Create and configure the relay FastAPI app."""

    import time as _time
    _start_mono = _time.monotonic()

    store = EventStore(db_path=db_path)
    auth = HMACAuth(shared_secrets=shared_secrets)
    limiter = RateLimiter(max_requests=max_requests_per_minute)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: purge old acked events
        try:
            purged = store.purge_acked(days=7)
            if purged:
                logger.info("Startup purge: removed %d old acked events", purged)
        except Exception as e:
            logger.warning("Startup purge failed: %s", e)
        yield

    app = FastAPI(title="Trading Relay", version="1.0.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.post("/events", response_model=IngestResponse)
    async def ingest_events(
        request: Request,
        x_signature: str = Header(default=""),
    ):
        """Receive HMAC-signed event batches from trading bots."""
        raw_body = await request.body()

        # Decompress gzip if Content-Encoding header present
        content_encoding = request.headers.get("content-encoding", "")
        if content_encoding == "gzip":
            try:
                body = gzip.decompress(raw_body)
            except Exception:
                return Response(status_code=400, content="Invalid gzip data")
        else:
            body = raw_body

        # Parse body to get bot_id for auth lookup
        try:
            import json
            data = json.loads(body)
            bot_id = data.get("bot_id", "")
        except Exception:
            return Response(status_code=400, content="Invalid JSON")

        # HMAC verification (always against decompressed canonical JSON)
        if auth.enabled and not auth.verify(body, x_signature, bot_id):
            return Response(status_code=401, content="Invalid signature")

        # Rate limiting
        if not limiter.is_allowed(bot_id):
            return Response(
                status_code=429,
                content="Rate limit exceeded",
                headers={"Retry-After": "60"},
            )

        # Parse and store events
        try:
            ingest = IngestRequest(**data)
            events = [e.model_dump() for e in ingest.events]
            result = store.insert_events(events)
            logger.info(
                "Ingested from %s: %d accepted, %d duplicates",
                bot_id, result["accepted"], result["duplicates"],
            )
            return IngestResponse(**result)
        except Exception as e:
            logger.error("Ingest error: %s", e)
            return Response(status_code=400, content=str(e))

    @app.get("/events")
    async def get_events(
        since: str | None = None,
        limit: int = 100,
        bot_id: str | None = None,
    ) -> dict[str, Any]:
        """Pull un-acked events (used by home orchestrator)."""
        events = store.get_events(since=since, limit=min(limit, 1000), bot_id=bot_id)
        return {"events": events}

    @app.post("/ack", response_model=AckResponse)
    async def ack_events(req: AckRequest):
        """Acknowledge events up to a watermark (used by home orchestrator)."""
        count = store.ack_up_to(req.watermark)
        return AckResponse(status="ok", watermark=req.watermark, acked_count=count)

    @app.get("/health")
    async def health():
        """Health check endpoint with enriched stats."""
        pending = store.count_pending()
        stats = store.get_stats()
        uptime = _time.monotonic() - _start_mono
        return {
            "status": "ok",
            "pending_events": pending,
            "per_bot_pending": stats["per_bot_pending"],
            "last_event_per_bot": stats["last_event_per_bot"],
            "oldest_pending_age_seconds": stats["oldest_pending_age_seconds"],
            "db_size_bytes": stats["db_size_bytes"],
            "uptime_seconds": round(uptime, 1),
        }

    @app.post("/admin/purge")
    async def admin_purge(days: int = 7):
        """Purge acked events older than N days."""
        deleted = store.purge_acked(days=days)
        return {"status": "ok", "deleted": deleted, "retention_days": days}

    return app
