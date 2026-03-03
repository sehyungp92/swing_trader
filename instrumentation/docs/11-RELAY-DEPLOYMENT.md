# Task 11: Deploy the Relay Service (This VPS Only)

> **This task only applies to the VPS designated as the relay host.**
> If this bot's VPS is not the relay host, skip this task entirely.

## Goal

Deploy the relay service — a lightweight FastAPI app that buffers events from all trading bots and serves them to the home orchestrator on demand. The relay runs alongside this bot on the same VPS.

The relay is ~100 lines of meaningful code. It does three things:
1. `POST /events` — bots push HMAC-signed event batches here
2. `GET /events?since=<watermark>` — home orchestrator pulls events
3. `POST /ack` — home orchestrator confirms receipt

No analysis, no logic — pure event buffer backed by SQLite.

## Prerequisites

- This VPS has Python 3.12+ installed
- You have root/sudo access for systemd and nginx configuration
- You have a domain or subdomain pointing to this VPS (e.g., `relay.yourdomain.com`)
- You know the `bot_id` and HMAC secret for each bot that will push events

## Step 1: Copy Relay Files

The relay code lives in the `trading_assistant` repository. You need these directories on the VPS:

```
/opt/trading-relay/
  relay/
    __init__.py
    app.py              # FastAPI application
    auth.py             # HMAC verification
    db/
      __init__.py
      store.py          # SQLite event storage
      schema.sql        # table definitions
    rate_limiter.py     # per-bot rate limiting
  schemas/
    __init__.py
    notifications.py    # (dependency of relay, may be needed for imports)
  pyproject.toml        # for pip install
```

Transfer from the trading_assistant repo:
```bash
# From the trading_assistant repo on your local machine:
rsync -avz relay/ schemas/ pyproject.toml user@relay-vps:/opt/trading-relay/
```

Or if rsync is not available, tar and scp:
```bash
tar czf /tmp/relay-deploy.tar.gz relay/ schemas/ pyproject.toml
scp /tmp/relay-deploy.tar.gz user@relay-vps:/opt/trading-relay/
ssh user@relay-vps "cd /opt/trading-relay && tar xzf relay-deploy.tar.gz"
```

## Step 2: Install Dependencies

```bash
ssh user@relay-vps
cd /opt/trading-relay

# Create virtualenv
python3.12 -m venv venv
source venv/bin/activate

# Install
pip install fastapi uvicorn[standard] aiosqlite pydantic
```

## Step 3: Configure Shared Secrets

Create a configuration file with the HMAC shared secret for each bot:

```bash
# /opt/trading-relay/secrets.json
```

```json
{
  "bot_alpha": "generate-a-random-64-char-hex-secret-here",
  "bot_beta": "generate-a-different-random-secret-here"
}
```

Generate secrets:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**Important:** Each bot's sidecar must use the matching secret in its `INSTRUMENTATION_HMAC_SECRET` environment variable.

## Step 4: Create the Startup Script

```bash
# /opt/trading-relay/start.sh
```

```bash
#!/bin/bash
set -euo pipefail

cd /opt/trading-relay
source venv/bin/activate

# Load secrets into environment for the app
export RELAY_SECRETS_FILE="/opt/trading-relay/secrets.json"

exec uvicorn relay.app:app \
  --host 127.0.0.1 \
  --port 8001 \
  --workers 1 \
  --log-level info
```

However, the default `relay.app:app` calls `create_relay_app()` without secrets. You need a thin wrapper that reads the secrets file:

```bash
# /opt/trading-relay/run_relay.py
```

```python
"""Relay entry point — loads secrets and creates the app."""
import json
import os
from pathlib import Path
from relay.app import create_relay_app

secrets_file = os.environ.get("RELAY_SECRETS_FILE", "/opt/trading-relay/secrets.json")
secrets = {}
if Path(secrets_file).exists():
    secrets = json.loads(Path(secrets_file).read_text())

app = create_relay_app(
    db_path="/opt/trading-relay/data/relay.db",
    shared_secrets=secrets,
)
```

Update the start script to use this:
```bash
#!/bin/bash
set -euo pipefail
cd /opt/trading-relay
source venv/bin/activate
export RELAY_SECRETS_FILE="/opt/trading-relay/secrets.json"

exec uvicorn run_relay:app \
  --host 127.0.0.1 \
  --port 8001 \
  --workers 1 \
  --log-level info
```

```bash
chmod +x /opt/trading-relay/start.sh
mkdir -p /opt/trading-relay/data
```

## Step 5: Create Systemd Service

```bash
sudo tee /etc/systemd/system/trading-relay.service << 'EOF'
[Unit]
Description=Trading Assistant Relay
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/trading-relay
ExecStart=/opt/trading-relay/start.sh
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-relay
sudo systemctl start trading-relay
sudo systemctl status trading-relay
```

Check logs:
```bash
journalctl -u trading-relay -f
```

## Step 6: Set Up Reverse Proxy

### Option A: Nginx

```bash
sudo tee /etc/nginx/sites-available/trading-relay << 'EOF'
server {
    listen 80;
    server_name relay.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Increase body size for large event batches
        client_max_body_size 10m;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/trading-relay /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Option B: Caddy (simpler, auto-SSL)

```bash
# /etc/caddy/Caddyfile (add this block)
relay.yourdomain.com {
    reverse_proxy 127.0.0.1:8001
}
```

```bash
sudo systemctl reload caddy
```

### SSL with Let's Encrypt (Nginx only)

Caddy handles SSL automatically. For nginx:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d relay.yourdomain.com
```

## Step 7: Test the Relay

### Health check (basic connectivity)

```bash
curl -s https://relay.yourdomain.com/events?limit=0
# Expected: {"events": []}
```

### Test HMAC-signed ingest

```bash
python3 << 'PYEOF'
import hashlib, hmac, json, urllib.request

secret = "your-bot-alpha-secret-here"  # from secrets.json
payload = {
    "bot_id": "bot_alpha",
    "events": [{
        "event_id": "test-event-001",
        "bot_id": "bot_alpha",
        "event_type": "heartbeat",
        "payload": "{}",
        "exchange_timestamp": "2026-03-02T00:00:00+00:00"
    }]
}

canonical = json.dumps(payload, sort_keys=True)
sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()

req = urllib.request.Request(
    "https://relay.yourdomain.com/events",
    data=canonical.encode(),
    headers={"Content-Type": "application/json", "X-Signature": sig},
    method="POST",
)
resp = urllib.request.urlopen(req)
print(resp.status, json.loads(resp.read()))
PYEOF
# Expected: 200 {"accepted": 1, "duplicates": 0}
```

### Test pull (simulating the home orchestrator)

```bash
curl -s "https://relay.yourdomain.com/events?limit=10"
# Expected: {"events": [{"event_id": "test-event-001", ...}]}
```

### Test ack

```bash
curl -s -X POST "https://relay.yourdomain.com/ack" \
  -H "Content-Type: application/json" \
  -d '{"watermark": "test-event-001"}'
# Expected: {"status": "ok", "watermark": "test-event-001"}
```

### Test duplicate rejection

Run the ingest test again with the same event_id:
```bash
# Re-run the HMAC ingest test above
# Expected: 200 {"accepted": 0, "duplicates": 1}
```

### Test bad signature

```bash
curl -s -X POST "https://relay.yourdomain.com/events" \
  -H "Content-Type: application/json" \
  -H "X-Signature: badbadbadbad" \
  -d '{"bot_id": "bot_alpha", "events": []}'
# Expected: 401
```

## Step 8: Configure the Home Orchestrator

On your home machine's `.env`:

```bash
RELAY_URL=https://relay.yourdomain.com
```

The orchestrator's `VPSReceiver` will automatically:
- Poll `GET /events?since=<watermark>` every 5 minutes (configurable)
- Store events in the local SQLite queue
- Ack events via `POST /ack`
- Catch up on missed events at startup via `drain()`

## Step 9: Configure This Bot's Sidecar

Since this bot runs on the same VPS as the relay, it can use `localhost`:

```yaml
# instrumentation/config/instrumentation_config.yaml
sidecar:
  relay_url: "http://127.0.0.1:8001/events"   # localhost, no SSL overhead
  hmac_secret_env: "INSTRUMENTATION_HMAC_SECRET"
  batch_size: 50
  retry_max: 5
  retry_backoff_base_seconds: 10
  poll_interval_seconds: 60
```

For bots on OTHER VPSes, they use the public URL:
```yaml
sidecar:
  relay_url: "https://relay.yourdomain.com/events"
```

## Maintenance

### Check relay status
```bash
sudo systemctl status trading-relay
journalctl -u trading-relay --since "1 hour ago"
```

### Check database size
```bash
du -h /opt/trading-relay/data/relay.db
```

### Manual cleanup (if DB grows too large)
Events are acked after the orchestrator pulls them, but the rows remain.
To purge old acked events:
```bash
cd /opt/trading-relay
source venv/bin/activate
python3 -c "
import sqlite3
conn = sqlite3.connect('data/relay.db')
conn.execute('DELETE FROM events WHERE acked = 1')
conn.execute('VACUUM')
conn.commit()
print('Purged acked events')
"
```

### Update relay code
```bash
# From local machine:
rsync -avz relay/ schemas/ user@relay-vps:/opt/trading-relay/
ssh user@relay-vps "sudo systemctl restart trading-relay"
```

---

## Done Criteria

- [ ] Relay service is running on the VPS (`systemctl status trading-relay` shows active)
- [ ] HTTPS endpoint is accessible (`curl https://relay.yourdomain.com/events?limit=0`)
- [ ] HMAC authentication works (signed request returns 200, bad signature returns 401)
- [ ] Events can be ingested (test ingest returns `accepted: 1`)
- [ ] Events can be pulled (test pull returns the ingested event)
- [ ] Ack works (acked events no longer appear in pull)
- [ ] Duplicates are rejected (same event_id returns `duplicates: 1`)
- [ ] Service auto-restarts on failure (`sudo systemctl restart trading-relay`)
- [ ] This bot's sidecar can reach the relay via localhost
- [ ] Home orchestrator has `RELAY_URL` set in `.env`
