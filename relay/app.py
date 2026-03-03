"""FastAPI relay application — buffers events from trading bots."""
import logging
from typing import Any

from fastapi import FastAPI, Header, Request, Response
from pydantic import BaseModel

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

    app = FastAPI(title="Trading Relay", version="1.0.0")
    store = EventStore(db_path=db_path)
    auth = HMACAuth(shared_secrets=shared_secrets)
    limiter = RateLimiter(max_requests=max_requests_per_minute)

    @app.post("/events", response_model=IngestResponse)
    async def ingest_events(
        request: Request,
        x_signature: str = Header(default=""),
    ):
        """Receive HMAC-signed event batches from trading bots."""
        body = await request.body()

        # Parse body to get bot_id for auth lookup
        try:
            import json
            data = json.loads(body)
            bot_id = data.get("bot_id", "")
        except Exception:
            return Response(status_code=400, content="Invalid JSON")

        # HMAC verification
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
        """Health check endpoint."""
        pending = store.count_pending()
        return {"status": "ok", "pending_events": pending}

    return app
