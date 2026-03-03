# ATRSS Multi-Strategy Trading System — Implementation Guide

Complete guide to deploying, operating, and extending the swing_trader system: five algorithmic strategies (ATRSS, S5_PB, S5_DUAL, SWING_BREAKOUT_V3, AKC_HELIX) with shared OMS, risk management, IB Gateway integration, PostgreSQL persistence, a purpose-built Next.js trading dashboard, and a full instrumentation layer with relay service for centralized analysis.

---

## Part 1: Current System Status

### What's Implemented

| Component | Status | Description |
|-----------|--------|-------------|
| **ATRSS** (Strategy 1) | Complete | ETF Trend-Regime Swing System — pullback/breakout/reverse entries, pyramiding (add-on A/B), chandelier trailing, partial profit-taking, stall detection |
| **S5_PB** (Strategy 4 — Pullback) | Complete | Keltner Momentum Pullback — daily-bar Keltner channel + ROC momentum on IBIT, ATR trailing stops |
| **S5_DUAL** (Strategy 4 — Dual) | Complete | Keltner Momentum Dual — daily-bar dual-entry mode on GLD/IBIT, RSI-gated longs, Keltner channel |
| **AKC_HELIX** (Strategy 2) | Complete | Divergence-based swing system — 4H/1H hidden & classic divergence, MACD momentum, corridor-cap trailing, DIRTY re-entry |
| **SWING_BREAKOUT_V3** (Strategy 3) | Complete | Compression breakout system — squeeze detection, displacement scoring, adaptive L-bucket sizing, re-entry campaigns |
| **OMS** | Complete | Intent-based order management — risk gateway, execution router, fill processor, timeout monitor, event bus, reconciliation |
| **Risk Management** | Complete | Pre-trade risk gates — daily stops, heat caps, priority reservations, per-strategy ceilings, event blackout calendar, market holiday/half-day calendar |
| **IBKR Adapter** | Complete | Async IB Gateway integration — order submission, fill handling, position reconciliation, error classification, heartbeat |
| **Cross-Strategy Coordination** | Complete | Shared coordinator — ATRSS entry tightens Helix stop to BE, size boost (1.25x) when ATRSS active same direction |
| **Backtesting** | Complete | Full framework — SimBroker, portfolio engine, walk-forward, Bayesian optimization, ablation, per-strategy diagnostics |
| **Docker Infrastructure** | Complete | PostgreSQL 16, Next.js dashboard, per-strategy containers with Docker Compose profiles |
| **Trading Dashboard** | Complete | Next.js 14 dark terminal UI — live positions, orders, strategy health, equity curve, 30-second polling |
| **Database** | Complete | PostgreSQL with role separation (admin/writer/reader), auto-init SQL, retention jobs |
| **Instrumentation** | Complete | Event logging layer — market snapshots, trade events, missed opportunities, process quality scoring, daily aggregates, regime classification, sidecar forwarder |
| **Relay Service** | Complete | FastAPI event buffer — HMAC-signed ingest, SQLite store, watermark-based pull/ack, rate limiting, systemd/nginx deployment templates |

### Architecture

```
Ubuntu VPS
├── IB Gateway (systemd service, port 4002)
│   └── via IBC 3.19.0 + Xvfb (headless)
│
├── Trading Relay (systemd service, port 8001)
│   └── FastAPI + SQLite event buffer ──► home orchestrator pulls via HTTPS
│
├── Instrumentation (per-strategy, in-process)
│   ├── JSONL event files ──► sidecar ──► relay:8001
│   └── data/: snapshots/, trades/, missed/, daily/, scores/, errors/
│
└── Docker
    ├── postgres (127.0.0.1:5432)
    │   └── trading database (OMS state, trades, risk)
    ├── dashboard (port 3000, Next.js 14)
    ├── atrss strategy ────────► IB Gateway:4002
    ├── akc_helix strategy ────► IB Gateway:4002
    ├── swing_breakout strategy ► IB Gateway:4002
    ├── s5_pb (KeltnerEngine) ──► IB Gateway:4002
    └── s5_dual (KeltnerEngine) ► IB Gateway:4002
```

Each strategy container runs its own engine (`python -m strategy`, `python -m strategy_2`, `python -m strategy_3`). The multi-strategy launcher (`main_multi.py`) runs all five strategies (ATRSS, S5_PB, S5_DUAL, Breakout, Helix) in a single process with a shared OMS, `StrategyCoordinator`, and `MarketCalendar`.

### Key File Paths

| File / Directory | Purpose |
|------------------|---------|
| `main_multi.py` | Multi-strategy orchestrator (shared OMS + coordinator) |
| `strategy/` | ATRSS — `engine.py` (`ATRSSEngine`), `config.py`, `indicators.py`, `signals.py`, `stops.py`, `allocator.py` |
| `strategy_2/` | AKC_HELIX — `engine.py` (`HelixEngine`), `config.py`, `gates.py`, `indicators.py`, `signals.py` |
| `strategy_3/` | SWING_BREAKOUT_V3 — `engine.py` (`BreakoutEngine`), `config.py`, `gates.py`, `indicators.py` |
| `strategy_4/` | Keltner Momentum (S5) — `engine.py` (`KeltnerEngine`), `config.py` (`SYMBOL_CONFIGS`, `S5_PB_CONFIGS`, `S5_DUAL_CONFIGS`), `signals.py`, `indicators.py`, `models.py` |
| `shared/market_calendar.py` | US equity & CME futures holiday/half-day calendar — pure stdlib, year-cached |
| `shared/oms/` | OMS core — `services/oms_service.py`, `intent/handler.py`, `risk/gateway.py`, `engine/state_machine.py`, `engine/fill_processor.py`, `execution/router.py`, `events/bus.py` |
| `shared/oms/models/` | Data models — `order.py`, `position.py`, `instrument.py`, `intent.py`, `risk_state.py`, `fill.py` |
| `shared/oms/persistence/` | DB layer — `postgres.py` (`PgStore`), `repository.py`, `schema.py`, `in_memory.py` |
| `shared/oms/config/risk_config.py` | `RiskConfig`, `StrategyRiskConfig` dataclasses |
| `shared/oms/coordination/coordinator.py` | `StrategyCoordinator` — cross-strategy rules |
| `shared/oms/reconciliation/orchestrator.py` | OMS ↔ IB state reconciliation |
| `shared/ibkr_core/adapters/execution_adapter.py` | `IBKRExecutionAdapter` — sole OMS↔IB interface |
| `shared/ibkr_core/client/session.py` | `IBSession` — async connection wrapper |
| `shared/ibkr_core/mapping/contract_factory.py` | `ContractFactory` — symbol → IB Contract |
| `shared/ibkr_core/mapping/order_mapper.py` | OMS order → IB Trade order |
| `shared/services/bootstrap.py` | `bootstrap_database()` — DB pool init |
| `shared/services/trade_recorder.py` | `TradeRecorder` — trade logging to DB |
| `shared/services/heartbeat.py` | `HeartbeatService` — periodic health reporting |
| `backtest/` | Backtesting — `cli.py`, `engine/backtest_engine.py`, `engine/s5_engine.py`, `engine/unified_portfolio_engine.py`, `engine/sim_broker.py`, `optimization/`, `analysis/` |
| `config/contracts.yaml` | Futures & stock contract specs (tick_size, multiplier, exchange) |
| `config/ibkr_profiles.yaml` | IBKR connection profile (host, port, client_id, account_id) |
| `config/routing.yaml` | Exchange routing for futures symbols |
| `.env.example` | Environment variable template |
| `infra/dashboard/` | Next.js 14 trading dashboard — see Part 7 |
| `infra/dashboard/src/app/api/` | 8 API routes: `portfolio`, `strategies`, `positions`, `trades`, `orders`, `health`, `equity-curve`, `daily-pnl` |
| `infra/dashboard/src/components/` | React components: `PortfolioHeader`, `StrategyGrid`, `StrategyCard`, `PositionsTable`, `TradesTable`, `OrdersTable`, `SystemHealth`, `EquityCurve`, `DailyPnlBars`, `RefreshIndicator` |
| `infra/dashboard/src/lib/db.ts` | `pg` Pool singleton — `trading_reader` role, NUMERIC/INT8 type parsers |
| `infra/dashboard/src/lib/types.ts` | TypeScript interfaces + `STRATEGY_CONFIG` constants |
| `infra/dashboard/src/lib/formatters.ts` | `fmtR`, `fmtUSD`, `fmtAge`, `fmtHoldTime`, `fmtDate`, `fmtTime` |
| `instrumentation/` | Event instrumentation layer — see Part 7b |
| `instrumentation/src/` | Core modules: `event_metadata.py`, `market_snapshot.py`, `trade_logger.py`, `missed_opportunity.py`, `process_scorer.py`, `daily_snapshot.py`, `regime_classifier.py`, `sidecar.py` |
| `instrumentation/config/` | `instrumentation_config.yaml`, `simulation_policies.yaml`, `regime_classifier_config.yaml`, `process_scoring_rules.yaml` |
| `instrumentation/data/` | JSONL event files: `snapshots/`, `trades/`, `missed/`, `daily/`, `scores/`, `errors/` |
| `instrumentation/tests/` | 69 tests: unit tests for each module + integration lifecycle test |
| `instrumentation/audit_report.md` | Codebase audit identifying hook points and missing data |
| `relay/` | FastAPI relay service — see Part 7c |
| `relay/app.py` | FastAPI app factory: `POST /events`, `GET /events`, `POST /ack`, `GET /health` |
| `relay/auth.py` | HMAC-SHA256 signature verification per bot_id |
| `relay/db/store.py` | SQLite event store with duplicate rejection and watermark ack |
| `relay/db/schema.sql` | SQLite table + index definitions |
| `relay/rate_limiter.py` | Per-bot sliding window rate limiter |
| `relay/tests/test_relay.py` | 23 tests: store, auth, rate limiting, full API integration |
| `run_relay.py` | Relay entry point — loads secrets, creates app |
| `relay/start.sh` | Startup script for systemd |
| `relay/trading-relay.service` | systemd unit file template |
| `relay/nginx-trading-relay.conf` | Nginx reverse proxy template |
| `Dockerfile` | Python 3.12-slim, copies shared + config + strategies + instrumentation |
| `infra/docker-compose.yml` | Service orchestration (postgres, dashboard, 3 strategy profiles) |
| `infra/init-db.sql` | DB init — roles (`trading_admin`, `trading_writer`, `trading_reader`), grants |
| `infra/retention.sql` | Daily cleanup (order_events 60d, vacuum) |
| `infra/cron/retention.sh` | Cron wrapper for retention SQL |
| `infra/ibc/config.ini.example` | IBC configuration template |
| `infra/systemd/ibgateway.service` | systemd unit for headless IB Gateway |
| `infra/deploy.sh` | Automated deployment script |
| `requirements.txt` | Python deps: numpy, pandas, pyarrow, matplotlib, ib_async, asyncpg, pydantic, pyyaml |

---

## Part 2: Pre-Deployment Checklist (Local)

### 2.1 IBKR Paper Account Prerequisites

1. **Paper trading account** — Log into [IBKR Account Management](https://www.interactivebrokers.com/) and ensure paper trading is enabled (account ID starts with `DU`).
2. **API access** — Account Management → Settings → API → Enable ActiveX and Socket Clients. Set "Trusted IPs" to include `127.0.0.1`.
3. **Market data subscriptions** — Subscribe to data for your target instruments:
   - ETFs (QQQ, GLD, USO, IBIT): US Securities Snapshot and Futures Value Bundle, or US Equity & Options Add-On
   - Micro Futures (MNQ, MCL, MGC, MBT): CME/COMEX/NYMEX market data bundles
   - Paper accounts get 15-minute delayed data by default; real-time requires subscriptions

### 2.2 Local Python Environment

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
```

**requirements.txt contents:**
```
numpy>=1.26
pandas>=2.3
pyarrow>=15.0
matplotlib>=3.10
ib_async>=2.1
asyncpg>=0.31
pydantic>=2.12
pyyaml>=6.0
```

The relay service has separate dependencies (installed in its own venv on the VPS): `fastapi`, `uvicorn[standard]`, `aiosqlite`, `pydantic`.

### 2.3 Run Backtests Locally

Validate strategy logic before deploying:

```bash
# ATRSS backtest
python -m backtest run --start 2020-01-01 --end 2024-12-31

# Walk-forward validation
python -m backtest walk-forward --test-months 12

# Parameter sensitivity
python -m backtest ablation --filter momentum_filter
```

Review output in the generated reports (equity curves, trade summaries, drawdown analysis).

### 2.4 Verify Configuration Files

**`config/contracts.yaml`** — Ensure all target symbols are defined with correct `tick_size`, `multiplier`, `exchange`:

| Symbol | Type | Exchange | Multiplier | Tick Size |
|--------|------|----------|------------|-----------|
| MNQ | FUT | CME | 2.0 | 0.25 |
| MCL | FUT | NYMEX | 100.0 | 0.01 |
| MGC | FUT | COMEX | 10.0 | 0.10 |
| MBT | FUT | CME | 0.1 | 5.0 |
| QQQ | STK | SMART | 1.0 | 0.01 |
| USO | STK | SMART | 1.0 | 0.01 |
| GLD | STK | SMART | 1.0 | 0.01 |
| IBIT | STK | SMART | 1.0 | 0.01 |

**`config/ibkr_profiles.yaml`** — Update `account_id` to your paper account:
```yaml
host: "127.0.0.1"
port: 4002
client_id: 7
account_id: "DU1234567"   # ← your paper account ID
```

**`config/routing.yaml`** — Futures exchange routing. No changes needed unless adding new instruments.

### 2.5 Test IB Connection Locally

1. Start TWS or IB Gateway on your desktop
2. Enable API connections (Edit → Global Configuration → API → Settings → Enable, port 4002)
3. Test:
```bash
python -c "
import asyncio
from ib_async import IB
async def test():
    ib = IB()
    await ib.connectAsync('127.0.0.1', 4002, clientId=99)
    print('Connected:', ib.isConnected())
    print('Accounts:', ib.managedAccounts())
    ib.disconnect()
asyncio.run(test())
"
```

---

## Part 3: VPS Provisioning

### 3.1 Recommended Specs

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 40 GB SSD | 80 GB SSD |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

### 3.2 VPS Provider Considerations

Choose a provider with low latency to IBKR servers:
- **IBKR data centers**: Secaucus NJ (US equities), Aurora IL (CME futures), London, Hong Kong
- **Recommended providers**: Hetzner (US-East), DigitalOcean (NYC), Vultr (NJ), AWS us-east-1
- Latency target: <10ms to IBKR for swing trading (not latency-sensitive, but reliable connectivity matters)

### 3.3 Initial Server Setup

```bash
# System update
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl wget unzip software-properties-common ufw

# Timezone — match IBKR's US Eastern for log readability
sudo timedatectl set-timezone America/New_York

# Firewall
sudo ufw allow OpenSSH
sudo ufw allow 3000/tcp    # Trading dashboard (restrict to your IP in production)
sudo ufw enable
```

### 3.4 SSH Hardening

```bash
# Generate SSH key on your local machine (if you haven't already)
ssh-keygen -t ed25519 -C "trading-vps"
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@your-vps-ip

# On the VPS — disable password authentication
sudo nano /etc/ssh/sshd_config
```

Set these values:
```
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
```

```bash
sudo systemctl restart sshd
```

---

## Part 4: IB Gateway Installation (Headless)

IB Gateway runs headlessly on the VPS using IBC (IB Controller) to automate login, and Xvfb to provide a virtual display.

### 4.1 Install Java and Xvfb

```bash
sudo apt install -y default-jre xvfb
java -version   # confirm Java 11+
```

### 4.2 Install IB Gateway

```bash
cd /tmp
wget -O ibgateway-stable-standalone-linux-x64.sh \
  "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"
chmod +x ibgateway-stable-standalone-linux-x64.sh
sudo sh ibgateway-stable-standalone-linux-x64.sh -q -dir /opt/ibgateway
```

### 4.3 Install IBC 3.19.0

```bash
cd /tmp
wget https://github.com/IbcAlpha/IBC/releases/download/3.19.0/IBCLinux-3.19.0.zip
sudo mkdir -p /opt/ibc
sudo unzip IBCLinux-3.19.0.zip -d /opt/ibc
sudo chmod +x /opt/ibc/*.sh /opt/ibc/*/*.sh
```

### 4.4 Configure IBC

```bash
sudo mkdir -p /opt/ibc/config
sudo cp /opt/trading/swing_trader/infra/ibc/config.ini.example /opt/ibc/config/config.ini
sudo nano /opt/ibc/config/config.ini
```

Edit these fields with your paper trading credentials:

```ini
# IBKR Credentials (paper trading)
IbLoginId=YOUR_IBKR_USERNAME
IbPassword=YOUR_IBKR_PASSWORD

# Paper trading mode
TradingMode=paper

# Auto-accept non-brokerage account warning
AcceptNonBrokerageAccountWarning=yes

# Existing session handling
ExistingSessionDetectedAction=primary

# Auto-restart (IBKR resets connections daily ~midnight ET)
AutoRestartTime=00:00

# Accept incoming API connections
AcceptIncomingConnectionAction=accept
ReadOnlyApi=no

# Dismiss popups
DismissPasswordExpiryWarning=yes
DismissNSEComplianceNotice=yes

# Gateway port for paper trading
OverrideTwsApiPort=4002

# Gateway mode (not TWS)
FIX=no
```

Secure the file:
```bash
sudo chmod 600 /opt/ibc/config/config.ini
```

### 4.5 Install systemd Service

The service file (`infra/systemd/ibgateway.service`) starts Xvfb on display `:1` then launches IBC in gateway mode:

```bash
sudo cp /opt/trading/swing_trader/infra/systemd/ibgateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ibgateway
sudo systemctl start ibgateway
```

**Service definition** (for reference):
```ini
[Unit]
Description=IB Gateway (Paper Trading) via IBC
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment="DISPLAY=:1"
ExecStartPre=/bin/bash -c '/usr/bin/Xvfb :1 -screen 0 1024x768x24 &'
ExecStart=/opt/ibc/gatewaystart.sh -inline \
    --ibc-path /opt/ibc \
    --ibc-ini /opt/ibc/config/config.ini \
    --gateway-path /opt/ibgateway \
    --mode paper \
    --on2fa:second-factor-device \
    --tws-settings-path /root/Jts
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

### 4.6 Verify IB Gateway

Wait ~60 seconds for startup, then verify:

```bash
# Check port is listening
ss -tlnp | grep 4002

# Check service status
sudo systemctl status ibgateway

# Check logs
sudo journalctl -u ibgateway --no-pager -n 50
```

Port 4002 should be in LISTEN state. If not, see troubleshooting below.

### 4.7 Troubleshooting IB Gateway

| Problem | Solution |
|---------|----------|
| **Credentials rejected** | Verify username/password in `/opt/ibc/config/config.ini`. Paper account usernames are the same as live, but the password may differ. Log in via web first to confirm. |
| **Java not found** | Run `java -version`. Install with `sudo apt install -y default-jre`. |
| **Xvfb not starting** | Check `ps aux \| grep Xvfb`. If display `:1` is taken, change to `:2` in the service file. |
| **2FA prompt blocking login** | Use IBC's `--on2fa:second-factor-device` flag (already in service file). Alternatively, configure IBKR to use a security device that IBC can handle, or pre-authenticate via web. |
| **Port not listening after 2 minutes** | Check `journalctl -u ibgateway -n 100`. Common: wrong IBC path, missing Java, firewall blocking localhost. |

---

## Part 5: Docker Stack Deployment

### 5.1 Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit   # re-login for group change
```

Verify:
```bash
docker --version
docker compose version
```

### 5.2 Upload Repository

```bash
sudo mkdir -p /opt/trading
sudo chown $USER:$USER /opt/trading
cd /opt/trading

# Option A: git clone
git clone <YOUR_REPO_URL> swing_trader

# Option B: scp from local machine
scp -r /path/to/swing_trader user@your-vps-ip:/opt/trading/swing_trader
```

### 5.3 Configure Environment

```bash
cd /opt/trading/swing_trader
cp .env.example .env
nano .env
```

Set all variables:

```bash
# Environment: dev | backtest | paper | live
SWING_TRADER_ENV=paper

# IBKR connection
IB_ACCOUNT_ID=DU1234567          # Your paper account ID
IB_HOST=host.docker.internal     # Docker's host gateway
IB_PORT=4002                     # Paper trading port

# Database (PostgreSQL)
POSTGRES_PASSWORD=<strong-password>
POSTGRES_READER_PASSWORD=<strong-password>
POSTGRES_WRITER_PASSWORD=<strong-password>
DB_HOST=postgres                 # Docker service name
DB_PORT=5432
DB_NAME=trading
DB_USER=trading_writer
DB_PASSWORD=<same-as-writer-password>

# Strategy symbol sets (optional overrides)
# ATRSS_SYMBOL_SET=etf            # etf | micro | full | all
# AKCHELIX_SYMBOL_SET=etf         # etf | micro_futures | full_futures | all
```

Secure the file:
```bash
chmod 600 .env
```

### 5.4 Start PostgreSQL and Dashboard

```bash
cd /opt/trading/swing_trader

# Build dashboard image (first time or after dashboard code changes)
docker compose -f infra/docker-compose.yml build dashboard

# Start infrastructure
docker compose -f infra/docker-compose.yml up -d postgres dashboard

# Wait for health check
docker compose -f infra/docker-compose.yml ps
# postgres should show "healthy"; dashboard should show "Up"

# Verify postgres is ready
docker exec trading_postgres pg_isready -U trading_admin -d trading

# Verify dashboard started
docker compose -f infra/docker-compose.yml logs dashboard | tail -5
# Expect: "ready - started server on 0.0.0.0:3000"
```

### 5.5 Update Default Database Passwords

The `infra/init-db.sql` creates roles with placeholder passwords. Update them to match your `.env`:

```bash
docker exec -it trading_postgres psql -U trading_admin -d trading -c \
  "ALTER USER trading_writer WITH PASSWORD 'your_actual_writer_password';"

docker exec -it trading_postgres psql -U trading_admin -d trading -c \
  "ALTER USER trading_reader WITH PASSWORD 'your_actual_reader_password';"
```

### 5.6 Build and Start Strategy Containers

```bash
# Build all strategies
docker compose -f infra/docker-compose.yml \
  --profile atrss --profile akc_helix --profile swing_breakout build

# Start all strategies
docker compose -f infra/docker-compose.yml \
  --profile atrss --profile akc_helix --profile swing_breakout up -d

# Verify
docker compose -f infra/docker-compose.yml \
  --profile atrss --profile akc_helix --profile swing_breakout ps
```

**Start specific strategies only:**
```bash
# ATRSS only
docker compose -f infra/docker-compose.yml --profile atrss up -d

# ATRSS + Breakout (skip Helix)
docker compose -f infra/docker-compose.yml --profile atrss --profile swing_breakout up -d
```

### 5.7 Verify Strategy→IB Gateway Connectivity

```bash
docker exec -it trading_atrss python -c \
  "import socket; s = socket.socket(); s.connect(('host.docker.internal', 4002)); print('Connected!'); s.close()"
```

Each strategy container uses `extra_hosts: host.docker.internal:host-gateway` to reach the IB Gateway running on the host.

---

## Part 6: Post-Deployment Verification

### 6.1 Check Strategy Logs

```bash
# ATRSS
docker compose -f infra/docker-compose.yml --profile atrss logs -f atrss

# AKC_HELIX
docker compose -f infra/docker-compose.yml --profile akc_helix logs -f akc_helix

# SWING_BREAKOUT_V3
docker compose -f infra/docker-compose.yml --profile swing_breakout logs -f swing_breakout
```

**Successful bootstrap sequence** — you should see:
1. Database bootstrap (pool created, or fallback to in-memory)
2. IB Gateway connection established
3. Strategy engine started
4. Heartbeat messages (periodic)
5. During market hours: bar data received, indicator computation, signal evaluation

### 6.2 Verify Database Tables

```bash
docker exec -it trading_postgres psql -U trading_admin -d trading -c \
  "SELECT * FROM strategy_state;"
```

**Expected tables** (created by OMS on first boot):
- `strategy_state` — strategy health (mode, heartbeat, heat_r, daily_pnl_r)
- `adapter_state` — broker connection state (connected, disconnect_count_24h)
- `orders` — order lifecycle tracking
- `order_events` — order state transitions
- `fills` — execution fills
- `trades` — completed trade records
- `trade_marks` — MAE/MFE metrics per trade
- `risk_daily_strategy` — daily risk metrics per strategy
- `risk_daily_portfolio` — portfolio-level daily risk

### 6.3 First Signal Generation

Wait for market hours (ETF: 09:30–16:00 ET, Futures: nearly 24h) and observe:
- ATRSS: hourly cycle logs showing `compute_daily_state()`, `compute_hourly_state()`, candidate evaluation
- Helix: divergence scanning, MACD momentum checks
- Breakout: squeeze detection, displacement scoring

### 6.4 Verify Order Submission Flow

When a strategy generates a signal during paper trading:
1. Strategy creates an `Intent` (type=`NEW_ORDER`)
2. `IntentHandler` validates and routes to `RiskGateway`
3. `RiskGateway` runs pre-trade checks (daily stop, heat cap, max working orders)
4. If approved: `ExecutionRouter` queues the order (stops > cancels > replaces > entries)
5. `IBKRExecutionAdapter` submits to IB Gateway
6. Fill events flow back through `FillProcessor` → `EventBus` → strategy

Monitor the flow in strategy logs. Look for `RISK_APPROVED`, `ROUTED`, `WORKING`, `FILLED` state transitions.

### 6.5 Check Trading Dashboard

Open `http://YOUR_VPS_IP:3000` — see Part 7 for dashboard details. The dashboard connects to the `trading` database as `trading_reader` and starts polling immediately. Expect the strategy grid to populate once strategy containers are running and have written their first heartbeat to `strategy_state`.

---

## Part 7: Trading Dashboard

### 7.1 Overview

The dashboard is a Next.js 14 application in the `infra/dashboard/` directory. It connects directly to the `trading` PostgreSQL database as `trading_reader` (SELECT-only) and replaces the previous Metabase service on port 3000.

**Design:** Dark terminal aesthetic (`#0a0b0d` background, green/red P&L, amber warnings), full `font-mono`, responsive up to 1800px wide.

**Polling:**
- **Live** (every 30s): portfolio, strategies, positions, trades, orders, health — via `Promise.allSettled`
- **Charts** (every 5 min): 90-day equity curve, 30-day daily P&L bars

### 7.2 Dashboard Layout

```
PortfolioHeader     ← today P&L + heat gauge + broker pills + halt banner
StrategyGrid        ← 5 cards (2/3/5 col responsive)
PositionsTable | TradesTable
OrdersTable    | SystemHealth
EquityCurve    | DailyPnlBars
RefreshIndicator    ← fixed bottom-right, countdown + last update time
```

**PortfolioHeader zones:**
1. `daily_realized_r` in `text-3xl` green/red + USD sub-line
2. Heat gauge — Progress bar (`heat_r / 2.0`); green <60%, amber 60–90%, red >90%
3. Broker adapter pills (CONNECTED green / DISCONNECTED red)
4. Halt banner — amber/red; hidden when no active halts

**StrategyCard fields:** status badge (RUNNING/HALTED/STALE/STAND_DOWN), mini heat bar vs `maxHeatR`, daily realized R, entry count, daily stop remaining (`2.0 - |daily_pnl_r|`), heartbeat age.

### 7.3 API Routes and Database Views

All routes use `export const dynamic = 'force-dynamic'` and return `Cache-Control: no-store`.

| Route | Source | Notes |
|-------|--------|-------|
| `/api/portfolio` | `risk_daily_portfolio` + `positions` | Subquery for unrealized sum + heat; default zeros on weekend/no-data |
| `/api/strategies` | `v_strategy_health` LEFT JOIN `risk_daily_strategy` | COALESCE risk columns to 0 |
| `/api/positions` | `positions` table directly | `WHERE net_qty != 0`; queries table (not `v_live_positions`) to include `open_risk_r` / `open_risk_dollars` |
| `/api/trades` | `v_today_trades` | `LIMIT 50`; view joins `trades` + `trade_marks` |
| `/api/orders` | `v_working_orders` | Filters to active statuses, computes `age_minutes` |
| `/api/health` | `v_strategy_health` + `v_adapter_health` + `v_active_halts` | 3 queries merged into one response |
| `/api/equity-curve` | `risk_daily_portfolio` | 90-day window, `SUM(...) OVER` for cumulative R |
| `/api/daily-pnl` | `risk_daily_portfolio` | 30-day window |

### 7.4 Local Development

```bash
cd infra/dashboard
npm install          # generates package-lock.json
npm run dev          # http://localhost:3000

# Point at local postgres:
# Edit infra/dashboard/.env.local — DB_HOST=localhost, DB_PASSWORD=your_local_reader_password
```

The `.env.local` file is gitignored. Copy values from the root `.env` for `POSTGRES_READER_PASSWORD`.

### 7.5 Docker Deployment

```bash
cd /opt/trading/swing_trader

# Build dashboard image
docker compose -f infra/docker-compose.yml build dashboard

# Start (postgres must be healthy first)
docker compose -f infra/docker-compose.yml up -d dashboard

# Verify startup
docker compose -f infra/docker-compose.yml logs dashboard
# Expect: "ready - started server on 0.0.0.0:3000"

# Rebuild after code changes
docker compose -f infra/docker-compose.yml build dashboard && \
  docker compose -f infra/docker-compose.yml restart dashboard
```

**Important:** Run `npm install` locally before first Docker build to generate `package-lock.json` (required for `npm ci` in the Dockerfile).

### 7.6 Database Connection

The dashboard connects as `trading_reader` (read-only). Credentials flow:

```
infra/init-db.sql  → creates trading_reader (hardcoded placeholder password)
root .env          → POSTGRES_READER_PASSWORD=<your_actual_password>
infra/docker-compose.yml dashboard service → DB_PASSWORD=${POSTGRES_READER_PASSWORD}
```

Update the placeholder password after first container start:

```bash
docker exec -it trading_postgres psql -U trading_admin -d trading -c \
  "ALTER USER trading_reader WITH PASSWORD 'your_actual_reader_password';"
```

The `trading_reader` role has SELECT on all tables and views in the `public` schema, including the OMS views created at runtime by the OMS process (`trading_writer`). This is guaranteed by the `ALTER DEFAULT PRIVILEGES FOR ROLE trading_writer` grants in `init-db.sql`.

### 7.7 Dashboard Panels Reference

| Section | Data Source | Refresh |
|---------|-------------|---------|
| Portfolio header (P&L, heat, halts) | `/api/portfolio` + `/api/health` | 30s |
| Strategy cards (5 strategies) | `/api/strategies` | 30s |
| Open positions table | `/api/positions` | 30s |
| Today's trades table | `/api/trades` | 30s |
| Working orders table | `/api/orders` | 30s |
| System health (heartbeats, errors) | `/api/health` | 30s |
| 90-day equity curve | `/api/equity-curve` | 5 min |
| 30-day daily P&L bars | `/api/daily-pnl` | 5 min |

---

## Part 7b: Instrumentation Layer

The instrumentation layer captures structured event data from all strategies for downstream analysis. It runs in-process alongside each strategy engine — no separate containers. All data is written to disk first (JSONL), then forwarded to the relay service by the sidecar.

### 7b.1 Architecture

```
Strategy Engine (in-process)
├── TradeLogger ──────► trades/trades_YYYY-MM-DD.jsonl
├── MissedOpportunityLogger ► missed/missed_YYYY-MM-DD.jsonl
├── ProcessScorer ────► scores/scores_YYYY-MM-DD.jsonl
├── MarketSnapshotService ► snapshots/snapshots_YYYY-MM-DD.jsonl
├── DailySnapshotBuilder ► daily/daily_YYYY-MM-DD.json
├── RegimeClassifier ─► tags each trade/snapshot
└── Sidecar (background thread)
    └── reads JSONL files ──► HMAC signs ──► POST relay:8001/events
```

### 7b.2 Core Modules

| Module | File | Purpose |
|--------|------|---------|
| Event Metadata | `instrumentation/src/event_metadata.py` | Deterministic event IDs (SHA256 truncated to 16 hex chars), dual timestamps (exchange + local), clock skew computation |
| Market Snapshot | `instrumentation/src/market_snapshot.py` | Captures bid/ask/mid/spread/ATR/volume at trade time and on interval; IBKR adapter |
| Trade Logger | `instrumentation/src/trade_logger.py` | Wraps entry/exit to capture full context: signal, filters, regime, slippage, strategy params snapshot |
| Missed Opportunity | `instrumentation/src/missed_opportunity.py` | Logs blocked signals with hypothetical outcome backfill (simulated TP/SL from candle walk) |
| Process Scorer | `instrumentation/src/process_scorer.py` | Rules-based quality scoring (0–100) with 21 controlled root-cause tags; independent of PnL |
| Daily Snapshot | `instrumentation/src/daily_snapshot.py` | End-of-day rollup: trade counts, PnL, profit factor, regime breakdown, missed stats, process quality distribution |
| Regime Classifier | `instrumentation/src/regime_classifier.py` | Deterministic rules: MA slope + ADX + ATR percentile → trending_up/trending_down/ranging/volatile/unknown |
| Sidecar | `instrumentation/src/sidecar.py` | Background forwarder: reads JSONL, wraps in relay envelope, HMAC-SHA256 signs, sends with retry + exponential backoff |

### 7b.3 Configuration Files

| File | Purpose |
|------|---------|
| `instrumentation/config/instrumentation_config.yaml` | Central config: bot_id (`swing_multi_01`), data_dir, snapshot intervals, sidecar relay URL, batch size, retry settings |
| `instrumentation/config/simulation_policies.yaml` | Per-strategy assumptions for missed opportunity backfill: entry fill model, slippage (all 2 bps for IBKR), fees, TP/SL logic |
| `instrumentation/config/regime_classifier_config.yaml` | ADX/MA/ATR thresholds for regime classification |
| `instrumentation/config/process_scoring_rules.yaml` | Scoring rules per dimension (regime fit, signal strength, entry latency, slippage, exit reason) with per-strategy overrides |

### 7b.4 Process Quality Root Causes (Controlled Taxonomy)

The process scorer uses exactly 21 fixed tags — no free-form text:

| Category | Tags |
|----------|------|
| Regime | `regime_mismatch`, `regime_aligned`, `regime_unknown` |
| Signal | `weak_signal`, `strong_signal`, `conflicting_signals` |
| Entry | `late_entry`, `early_entry`, `good_entry` |
| Slippage | `high_entry_slippage`, `high_exit_slippage`, `low_slippage` |
| Exit | `premature_exit`, `late_exit`, `good_exit`, `stop_loss_hit`, `take_profit_hit` |
| Result | `normal_win`, `normal_loss`, `exceptional_win` |
| Misc | `oversize_position`, `funding_drag` |

### 7b.5 Data Directory Structure

```
instrumentation/data/
├── snapshots/       # Market snapshots (JSONL, one file per day)
├── trades/          # Trade entry/exit events (JSONL)
├── missed/          # Blocked signal events (JSONL)
├── scores/          # Process quality scores (JSONL)
├── daily/           # Daily aggregate snapshots (JSON)
├── errors/          # Instrumentation error events (JSONL)
└── .sidecar_buffer/ # Sidecar watermark state
```

### 7b.6 Docker Deployment

The `Dockerfile` copies `instrumentation/` into strategy containers. Each strategy container in `docker-compose.yml` has:

- **Named volume** for `instrumentation/data/` — persists JSONL files across container restarts so the sidecar can forward them
- **`INSTRUMENTATION_HMAC_SECRET`** env var — passed from `.env` for sidecar → relay HMAC signing
- **`host.docker.internal`** — sidecar reaches the relay (systemd on host) via `http://host.docker.internal:8001/events`

```yaml
# Per-strategy container additions (already in docker-compose.yml):
environment:
  INSTRUMENTATION_HMAC_SECRET: ${INSTRUMENTATION_HMAC_SECRET:-}
volumes:
  - instrumentation_<strategy>:/app/instrumentation/data
```

**Relay URL handling:** The sidecar checks `RELAY_URL` env var first, then falls back to `relay_url` in the config YAML. The docker-compose sets `RELAY_URL=http://host.docker.internal:8001/events` on all strategy containers, while the config YAML defaults to `http://127.0.0.1:8001/events` for non-Docker runs. No manual config changes needed for either deployment mode.

### 7b.7 Key Design Decisions

- **Fault tolerant**: All instrumentation code is wrapped in try/except. A logger failure never blocks trade execution.
- **Disk first**: Events are written to local JSONL files immediately. The sidecar forwards them asynchronously.
- **Deterministic event IDs**: `SHA256(bot_id|timestamp|event_type|payload_key)[:16]` prevents duplicate processing downstream.
- **HMAC canonicalization**: Sidecar uses `json.dumps(data, sort_keys=True)` before signing. Mismatch causes silent 401 rejections.
- **Per-strategy simulation policies**: Missed opportunity backfill uses strategy-specific assumptions (ATRSS uses atr_offset entry fill, S5_PB uses market fill, etc.).

### 7b.8 Tests

92 total tests (69 instrumentation + 23 relay):

| Test File | Count | Coverage |
|-----------|-------|----------|
| `test_event_metadata.py` | 10 | Determinism, uniqueness, hex format, clock skew, factory |
| `test_market_snapshot.py` | 9 | Capture, file writing, degraded mode, caching, dict provider |
| `test_trade_logger.py` | 9 | Entry/exit, PnL (long+short), fault tolerance, slippage |
| `test_missed_opportunity.py` | 7 | Event creation, assumption tags, simulation policy, backfill queue |
| `test_process_scorer.py` | 8 | Perfect/bad trades, taxonomy enforcement, bounds, classification |
| `test_daily_snapshot.py` | 7 | Trades, no data, missed, scores, regime breakdown, profit factor |
| `test_regime_classifier.py` | 7 | Valid regime, trending, insufficient data, caching, crash safety |
| `test_sidecar.py` | 9 | Wrap event, watermark, HMAC signing, canonical sort_keys |
| `test_integration.py` | 3 | Full day lifecycle, fault tolerance, unique event IDs |
| `test_relay.py` | 23 | Store, auth, rate limiting, full API (ingest/pull/ack/duplicates) |

```bash
# Run all instrumentation + relay tests
PYTHONPATH="$(pwd):$PYTHONPATH" python -m pytest instrumentation/tests/ relay/tests/ -v
```

---

## Part 7c: Relay Service

The relay is a lightweight FastAPI app (~100 lines of meaningful code) that buffers events from all trading bots and serves them to the home orchestrator on demand. It runs on the same VPS as the bot, backed by SQLite.

### 7c.1 API Endpoints

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| `POST /events` | HMAC-SHA256 signed | Yes | Bots push event batches |
| `GET /events?since=<watermark>&limit=100&bot_id=<id>` | No | Home orchestrator pulls un-acked events |
| `POST /ack` | No | Home orchestrator confirms receipt up to a watermark |
| `GET /health` | No | Health check with pending event count |

### 7c.2 Components

| File | Purpose |
|------|---------|
| `relay/app.py` | FastAPI app factory with Pydantic request/response models |
| `relay/auth.py` | HMAC-SHA256 verification — per-bot secrets from JSON file; auth disabled if no secrets configured |
| `relay/db/store.py` | SQLite store: `insert_events()` (duplicate rejection via UNIQUE event_id), `get_events()` (watermark + bot_id filter), `ack_up_to()` |
| `relay/db/schema.sql` | `events` table with indexes on `acked`, `bot_id`, `event_id`, `received_at` |
| `relay/rate_limiter.py` | Sliding window rate limiter (default 60 req/min per bot) |

### 7c.3 Deployment (VPS)

The relay deploys to `/opt/trading-relay/` on the VPS:

```bash
# 1. Copy files
rsync -avz relay/ run_relay.py user@vps:/opt/trading-relay/

# 2. Install dependencies
ssh user@vps "cd /opt/trading-relay && python3.12 -m venv venv && source venv/bin/activate && pip install fastapi uvicorn[standard] aiosqlite pydantic"

# 3. Generate HMAC secret
python3 -c "import secrets; print(secrets.token_hex(32))"
# Save to /opt/trading-relay/secrets.json: {"swing_multi_01": "<secret>"}

# 4. Install systemd service
sudo cp /opt/trading-relay/trading-relay.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now trading-relay

# 5. Set up nginx reverse proxy
sudo cp /opt/trading-relay/nginx-trading-relay.conf /etc/nginx/sites-available/trading-relay
sudo ln -s /etc/nginx/sites-available/trading-relay /etc/nginx/sites-enabled/
sudo certbot --nginx -d relay.yourdomain.com
```

### 7c.4 Testing the Relay

```bash
# Health check
curl -s http://127.0.0.1:8001/health
# {"status": "ok", "pending_events": 0}

# HMAC-signed ingest test
python3 -c "
import hashlib, hmac, json, urllib.request
secret = 'your-secret-here'
payload = {'bot_id': 'swing_multi_01', 'events': [{'event_id': 'test-001', 'bot_id': 'swing_multi_01', 'event_type': 'heartbeat', 'payload': '{}'}]}
canonical = json.dumps(payload, sort_keys=True)
sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
req = urllib.request.Request('http://127.0.0.1:8001/events', data=canonical.encode(), headers={'Content-Type': 'application/json', 'X-Signature': sig}, method='POST')
print(json.loads(urllib.request.urlopen(req).read()))
"
# {"accepted": 1, "duplicates": 0}
```

### 7c.5 Maintenance

| Action | Command |
|--------|---------|
| Check status | `sudo systemctl status trading-relay` |
| View logs | `journalctl -u trading-relay --since "1 hour ago"` |
| Check DB size | `du -h /opt/trading-relay/data/relay.db` |
| Purge acked events | `sqlite3 /opt/trading-relay/data/relay.db "DELETE FROM events WHERE acked = 1; VACUUM;"` |
| Restart after code update | `sudo systemctl restart trading-relay` |

---

## Part 8: Cron Jobs & Maintenance

### 8.1 Data Retention Cron Job

The retention script runs `infra/retention.sql` daily — deleting old order events (60 days), resetting disconnect counters, and vacuuming tables.

```bash
# Create log directory
sudo mkdir -p /var/log/trading
sudo chown $USER:$USER /var/log/trading

# Make script executable
chmod +x /opt/trading/swing_trader/infra/cron/retention.sh

# Add to crontab (daily at 00:05 UTC)
(crontab -l 2>/dev/null; echo "5 0 * * * /opt/trading/swing_trader/infra/cron/retention.sh") | crontab -
```

**What `infra/retention.sql` does:**
```sql
-- Delete old order events (60 days)
DELETE FROM order_events WHERE event_ts < now() - INTERVAL '60 days';

-- Reset daily disconnect counters
UPDATE adapter_state SET disconnect_count_24h = 0;

-- Vacuum for performance
VACUUM ANALYZE order_events;
VACUUM ANALYZE fills;
VACUUM ANALYZE trades;
```

### 8.2 Log Rotation

Docker handles log rotation for containers. For system logs:

```bash
# /etc/logrotate.d/trading
cat <<'EOF' | sudo tee /etc/logrotate.d/trading
/var/log/trading/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
}
EOF
```

### 8.3 Database Backup

```bash
# Daily backup script
cat <<'SCRIPT' > /opt/trading/backup-db.sh
#!/bin/bash
set -euo pipefail
BACKUP_DIR="/opt/trading/backups"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)
docker exec trading_postgres pg_dump -U trading_admin trading | gzip > "$BACKUP_DIR/trading_${DATE}.sql.gz"
# Keep last 30 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete
SCRIPT
chmod +x /opt/trading/backup-db.sh

# Schedule daily at 01:00 UTC
(crontab -l 2>/dev/null; echo "0 1 * * * /opt/trading/backup-db.sh") | crontab -
```

---

## Part 9: Monitoring & Alerting

### 9.1 Strategy Health Monitoring

The `strategy_state` table tracks each strategy's health:

| Column | Meaning |
|--------|---------|
| `mode` | `RUNNING`, `STAND_DOWN`, `HALTED` |
| `last_heartbeat_ts` | Last heartbeat timestamp |
| `heat_r` | Current open risk in R |
| `daily_pnl_r` | Today's realized P&L in R |
| `last_error` | Most recent error message |
| `last_seen_bar_ts` | Timestamp of last processed bar |

**Quick health check:**
```bash
docker exec -it trading_postgres psql -U trading_admin -d trading -c \
  "SELECT strategy_id, mode, age(now(), last_heartbeat_ts) as heartbeat_age, heat_r, daily_pnl_r FROM strategy_state;"
```

### 9.2 IB Gateway Connection Monitoring

The `adapter_state` table tracks broker connectivity:

| Column | Meaning |
|--------|---------|
| `connected` | Current connection status |
| `last_heartbeat_ts` | Last successful heartbeat |
| `disconnect_count_24h` | Disconnections in last 24 hours |
| `last_error_code` | Last IB error code |

### 9.3 Log Monitoring

```bash
# All strategy logs (last 100 lines, follow)
docker compose -f infra/docker-compose.yml \
  --profile atrss --profile akc_helix --profile swing_breakout logs -f --tail=100

# IB Gateway logs
sudo journalctl -u ibgateway --no-pager -n 50
```

### 9.4 Suggested External Monitoring

- **Process liveness**: Use [UptimeRobot](https://uptimerobot.com/) or [Healthchecks.io](https://healthchecks.io/) to ping a health endpoint or monitor cron jobs.
- **Port monitoring**: Monitor port 4002 (IB Gateway) and 5432 (PostgreSQL) availability.
- **Simple heartbeat script** (add to crontab every 5 minutes):
```bash
#!/bin/bash
# Check if strategies are running and heartbeats are fresh
STALE=$(docker exec trading_postgres psql -U trading_admin -d trading -t -c \
  "SELECT count(*) FROM strategy_state WHERE last_heartbeat_ts < now() - interval '5 minutes';")
if [ "$STALE" -gt 0 ]; then
  echo "WARNING: $STALE strategies have stale heartbeats" | mail -s "Trading Alert" you@email.com
fi
```

### 9.5 Daily IBKR Reset Handling

IBKR resets all connections daily around midnight ET. The system handles this automatically:
- **IBC config**: `AutoRestartTime=00:00` — IBC auto-restarts IB Gateway after the daily reset
- **systemd**: `Restart=on-failure`, `RestartSec=30` — restarts if IB Gateway crashes
- **Docker**: `restart: unless-stopped` — strategy containers restart automatically
- **OMS**: Reconnection logic in `IBSession` detects disconnection and re-establishes the connection
- **Reconciliation**: On reconnect, the reconciliation orchestrator syncs OMS state with IB's actual state

---

## Part 10: Paper-to-Live Transition

### 10.1 Minimum Paper Trading Duration

**Recommended: 2–4 weeks minimum** of paper trading before going live. During this period, validate:

- [ ] Strategies connect to IB Gateway and stay connected across daily resets
- [ ] Orders submit correctly (correct symbol, quantity, price, order type)
- [ ] Fills process correctly (position tracking, P&L computation)
- [ ] Stop orders execute at expected prices
- [ ] Partial profit-taking (TP1/TP2) works correctly
- [ ] Trailing stops adjust as expected
- [ ] Risk gates fire correctly (daily stop, heat cap, max working orders)
- [ ] Cross-strategy coordination works (ATRSS entry → Helix stop tightening)
- [ ] Overnight restarts are seamless (no orphaned orders, no missed fills)
- [ ] Trading dashboard at `:3000` shows accurate real-time positions, orders, and strategy health
- [ ] Database tables grow at expected rates
- [ ] Fill quality and slippage are within acceptable ranges

### 10.2 Configuration Changes for Live

| Setting | Paper | Live |
|---------|-------|------|
| `.env` `SWING_TRADER_ENV` | `paper` | `live` |
| IBC `config.ini` `TradingMode` | `paper` | `live` |
| IBC `config.ini` `OverrideTwsApiPort` | `4002` | `4001` |
| systemd service `--mode` | `paper` | `live` |
| `.env` `IB_PORT` | `4002` | `4001` |
| `.env` `IB_ACCOUNT_ID` | `DU1234567` | `U1234567` (live) |

### 10.3 Risk Parameter Review

Before going live, review and confirm these risk parameters in `main_multi.py`. These match the **optimized_v2** backtest preset ($10K: +191.2% return, -11.8% max DD, 1.22 Sharpe):

| Parameter | ATRSS | S5_PB | S5_DUAL | SWING_BREAKOUT_V3 | AKC_HELIX |
|-----------|-------|-------|---------|-------------------|-----------|
| `unit_risk_pct` | **1.20%** | 0.80% | 0.80% | 0.50% | 0.50% |
| `daily_stop_R` | 2.0 | 2.0 | 2.0 | 2.0 | 2.5 |
| `max_heat_R` | 1.00 | 1.50 | 1.50 | 0.65 | 0.85 |
| `max_working_orders` | 4 | 2 | 2 | 2 | 4 |
| `priority` | 0 (highest) | 1 | 2 | 3 | 4 (lowest) |

**Portfolio-level:**
- `heat_cap_R` = **2.0** (total open risk across all strategies)
- `portfolio_daily_stop_R` = 3.0 (portfolio-wide daily loss limit)

**Note:** S5_PB and S5_DUAL are backtest-only — they have no live engine yet. `main_multi.py` includes their priority/heat config but marks them as TODO for live integration.

**Backtest validation:** `python -m backtest.run_unified --preset optimized_v1 --equity 10000`

Consider starting with **reduced risk** (e.g., 50% of target `unit_risk_pct`) for the first 1–2 weeks of live trading, then scaling up once you confirm live execution quality matches paper.

### 10.4 Gradual Rollout

1. **Week 1**: Start with **ATRSS only** (priority 0, most tested strategy)
2. **Week 2**: Add **SWING_BREAKOUT_V3** if ATRSS is performing as expected
3. **Week 3**: Add **AKC_HELIX** to complete the 3-strategy live setup
4. **When ready**: Add **S5_PB** and **S5_DUAL** once the live engine is built (see Part 11.0)

```bash
# Week 1: ATRSS only
docker compose -f infra/docker-compose.yml --profile atrss up -d

# Week 2: Add Helix
docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix up -d

# Week 3: Full deployment
docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix --profile swing_breakout up -d
```

---

## Part 11: Remaining Implementation Steps

### 11.0 Strategy 4 Live Engine (S5_PB / S5_DUAL) — DONE

**Status: Complete**

`strategy_4/engine.py` implements `KeltnerEngine` — the simplest live engine in the system (daily bars only, no intraday). A single class is instantiated twice: once as S5_PB (IBIT pullback) and once as S5_DUAL (GLD+IBIT dual mode).

**Implementation:**

- `strategy_4/engine.py` (751 lines) — `KeltnerEngine` with daily scheduler at 16:15 ET, `_compute_state()` producing `DailyState`, entry/exit signal evaluation via `strategy_4/signals.py`, risk-based position sizing, trailing stop ratcheting at R >= 1.0, and OMS event processing for fills/cancels.
- `strategy_4/config.py` — `S5_PB_CONFIGS` (IBIT pullback, ema=10, roc=5, stop=1.5 ATR, risk=0.8%) and `S5_DUAL_CONFIGS` (GLD+IBIT dual, ema=15, no shorts, rsi_long=45, risk=0.8%). Includes `build_instruments()` for InstrumentRegistry.
- `main_multi.py` — Both engines wired with priority ordering: ATRSS(0), S5_PB(1), S5_DUAL(2), Breakout(3), Helix(4).

**Live configs verified against `portfolio_optimized_v2.txt`:**

| Parameter | Backtest | Live | Match |
|-----------|----------|------|-------|
| S5_PB symbols | IBIT | IBIT | Yes |
| S5_PB entry_mode | pullback | pullback | Yes |
| S5_PB kelt_ema | 10 | 10 | Yes |
| S5_PB roc_period | 5 | 5 | Yes |
| S5_PB atr_stop_mult | 1.5 | 1.5 | Yes |
| S5_PB risk_pct | 0.008 | 0.008 | Yes |
| S5_PB priority | 1 | 1 | Yes |
| S5_PB max_heat_R | 1.50 | 1.50 | Yes |
| S5_DUAL symbols | GLD, IBIT | GLD, IBIT | Yes |
| S5_DUAL entry_mode | dual | dual | Yes |
| S5_DUAL kelt_ema | 15 | 15 | Yes |
| S5_DUAL shorts_enabled | False | False | Yes |
| S5_DUAL rsi_entry_long | 45.0 | 45.0 | Yes |
| S5_DUAL risk_pct | 0.008 | 0.008 | Yes |
| S5_DUAL priority | 2 | 2 | Yes |
| S5_DUAL max_heat_R | 1.50 | 1.50 | Yes |
| heat_cap_R | 2.0 | 2.0 | Yes |
| portfolio_daily_stop_R | 3.0 | 3.0 | Yes |

**Remaining**: Add `s5_keltner` profile to `infra/docker-compose.yml` for standalone container mode (low priority — multi-strategy launcher is the primary deployment).

### 11.1 Unit Test Suite — DONE

**Status: Complete (884 tests passing)**

Full pytest coverage across all strategies, OMS integration, and paper trading:

```
tests/
├── conftest.py                  # Shared OHLCV data generators
├── test_strategy1_atrss.py      # 88 tests — indicators, signals, stops, allocator
├── test_strategy2_helix.py      # 98 tests — indicators, signals, stops, allocator, gates
├── test_strategy3_breakout.py   # 87 tests — indicators, signals, stops, allocator, gates
├── test_strategy4_keltner.py    # 74 tests — indicators, models, signals (4 entry modes, 3 exit modes), config
├── test_oms_integration.py      # 263 tests — risk gateway, fill processor, state machine, intent handler, event bus
├── test_paper_trading.py        # 219 tests — IBKR adapter, contract factory, execution, reconciliation
└── test_market_calendar.py      # 55 tests — holiday/half-day calendar (see 11.2)
```

**Strategy 4 test coverage** (`test_strategy4_keltner.py`, 74 tests across 17 classes):
- **Indicators**: EMA (SMA seed, convergence, lag), ATR (Wilder smoothing, volatility), RSI (bounds, monotonic, zero-loss), ROC (percentage, boundary), Keltner Channel (symmetry, band width), Volume SMA (expanding window, rolling)
- **Models**: Direction (arithmetic, negation), DailyState (defaults, 12 fields)
- **Signals — Entry**: Breakout (long/short, condition gating, shorts_enabled), Pullback (crossover, boundary), Momentum (RSI crossover, midline gate), Dual (breakout-first fallthrough), Volume Filter (block/pass/disabled/zero-SMA/equality)
- **Signals — Exit**: Trail-only (always false), Midline (cross detection), Reversal (full conditions only)
- **Config**: SYMBOL_CONFIGS keys, S5_PB/S5_DUAL variants, frozen dataclass, build_instruments

### 11.2 Market Calendar Integration — DONE

**Status: Complete**

`shared/market_calendar.py` provides holiday and half-day awareness using pure Python stdlib (no external dependencies). Year-cached via `@lru_cache`.

**Asset classes:**
- `AssetClass.EQUITY` — NYSE/NASDAQ: 10 holidays/year (New Year's, MLK, Presidents', Good Friday, Memorial, Juneteenth, Independence, Labor, Thanksgiving, Christmas)
- `AssetClass.CME_FUTURES` — CME/COMEX/NYMEX: 7 holidays/year (excludes MLK, Presidents', Juneteenth)
- Half days (3/year): day before Independence Day, Black Friday, Christmas Eve — early close 1:00 PM ET

**Public API:** `MarketCalendar` class with `is_market_holiday()`, `is_half_day()`, `is_trading_day()`, `next_trading_day()`, `market_close_time_et()`, `is_entry_blocked()`.

**Integration points:**
- `shared/oms/risk/gateway.py` — New check between event blackout and session block. Uses `order.instrument.venue` to select EQUITY vs CME_FUTURES calendar, so CME futures aren't blocked on equity-only holidays.
- `shared/oms/services/factory.py` — Both `build_oms_service()` and `build_multi_strategy_oms()` accept optional `market_calendar` parameter, wired to `RiskGateway`.
- Engine schedulers — `strategy_3/engine.py` and `strategy_4/engine.py` daily schedulers skip holidays in addition to weekends. `strategy/engine.py` `_is_rth()` helper checks holidays to avoid bar fetch attempts on closed days.
- `main_multi.py` — Creates one shared `MarketCalendar()` instance, passes to OMS factory and all five engine constructors.

**Tests:** `test_market_calendar.py` (55 tests) — Easter/Good Friday (2024-2030), observed rules (Sat→Fri, Sun→Mon), floating holidays, holiday counts, half-days, entry blocking (holiday/half-day noon cutoff/normal), trading day logic, CME vs equity differences (MLK: equity closed, CME open), year caching, market close times.

### 11.3 Next.js Trading Dashboard — DONE

**Status: Complete**

Purpose-built Next.js 14 dashboard in `infra/dashboard/` replaces Metabase on port 3000. Connects directly to the `trading` PostgreSQL database as `trading_reader`.

**Tech stack:** Next.js 14 (`output: 'standalone'`), TypeScript, Tailwind CSS, Recharts, node-postgres (`pg`).

**File structure:**
```
infra/dashboard/
├── Dockerfile                    ← multi-stage build (deps → builder → runner)
├── package.json                  ← next 14.2, pg 8.12, recharts 2.12, lucide-react
├── next.config.ts                ← output: 'standalone', serverExternalPackages: ['pg']
├── src/app/
│   ├── layout.tsx / page.tsx     ← client component, dual-interval polling (30s / 5min)
│   └── api/                      ← 8 API routes (see §7.3)
├── src/components/               ← 10 React components (see §7.2)
└── src/lib/
    ├── db.ts                     ← pg Pool singleton, NUMERIC/INT8 type parsers
    ├── types.ts                  ← TypeScript interfaces + STRATEGY_CONFIG constants
    └── formatters.ts             ← fmtR, fmtUSD, fmtAge, fmtHoldTime, fmtDate, fmtTime
```

**STRATEGY_CONFIG** (embedded in `types.ts`, authoritative source for dashboard heat/priority display):
```ts
ATRSS:             { maxHeatR: 1.00, riskPct: 1.2, priority: 0 }
S5_PB:             { maxHeatR: 1.50, riskPct: 0.8, priority: 1 }
S5_DUAL:           { maxHeatR: 1.50, riskPct: 0.8, priority: 2 }
SWING_BREAKOUT_V3: { maxHeatR: 0.65, riskPct: 0.5, priority: 3 }
AKC_HELIX:         { maxHeatR: 0.85, riskPct: 0.5, priority: 4 }
```

These match the live risk configs in `main_multi.py` and the backtest `optimized_v2` preset.

See Part 7 for deployment, local dev, and API route details.

### 11.4 Instrumentation & Relay Service — DONE

**Status: Complete (92 tests passing)**

Full instrumentation layer in `instrumentation/` with relay service in `relay/`. See Part 7b and Part 7c for detailed documentation.

**What was built:**
- **8 source modules** (`instrumentation/src/`): event metadata, market snapshots, trade logger, missed opportunity logger (with hypothetical backfill), process quality scorer (21 root-cause taxonomy), daily aggregates, regime classifier (MA/ADX/ATR rules), sidecar forwarder (HMAC-signed relay with watermark tracking)
- **4 config files** (`instrumentation/config/`): central config, per-strategy simulation policies, regime classifier thresholds, process scoring rules with per-strategy overrides
- **Relay service** (`relay/`): FastAPI app with SQLite store, HMAC auth, rate limiting, watermark-based pull/ack, systemd/nginx deployment templates
- **92 tests**: 69 instrumentation (unit + integration lifecycle) + 23 relay (store, auth, rate limiting, full API)
- **Codebase audit** (`instrumentation/audit_report.md`): documented all 5 strategies, 9 filters, exit triggers, 8 hook points

**Remaining**: Wire instrumentation hooks into strategy engines (`main_multi.py` and per-strategy `engine.py` files). The modules are ready — they need to be called from the existing entry/exit/signal/filter code paths.

### 11.5 Automated Data Pipeline

**Priority: Low**

Schedule historical data refresh for backtesting:

- Daily download of OHLCV data for all tracked symbols
- Update parquet cache in `data/` directory
- Run on the VPS or a separate machine (not during trading hours to avoid API rate limits)

```bash
# Example cron (weekdays at 18:00 ET, after market close)
0 18 * * 1-5 cd /opt/trading/swing_trader && python -m backtest download --duration "1 M"
```

### 11.6 Enhanced Monitoring — Slack/Telegram Alerts

**Priority: Medium**

Add real-time alerts for critical events:

- **Fills**: Notify on every fill (entry, exit, partial)
- **Halts**: Notify when any strategy or the portfolio hits a daily stop
- **Disconnections**: Notify when IB Gateway disconnects (beyond the expected daily reset)
- **Errors**: Notify on order rejections, reconciliation discrepancies
- **Daily summary**: End-of-day P&L summary across all strategies

### 11.7 Crash Recovery — State Checkpointing

**Priority: Medium**

Add strategy state persistence to PostgreSQL for crash recovery:

- Checkpoint position state, pending orders, and indicator buffers to the database periodically
- On restart, load the last checkpoint and resume from the correct state
- Currently, strategies rely on IB reconciliation to rebuild state — explicit checkpointing would improve recovery time and accuracy

### 11.8 Strategy 3 Tuning — Resolve TUNE_* Flags

**Priority: Low**

`strategy_3/config.py` has several tuning flags, some reverted to baseline:

| Flag | Status | Notes |
|------|--------|-------|
| `TUNE_COMPRESSION` | `True` | Active — relaxed squeeze/containment thresholds |
| `TUNE_DISPLACEMENT` | `True` | Active — lowered displacement quantile |
| `TUNE_SCORE` | `True` | Active — lowered score threshold |
| `TUNE_ENTRY_UNLOCK` | `True` | Active — relaxed entry gates, neutral regime allowed |
| `TUNE_TP_TARGETS` | `False` | **Reverted** — baseline TPs now achievable with tighter stops |
| `TUNE_REENTRY` | `True` | Active — relaxed re-entry cooldown, DIRTY gates |
| `TUNE_CONTINUATION` | `False` | **Reverted** — blocks Entry A/B by entering continuation too early |
| `TUNE_PORTFOLIO` | `True` | Active — wider portfolio heat/pending/hard block |
| `TUNE_REGIME_MULT` | `False` | **Reverted** — marginal sizing-only, risks larger caution losses |
| `TUNE_STALE` | `False` | **Reverted** — hurts 3/4 symbols (only helps GLD) |

Further backtesting and walk-forward analysis may identify opportunities to re-enable reverted flags or tune active ones.

### 11.9 Multi-Strategy Launcher as Docker Service

**Priority: Low**

`main_multi.py` runs all five live strategies (ATRSS, S5_PB, S5_DUAL, Breakout, Helix) in one process with a shared OMS, `StrategyCoordinator`, and `MarketCalendar`. To add the multi-strategy launcher as a Docker Compose profile:

```yaml
# Add to infra/docker-compose.yml
  multi_strategy:
    profiles: ["multi"]
    build:
      context: ..
      dockerfile: Dockerfile
    container_name: trading_multi
    restart: unless-stopped
    command: ["python", "main_multi.py"]
    env_file:
      - ../.env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - trading_net
```

**Trade-off**: Single-process mode enables cross-strategy coordination (shared `StrategyCoordinator`) but creates a single point of failure. Separate containers are more resilient but lose real-time coordination (ATRSS entry → Helix stop tightening).

---

## Part 12: Operational Runbook

### 12.1 Common Operations

| Action | Command |
|--------|---------|
| **Restart a strategy** | `docker compose -f infra/docker-compose.yml --profile atrss restart atrss` |
| **Stop all strategies** | `docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix --profile swing_breakout down` |
| **Stop everything** | `docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix --profile swing_breakout down && sudo systemctl stop ibgateway && sudo systemctl stop trading-relay` |
| **Start everything** | `sudo systemctl start ibgateway && sudo systemctl start trading-relay && sleep 60 && docker compose -f infra/docker-compose.yml up -d && docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix --profile swing_breakout up -d` |
| **View all logs** | `docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix --profile swing_breakout logs -f --tail=100` |
| **View single strategy log** | `docker compose -f infra/docker-compose.yml --profile atrss logs -f atrss` |
| **View dashboard logs** | `docker compose -f infra/docker-compose.yml logs -f dashboard` |
| **Rebuild strategies after code changes** | `git pull && docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix --profile swing_breakout build && docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix --profile swing_breakout up -d` |
| **Rebuild dashboard after code changes** | `git pull && docker compose -f infra/docker-compose.yml build dashboard && docker compose -f infra/docker-compose.yml restart dashboard` |
| **Check IB Gateway status** | `sudo systemctl status ibgateway` |
| **Check IB Gateway logs** | `sudo journalctl -u ibgateway --no-pager -n 30` |
| **Check database health** | `docker exec trading_postgres pg_isready -U trading_admin -d trading` |
| **Query strategy state** | `docker exec -it trading_postgres psql -U trading_admin -d trading -c "SELECT * FROM strategy_state;"` |
| **Query today's trades** | `docker exec -it trading_postgres psql -U trading_admin -d trading -c "SELECT * FROM v_today_trades;"` |
| **Query working orders** | `docker exec -it trading_postgres psql -U trading_admin -d trading -c "SELECT * FROM v_working_orders;"` |
| **Manual DB backup** | `/opt/trading/backup-db.sh` |
| **Check relay status** | `sudo systemctl status trading-relay` |
| **View relay logs** | `journalctl -u trading-relay --since "1 hour ago"` |
| **Restart relay** | `sudo systemctl restart trading-relay` |
| **Check relay DB size** | `du -h /opt/trading-relay/data/relay.db` |
| **Purge acked relay events** | `sqlite3 /opt/trading-relay/data/relay.db "DELETE FROM events WHERE acked = 1; VACUUM;"` |
| **Check relay pending events** | `curl -s http://127.0.0.1:8001/health` |
| **View today's instrumentation trades** | `cat instrumentation/data/trades/trades_$(date +%Y-%m-%d).jsonl \| python -m json.tool --no-ensure-ascii` |
| **View today's daily snapshot** | `cat instrumentation/data/daily/daily_$(date +%Y-%m-%d).json \| python -m json.tool` |
| **Run instrumentation tests** | `PYTHONPATH="$(pwd):$PYTHONPATH" python -m pytest instrumentation/tests/ relay/tests/ -v` |

### 12.2 Troubleshooting Guide

| Problem | Diagnosis | Solution |
|---------|-----------|----------|
| **Strategy can't connect to IB Gateway** | `ss -tlnp \| grep 4002` (port not listening) | Check `sudo systemctl status ibgateway`. Restart: `sudo systemctl restart ibgateway`. Verify `extra_hosts` in docker-compose. |
| **IB Gateway won't start** | `journalctl -u ibgateway -n 100` | Check Java (`java -version`), credentials in `/opt/ibc/config/config.ini`, Xvfb (`ps aux \| grep Xvfb`). |
| **Database connection refused** | `docker compose -f infra/docker-compose.yml ps` — postgres not healthy | Restart postgres: `docker compose -f infra/docker-compose.yml restart postgres`. Check `POSTGRES_PASSWORD` matches `.env`. |
| **Dashboard shows "Database error"** | API route returns 500 | Check `docker compose logs dashboard`. Verify `trading_reader` password: `docker exec -it trading_postgres psql -U trading_admin -d trading -c "ALTER USER trading_reader WITH PASSWORD 'correct_password';"`. Ensure OMS has run `PgStore.init_schema()` to create tables. |
| **Dashboard shows empty strategy grid** | No rows in `strategy_state` | OMS has not written a heartbeat yet. Start a strategy container and wait for its first heartbeat cycle. |
| **IB Gateway disconnects overnight** | Expected — IBKR daily reset ~midnight ET | `AutoRestartTime=00:00` handles reconnection. Strategies have `restart: unless-stopped`. Check `disconnect_count_24h` in `adapter_state`. |
| **"No security definition found"** | IB error code 200 | Market may be closed. Paper data is 15min delayed and unavailable outside hours. For futures, check contract expiry (roll to next front month). |
| **Orders rejected by risk gateway** | Strategy logs show `RISK_REJECTED` | Check `risk_daily_strategy` for halt status. Check heat cap: `SELECT * FROM strategy_state WHERE heat_r > 0;`. Verify `unit_risk_dollars` is computed correctly. |
| **Strategy shows HALTED mode** | `strategy_state.mode = 'HALTED'` | Strategy hit `daily_stop_R`. Will auto-resume next trading day. To manually clear (use caution): update `mode` in DB. |
| **Stale heartbeats** | `heartbeat_age_sec > 300` in dashboard strategy cards | Strategy may be stuck. Check logs for errors. Restart the strategy container. |
| **Disk space running low** | Docker images + DB growth | Run `docker system prune -f`. Check `docker volume ls`. Verify retention cron is running. |
| **Relay returns 401** | HMAC signature mismatch | Verify `INSTRUMENTATION_HMAC_SECRET` matches the bot's entry in `/opt/trading-relay/secrets.json`. Ensure sidecar uses `sort_keys=True` canonicalization. |
| **Relay not accepting events** | `systemctl status trading-relay` shows inactive | Restart: `sudo systemctl restart trading-relay`. Check logs: `journalctl -u trading-relay -n 50`. |
| **Sidecar not forwarding** | Events in JSONL but not in relay | Check sidecar config `relay_url` in `instrumentation_config.yaml`. Verify relay is reachable: `curl http://127.0.0.1:8001/health`. |
| **Missing instrumentation data** | No JSONL files in `instrumentation/data/` | Instrumentation not wired into strategy engine yet. Logger modules must be called from entry/exit hooks. |
| **Relay DB growing large** | `du -h /opt/trading-relay/data/relay.db` shows >1GB | Purge acked events: `sqlite3 relay.db "DELETE FROM events WHERE acked = 1; VACUUM;"`. Check home orchestrator is acking. |

### 12.3 Emergency Procedures

#### Global Standdown (Stop All New Entries)

```bash
# Set global standdown flag in database
docker exec -it trading_postgres psql -U trading_admin -d trading -c \
  "UPDATE strategy_state SET mode = 'STAND_DOWN', stand_down_reason = 'Manual emergency standdown' WHERE mode = 'RUNNING';"
```

Strategies will stop entering new positions but continue managing existing ones (stops, trailing, exits).

#### Flatten All Positions

If you need to close everything immediately:

1. **Via IB Gateway/TWS directly**: Log into the IB Gateway web interface or TWS and close all positions manually — this is the fastest and most reliable method.

2. **Via strategy**: Each strategy's `FLATTEN` intent type closes all positions for that strategy through the OMS.

#### Full System Shutdown

```bash
# Stop strategies first (allows graceful shutdown)
docker compose -f infra/docker-compose.yml --profile atrss --profile akc_helix --profile swing_breakout down

# Wait for containers to stop
sleep 10

# Stop IB Gateway
sudo systemctl stop ibgateway

# Stop relay service
sudo systemctl stop trading-relay

# Stop infrastructure (optional — keeps DB available for queries)
docker compose -f infra/docker-compose.yml down
```

### 12.4 Code Update Workflow

```bash
cd /opt/trading/swing_trader

# Pull latest code
git pull

# Rebuild strategy images
docker compose -f infra/docker-compose.yml \
  --profile atrss --profile akc_helix --profile swing_breakout build

# Rolling restart (one at a time to minimize downtime)
docker compose -f infra/docker-compose.yml --profile atrss restart atrss
sleep 30
docker compose -f infra/docker-compose.yml --profile akc_helix restart akc_helix
sleep 30
docker compose -f infra/docker-compose.yml --profile swing_breakout restart swing_breakout

# Verify
docker compose -f infra/docker-compose.yml \
  --profile atrss --profile akc_helix --profile swing_breakout ps
```

**Important**: Avoid updating during market hours when positions are open. If you must, the OMS reconciliation will re-sync state on restart, but there's a brief window where stops may not be monitored.

---

## Part 13: Key Configuration Reference

### 13.1 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SWING_TRADER_ENV` | `paper` | Environment mode: `dev`, `backtest`, `paper`, `live` |
| `IB_ACCOUNT_ID` | `DU_PLACEHOLDER` | IBKR account ID (DU prefix = paper, U prefix = live) |
| `IB_HOST` | `host.docker.internal` | IB Gateway hostname (from Docker container's perspective) |
| `IB_PORT` | `4002` | IB Gateway port (4002 = paper, 4001 = live) |
| `POSTGRES_PASSWORD` | `changeme` | PostgreSQL admin password |
| `POSTGRES_READER_PASSWORD` | `changeme` | Read-only user password (trading dashboard) |
| `POSTGRES_WRITER_PASSWORD` | `changeme` | Writer user password (OMS) |
| `DB_HOST` | `postgres` | PostgreSQL host (Docker service name) |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `trading` | Database name |
| `DB_USER` | `trading_writer` | Database user for OMS writes |
| `DB_PASSWORD` | `changeme` | Database password for OMS writes |
| `ATRSS_SYMBOL_SET` | `etf` | ATRSS symbols: `etf` (QQQ,GLD), `micro` (MNQ,MCL,MGC,MBT), `full` (NQ,CL,GC,BRR), `all` |
| `AKCHELIX_SYMBOL_SET` | `etf` | Helix symbols: `etf` (QQQ,GLD,IBIT), `micro_futures`, `full_futures`, `all` |
| `INSTRUMENTATION_HMAC_SECRET` | — | HMAC-SHA256 shared secret for sidecar → relay signing |
| `RELAY_SECRETS_FILE` | `/opt/trading-relay/secrets.json` | Path to bot_id → HMAC secret mapping (relay service) |
| `RELAY_DB_PATH` | `/opt/trading-relay/data/relay.db` | SQLite database path for relay event buffer |
| `RELAY_URL` | — | Relay URL for home orchestrator (e.g., `https://relay.yourdomain.com`) |

### 13.2 Risk Parameters (optimized_v2)

**Per-Strategy (from `main_multi.py` / `backtest/config_unified.py`):**

| Parameter | ATRSS | S5_PB | S5_DUAL | SWING_BREAKOUT_V3 | AKC_HELIX | Description |
|-----------|-------|-------|---------|-------------------|-----------|-------------|
| `unit_risk_pct` | **1.20%** | 0.80% | 0.80% | 0.50% | 0.50% | Base risk per trade as % of NAV |
| `daily_stop_R` | 2.0 | 2.0 | 2.0 | 2.0 | 2.5 | Max daily loss in R before strategy halts |
| `max_heat_R` | 1.00 | 1.50 | 1.50 | 0.65 | 0.85 | Per-strategy heat ceiling (max open risk in R) |
| `max_working_orders` | 4 | 2 | 2 | 2 | 4 | Max concurrent working orders |
| `priority` | 0 | 1 | 2 | 3 | 4 | Priority for heat reservation (0 = highest) |

**Portfolio-Level:**

| Parameter | Value | Description |
|-----------|-------|-------------|
| `heat_cap_R` | **2.0** | Total open risk across all strategies in R |
| `portfolio_daily_stop_R` | 3.0 | Portfolio-wide daily loss limit in R |

**Per-Symbol Risk (ATRSS `base_risk_pct`):**

| Symbol | base_risk_pct | Notes |
|--------|--------------|-------|
| QQQ | 0.60% | Shorts disabled, Dec size reduction 50% |
| GLD | 0.65% | Shorts disabled |
| MNQ | 1.00% | Default |
| MCL | 1.00% | Higher slippage tolerance |
| MGC | 1.00% | Default |
| MBT | 0.75% | Reduced due to volatility |

### 13.3 Symbol Sets and Supported Instruments

**ETFs:**

| Symbol | Exchange | Description | Used By |
|--------|----------|-------------|---------|
| QQQ | SMART/NASDAQ | Nasdaq 100 ETF | ATRSS, Helix, Breakout |
| GLD | SMART/ARCA | Gold ETF | ATRSS, Helix, Breakout, S5_DUAL |
| USO | SMART/ARCA | Oil ETF | Helix, Breakout |
| IBIT | SMART/NASDAQ | Bitcoin ETF | Helix, Breakout, S5_PB, S5_DUAL |

**Micro Futures:**

| Symbol | Exchange | Multiplier | Tick Size | Used By |
|--------|----------|------------|-----------|---------|
| MNQ | CME | 2.0 | 0.25 | ATRSS, Helix |
| MCL | NYMEX | 100.0 | 0.01 | ATRSS, Helix |
| MGC | COMEX | 10.0 | 0.10 | ATRSS, Helix |
| MBT | CME | 0.1 | 5.0 | ATRSS, Helix |

**Full-Size Futures:**

| Symbol | Exchange | Multiplier | Tick Size | Used By |
|--------|----------|------------|-----------|---------|
| NQ | CME | 20.0 | 0.25 | ATRSS, Helix |
| CL | NYMEX | 1000.0 | 0.01 | ATRSS, Helix |
| GC | COMEX | 100.0 | 0.10 | ATRSS, Helix |
| BT | CME | 5.0 | 5.0 | Helix |

### 13.4 Important File Paths (VPS)

| Path | Purpose |
|------|---------|
| `/opt/trading/swing_trader/` | Application root |
| `/opt/trading/swing_trader/.env` | Environment configuration |
| `/opt/trading/swing_trader/infra/docker-compose.yml` | Docker service definitions |
| `/opt/ibgateway/` | IB Gateway installation |
| `/opt/ibc/` | IBC installation |
| `/opt/ibc/config/config.ini` | IBC credentials and settings |
| `/etc/systemd/system/ibgateway.service` | systemd service for IB Gateway |
| `/opt/trading/swing_trader/instrumentation/data/` | Instrumentation JSONL event files |
| `/opt/trading/swing_trader/instrumentation/config/` | Instrumentation configuration |
| `/opt/trading-relay/` | Relay service installation |
| `/opt/trading-relay/data/relay.db` | Relay SQLite event buffer |
| `/opt/trading-relay/secrets.json` | Bot HMAC shared secrets |
| `/etc/systemd/system/trading-relay.service` | systemd service for relay |
| `/var/log/trading/` | Application logs (retention, backups) |
| `/opt/trading/backups/` | Database backups |

### 13.5 Database Roles

| Role | Permissions | Used By |
|------|-------------|---------|
| `trading_admin` | Superuser (creates tables, manages roles) | PostgreSQL admin, init-db.sql |
| `trading_writer` | SELECT, INSERT, UPDATE, DELETE on `public` schema | OMS, strategy containers |
| `trading_reader` | SELECT only on `public` schema | Trading dashboard (Next.js API routes) |

### 13.6 Docker Services

| Service | Container Name | Profile | Port | Image |
|---------|----------------|---------|------|-------|
| postgres | `trading_postgres` | (always) | 5432 | `postgres:16-alpine` |
| dashboard | `trading_dashboard` | (always) | 3000 | Built from `infra/dashboard/Dockerfile` |
| atrss | `trading_atrss` | `atrss` | — | Built from `Dockerfile` |
| akc_helix | `trading_akc_helix` | `akc_helix` | — | Built from `Dockerfile` |
| swing_breakout | `trading_swing_breakout` | `swing_breakout` | — | Built from `Dockerfile` |

**Non-Docker Services (systemd):**

| Service | Unit File | Port | Description |
|---------|-----------|------|-------------|
| IB Gateway | `ibgateway.service` | 4002 | Headless IB Gateway via IBC + Xvfb |
| Trading Relay | `trading-relay.service` | 8001 | FastAPI event buffer (SQLite-backed) |
