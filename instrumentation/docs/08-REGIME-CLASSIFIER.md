# Task 8: Add Regime Classifier

## Goal

Create a simple, deterministic market regime classifier that tags each trade and snapshot. This is not a machine learning model — it's a rules-based classifier using standard technical indicators.

The regime tag drives downstream analysis: Claude uses it to determine whether a strategy was applied in the right conditions.

## Regime Definitions

```
trending_up:    Price above 50-period MA, ADX > 25, MA slope positive
trending_down:  Price below 50-period MA, ADX > 25, MA slope negative
ranging:        ADX < 20, ATR percentile below 60th
volatile:       ATR percentile above 80th, regardless of trend
```

These are intentionally simple. Complexity here is the enemy — you want consistent, explainable labels.

## Implementation

```python
# instrumentation/src/regime_classifier.py

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List
from collections import deque


@dataclass
class RegimeConfig:
    """Thresholds for regime classification. Loaded from YAML."""
    ma_period: int = 50
    adx_period: int = 14
    adx_trend_threshold: float = 25.0
    adx_range_threshold: float = 20.0
    atr_period: int = 14
    atr_volatile_percentile: float = 80.0
    atr_lookback_bars: int = 100     # bars to compute ATR percentile over
    slope_lookback_bars: int = 5      # bars to compute MA slope


class RegimeClassifier:
    """
    Deterministic market regime classifier.

    Usage:
        classifier = RegimeClassifier(config, data_provider)
        regime = classifier.classify("BTC/USDT")
        # Returns: "trending_up" | "trending_down" | "ranging" | "volatile"
    """

    def __init__(self, config_path: str = "instrumentation/config/regime_classifier_config.yaml",
                 data_provider=None):
        """
        Args:
            config_path: path to regime classifier config
            data_provider: bot's market data provider
                Must support: get_ohlcv(symbol, timeframe, limit)
                ADAPT: if your bot uses a different API, modify _fetch_candles
        """
        self.data_provider = data_provider
        self.config = self._load_config(config_path)
        self._cache = {}  # symbol -> (timestamp, regime)

    def _load_config(self, path: str) -> RegimeConfig:
        config_file = Path(path)
        if config_file.exists():
            with open(config_file) as f:
                raw = yaml.safe_load(f) or {}
            return RegimeConfig(**{k: v for k, v in raw.items() if k in RegimeConfig.__dataclass_fields__})
        return RegimeConfig()

    def classify(self, symbol: str, timeframe: str = "1h") -> str:
        """
        Classify the current market regime for a symbol.

        Returns one of: "trending_up", "trending_down", "ranging", "volatile"
        Falls back to "unknown" if insufficient data.
        """
        try:
            candles = self._fetch_candles(symbol, timeframe)
            if not candles or len(candles) < self.config.atr_lookback_bars:
                return "unknown"

            closes = [c[4] for c in candles]
            highs = [c[2] for c in candles]
            lows = [c[3] for c in candles]

            # Current price vs MA
            ma = sum(closes[-self.config.ma_period:]) / self.config.ma_period
            current_price = closes[-1]
            above_ma = current_price > ma

            # MA slope (positive = up, negative = down)
            ma_recent = sum(closes[-self.config.slope_lookback_bars:]) / self.config.slope_lookback_bars
            ma_prior = sum(closes[-(self.config.slope_lookback_bars + 5):-5]) / self.config.slope_lookback_bars
            ma_slope_positive = ma_recent > ma_prior

            # ADX (simplified: using directional movement)
            adx = self._compute_adx(highs, lows, closes, self.config.adx_period)

            # ATR percentile
            atrs = self._compute_atr_series(highs, lows, closes, self.config.atr_period)
            current_atr = atrs[-1] if atrs else 0
            atr_percentile = self._percentile_rank(atrs, current_atr)

            # Classification logic
            if atr_percentile >= self.config.atr_volatile_percentile:
                regime = "volatile"
            elif adx >= self.config.adx_trend_threshold:
                if above_ma and ma_slope_positive:
                    regime = "trending_up"
                elif not above_ma and not ma_slope_positive:
                    regime = "trending_down"
                else:
                    regime = "ranging"  # conflicting signals
            elif adx < self.config.adx_range_threshold:
                regime = "ranging"
            else:
                # ADX between range and trend thresholds
                if above_ma and ma_slope_positive:
                    regime = "trending_up"
                elif not above_ma and not ma_slope_positive:
                    regime = "trending_down"
                else:
                    regime = "ranging"

            self._cache[symbol] = regime
            return regime

        except Exception:
            return self._cache.get(symbol, "unknown")

    def current_regime(self, symbol: str) -> str:
        """Get the most recently computed regime (cached)."""
        return self._cache.get(symbol, "unknown")

    def _fetch_candles(self, symbol: str, timeframe: str) -> list:
        """
        ADAPT: replace with your bot's actual candle fetching.
        Must return list of [timestamp, open, high, low, close, volume].
        """
        limit = max(self.config.atr_lookback_bars, self.config.ma_period) + 20
        return self.data_provider.get_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def _compute_adx(self, highs: list, lows: list, closes: list, period: int) -> float:
        """Simplified ADX calculation."""
        if len(highs) < period + 1:
            return 0

        plus_dm = []
        minus_dm = []
        tr = []

        for i in range(1, len(highs)):
            up = highs[i] - highs[i-1]
            down = lows[i-1] - lows[i]

            plus_dm.append(up if up > down and up > 0 else 0)
            minus_dm.append(down if down > up and down > 0 else 0)

            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            tr.append(max(hl, hc, lc))

        # Smoothed averages (Wilder's method)
        def wilder_smooth(data, period):
            if len(data) < period:
                return [0]
            result = [sum(data[:period]) / period]
            for i in range(period, len(data)):
                result.append((result[-1] * (period - 1) + data[i]) / period)
            return result

        smoothed_tr = wilder_smooth(tr, period)
        smoothed_plus = wilder_smooth(plus_dm, period)
        smoothed_minus = wilder_smooth(minus_dm, period)

        if not smoothed_tr or smoothed_tr[-1] == 0:
            return 0

        plus_di = smoothed_plus[-1] / smoothed_tr[-1] * 100
        minus_di = smoothed_minus[-1] / smoothed_tr[-1] * 100

        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0

        dx = abs(plus_di - minus_di) / di_sum * 100
        return dx  # single-point DX as ADX approximation

    def _compute_atr_series(self, highs: list, lows: list, closes: list, period: int) -> list:
        """Compute ATR series."""
        if len(highs) < period + 1:
            return []
        trs = []
        for i in range(1, len(highs)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            trs.append(max(hl, hc, lc))

        atrs = []
        if len(trs) >= period:
            atr = sum(trs[:period]) / period
            atrs.append(atr)
            for i in range(period, len(trs)):
                atr = (atr * (period - 1) + trs[i]) / period
                atrs.append(atr)
        return atrs

    def _percentile_rank(self, series: list, value: float) -> float:
        """Compute percentile rank of value within series."""
        if not series:
            return 50.0
        lookback = series[-self.config.atr_lookback_bars:]
        count_below = sum(1 for v in lookback if v < value)
        return (count_below / len(lookback)) * 100
```

Create `instrumentation/config/regime_classifier_config.yaml`:

```yaml
# ADAPT: tune these thresholds for your markets and timeframes
ma_period: 50
adx_period: 14
adx_trend_threshold: 25.0
adx_range_threshold: 20.0
atr_period: 14
atr_volatile_percentile: 80.0
atr_lookback_bars: 100
slope_lookback_bars: 5
```

### Integration

1. Initialize once at bot startup
2. Call `classify()` periodically (e.g., every snapshot interval) or on-demand at trade/signal time
3. Pass the result to `trade_logger.log_entry()` and `missed_logger.log_missed()` as `market_regime`

```python
# At bot startup:
regime_classifier = RegimeClassifier(
    config_path="instrumentation/config/regime_classifier_config.yaml",
    data_provider=bot.exchange
)

# When logging a trade entry:
regime = regime_classifier.classify(pair)  # or .current_regime(pair) if recently computed
trade_logger.log_entry(..., market_regime=regime)
```

---

## Done Criteria

- [ ] `instrumentation/src/regime_classifier.py` exists
- [ ] `instrumentation/config/regime_classifier_config.yaml` exists
- [ ] `_fetch_candles` adapted to this bot's data API
- [ ] Classification returns one of the four valid regimes or "unknown"
- [ ] Never crashes — returns cached value or "unknown" on failure
- [ ] Test with known market conditions (trend day should classify as trending_up/down)
