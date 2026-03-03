// ── Strategy constants ──────────────────────────────────────────────────────
export const STRATEGY_CONFIG: Record<
  string,
  { maxHeatR: number; riskPct: number; priority: number }
> = {
  ATRSS:             { maxHeatR: 1.00, riskPct: 1.2, priority: 0 },
  S5_PB:             { maxHeatR: 1.50, riskPct: 0.8, priority: 1 },
  S5_DUAL:           { maxHeatR: 1.50, riskPct: 0.8, priority: 2 },
  SWING_BREAKOUT_V3: { maxHeatR: 0.65, riskPct: 0.5, priority: 3 },
  AKC_HELIX:         { maxHeatR: 0.85, riskPct: 0.5, priority: 4 },
};

export const PORTFOLIO_HEAT_CAP = 2.0;
export const PORTFOLIO_DAILY_STOP = 3.0;

// ── API Response Types ──────────────────────────────────────────────────────

export interface PortfolioData {
  daily_realized_r: number;
  daily_realized_usd: number;
  portfolio_open_risk_r: number;
  unrealized_pnl: number;
  halted: boolean;
  halt_reason: string | null;
  heat_r: number; // sum of strategy heat_r from positions
}

export interface StrategyData {
  strategy_id: string;
  mode: string;
  last_heartbeat_ts: string | null;
  heartbeat_age_sec: number;
  health_status: string;
  heat_r: number;
  daily_pnl_r: number;
  last_error: string | null;
  last_error_ts: string | null;
  // from risk_daily_strategy
  daily_realized_r: number;
  daily_realized_usd: number;
  open_risk_r: number;
  filled_entries: number;
  halted: boolean;
  halt_reason: string | null;
}

export interface PositionRow {
  account_id: string;
  instrument_symbol: string;
  strategy_id: string;
  net_qty: number;
  avg_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  open_risk_dollars: number;
  open_risk_r: number;
  last_update_at: string;
  stale_minutes: number;
}

export interface TradeRow {
  trade_id: string;
  strategy_id: string;
  instrument_symbol: string;
  direction: string;
  quantity: number;
  entry_ts: string;
  entry_price: number;
  exit_ts: string | null;
  exit_price: number | null;
  realized_r: number | null;
  exit_reason: string | null;
  entry_type: string | null;
  mae_r: number | null;
  mfe_r: number | null;
  duration_minutes: number | null;
}

export interface OrderRow {
  oms_order_id: string;
  strategy_id: string;
  instrument_symbol: string;
  role: string;
  side: string;
  qty: number;
  filled_qty: number;
  stop_price: number | null;
  limit_price: number | null;
  status: string;
  broker_order_id: string | null;
  created_at: string;
  age_minutes: number;
}

export interface HealthData {
  strategies: StrategyHealthRow[];
  adapters: AdapterHealthRow[];
  halts: HaltRow[];
}

export interface StrategyHealthRow {
  strategy_id: string;
  mode: string;
  last_heartbeat_ts: string | null;
  heartbeat_age_sec: number;
  health_status: string;
  heat_r: number;
  daily_pnl_r: number;
  last_error: string | null;
  last_error_ts: string | null;
}

export interface AdapterHealthRow {
  adapter_id: string;
  broker: string;
  connected: boolean;
  last_heartbeat_ts: string | null;
  heartbeat_age_sec: number;
  health_status: string;
  disconnect_count_24h: number;
  last_error_code: string | null;
  last_error_message: string | null;
}

export interface HaltRow {
  halt_level: string;
  entity: string;
  halt_reason: string | null;
  last_update_at: string;
}

export interface EquityCurvePoint {
  trade_date: string;
  daily_realized_r: number;
  cumulative_r: number;
}

export interface DailyPnlPoint {
  trade_date: string;
  daily_realized_r: number;
}

export interface EnvData {
  mode: 'paper' | 'live' | 'dev';
  account_id: string;
  ib_port: number;
}
