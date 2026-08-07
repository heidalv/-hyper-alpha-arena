# Prompt Variables Reference

This document lists all available variables you can use in your prompt templates.

---

## Required Variables

| Variable | Description |
|----------|-------------|
| `{output_format}` | **MUST INCLUDE** - JSON output schema and format requirements. Ensures AI returns valid, parseable JSON. |
| `{trigger_context}` | **RECOMMENDED** - Trigger context information. Tells AI what triggered this decision (signal or scheduled) and provides signal details when triggered by signal pool. |

### Trigger Context Format

When triggered by **signal pool**:
```
=== TRIGGER CONTEXT ===
trigger_type: signal
signal_pool_name: OI Surge Monitor
pool_logic: OR
trigger_symbol: BTC
triggered_signals:
  - name: OI Delta Signal
    metric: oi_delta_percent
    condition: > 2.0
    current_value: 3.5
```

When triggered by **scheduled interval**:
```
=== TRIGGER CONTEXT ===
trigger_type: scheduled
trigger_interval: 150 minutes
```

**Why include this?** The AI can make more informed decisions when it knows:
- What triggered the analysis (signal vs scheduled check)
- Which specific signals fired and their values
- The market conditions that caused the trigger

---

## Basic Variables (All Templates)

| Variable | Description | Example |
|----------|-------------|---------|
| `{trading_environment}` | Current trading mode description | "Platform: Hyperliquid Perpetual Contracts \| Environment: MAINNET" |
| `{available_cash}` | Available cash (formatted USD) | "$10,000.00" |
| `{total_account_value}` | Total account value (formatted USD) | "$12,500.00" |
| `{market_prices}` | Current prices for all symbols (readable format) | "BTC: $50,000.00\nETH: $3,000.00" |
| `{news_section}` | Latest crypto news summary | "Bitcoin ETF inflows reach..." |
| `{max_leverage}` | Maximum allowed leverage | "10" |

---

## Session Variables (Pro Template)

| Variable | Description | Example |
|----------|-------------|---------|
| `{runtime_minutes}` | Minutes since trading started | "120" |
| `{current_time_utc}` | Current UTC timestamp | "2025-01-15T08:30:00Z" |
| `{total_return_percent}` | Total return percentage | "+5.25" |

---

## Portfolio Variables (Pro Template)

| Variable | Description | Example |
|----------|-------------|---------|
| `{holdings_detail}` | Detailed holdings with quantity, cost, value | "BTC: 0.5 units @ $48,000 avg (current value: $25,000)" |
| `{sampling_data}` | Intraday price series for all symbols | Multi-line price history data |
| `{margin_info}` | Margin mode information (Hyperliquid only) | "Margin Mode: Cross margin" |

---

## Hyperliquid Variables (Real Trading)

| Variable | Description | Example |
|----------|-------------|---------|
| `{environment}` | Trading environment | "mainnet" or "testnet" |
| `{total_equity}` | Total equity in USDC | "$10,500.00" |
| `{available_balance}` | Available balance for trading | "$8,000.00" |
| `{used_margin}` | Margin currently in use | "$2,500.00" |
| `{margin_usage_percent}` | Margin usage percentage | "23.8" |
| `{maintenance_margin}` | Maintenance margin requirement | "$500.00" |
| `{positions_detail}` | Detailed open positions with leverage, liquidation price, PnL | Full position breakdown |
| `{recent_trades_summary}` | Recent closed trades history | Last 5 closed positions |
| `{default_leverage}` | Default leverage setting | "3" |
| `{real_trading_warning}` | Risk warning message | "REAL MONEY TRADING - All decisions execute on live markets" |
| `{operational_constraints}` | Trading constraints and risk rules | Position size limits, margin rules |
| `{leverage_constraints}` | Leverage-specific constraints | "Leverage range: 1x to 10x" |

---

## Symbol Selection Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{selected_symbols_csv}` | Comma-separated symbol list | "BTC, ETH, SOL" |
| `{selected_symbols_detail}` | Detailed symbol information | "BTC: Bitcoin\nETH: Ethereum" |
| `{selected_symbols_count}` | Number of selected symbols | "6" |

---

## K-line and Technical Indicator Variables (Advanced)

These variables are dynamically generated based on your template. Add them to get technical analysis data.

### Syntax

**IMPORTANT**: `SYMBOL` is a placeholder - you must replace it with actual symbol names (BTC, ETH, SOL, etc.). The system will NOT auto-replace `SYMBOL` for you.

| Pattern | You Must Write | Example |
|---------|----------------|---------|
| `SYMBOL_klines_PERIOD` | Replace SYMBOL with actual symbol | `{BTC_klines_15m}`, `{ETH_klines_1h}` |
| `SYMBOL_market_data` | Replace SYMBOL with actual symbol | `{BTC_market_data}`, `{SOL_market_data}` |
| `SYMBOL_INDICATOR_PERIOD` | Replace SYMBOL with actual symbol | `{BTC_RSI14_15m}`, `{ETH_MACD_1h}` |

**Wrong** (will show nothing):
```
{SYMBOL_market_data}
{SYMBOL_RSI14_15m}
```

**Correct** (will show actual data):
```
{BTC_market_data}
{BTC_RSI14_15m}
```

### Supported Periods

`1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M`

### Supported Indicators

| Indicator | Variable Example | Description |
|-----------|------------------|-------------|
| RSI (14) | `{BTC_RSI14_15m}` | Relative Strength Index (14-period) |
| RSI (7) | `{BTC_RSI7_15m}` | Relative Strength Index (7-period, faster) |
| MACD | `{BTC_MACD_15m}` | MACD line, signal line, histogram |
| Stochastic | `{BTC_STOCH_15m}` | Stochastic Oscillator (%K and %D) |
| MA | `{BTC_MA_15m}` | Moving Averages (MA5, MA10, MA20) |
| EMA | `{BTC_EMA_15m}` | Exponential MAs (EMA20, EMA50, EMA100) |
| Bollinger | `{BTC_BOLL_15m}` | Bollinger Bands (upper, middle, lower) |
| ATR | `{BTC_ATR14_15m}` | Average True Range (volatility) |
| VWAP | `{BTC_VWAP_15m}` | Volume Weighted Average Price |
| OBV | `{BTC_OBV_15m}` | On-Balance Volume |

### Example Usage

To add BTC technical analysis to your prompt:

```
=== TECHNICAL ANALYSIS ===
{BTC_market_data}
{BTC_klines_15m}
{BTC_RSI14_15m}
{BTC_MACD_15m}
```

---

## Market Flow Indicator Variables (Advanced)

Market flow indicators provide insights into order flow, volume delta, and market microstructure.

### Supported Flow Indicators

| Indicator | Variable Example | Description |
|-----------|------------------|-------------|
| CVD | `{BTC_CVD_15m}` | Cumulative Volume Delta (Taker Buy - Sell) |
| TAKER | `{BTC_TAKER_15m}` | Taker Buy/Sell Volume and Ratio |
| OI | `{BTC_OI_15m}` | Open Interest (absolute value) |
| OI_DELTA | `{BTC_OI_DELTA_15m}` | Open Interest Change % |
| FUNDING | `{BTC_FUNDING_15m}` | Funding Rate (current and annualized) |
| DEPTH | `{BTC_DEPTH_15m}` | Order Book Depth Ratio (Bid/Ask) |
| IMBALANCE | `{BTC_IMBALANCE_15m}` | Order Book Imbalance (-1 to 1) |

### Supported Periods

`1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`

### Output Format Examples

**CVD (Cumulative Volume Delta)**
```
CVD (15m): +$2.34M
CVD last 5: -$0.50M, +$0.80M, +$1.20M, +$0.30M, +$0.54M
Cumulative: +$8.70M
```

**TAKER (Taker Volume)**
```
Taker Buy: +$5.20M | Taker Sell: +$2.86M
Buy/Sell Ratio: 1.82x (log: +0.60)
Log Ratio last 5: -0.05, +0.11, +0.37, +0.21, +0.60
```
Note: Log ratio = ln(buy/sell). Positive = buyers dominate, negative = sellers dominate.
- +0.69 = buyers 2x sellers
- 0 = balanced
- -0.69 = sellers 2x buyers

**OI (Open Interest)**
```
Open Interest: +$150.00M
OI last 5: +$148.50M, +$149.00M, +$149.30M, +$149.80M, +$150.00M
```

**OI_DELTA (Open Interest Change)**
```
OI Delta (15m): +1.20%
OI Delta last 5: -0.30%, +0.50%, +0.80%, +0.10%, +1.20%
```

**FUNDING (Funding Rate)**
```
Funding Rate: 0.0125%
Annualized: 45.63%
Funding last 5: 0.0080%, 0.0100%, 0.0110%, 0.0120%, 0.0125%
```

**DEPTH (Order Book Depth)**
```
Bid Depth: +$10.50M | Ask Depth: +$8.20M
Depth Ratio (Bid/Ask): 1.28
Ratio last 5: 1.12, 1.18, 1.25, 1.30, 1.28
Spread: 0.0100
```

**IMBALANCE (Order Book Imbalance)**
```
Order Imbalance: +0.125
Imbalance last 5: +0.050, +0.080, +0.100, +0.130, +0.125
```

### Example Usage

To add market flow analysis to your prompt:

```
=== MARKET FLOW ANALYSIS ===
{BTC_CVD_15m}
{BTC_TAKER_15m}
{BTC_OI_1h}
{BTC_FUNDING_1h}
```

---

## Market Regime Classification Variables (Advanced)

Market Regime variables provide AI-ready classification of current market conditions, combining multiple flow indicators into actionable regime types.

### Available Variables

| Variable | Description |
|----------|-------------|
| `{market_regime_description}` | Indicator calculation methodology and regime type definitions (include once for AI context) |
| `{market_regime}` | All symbols summary (default 5m timeframe) |
| `{market_regime_1m}` | All symbols summary (1-minute timeframe) |
| `{market_regime_5m}` | All symbols summary (5-minute timeframe) |
| `{market_regime_15m}` | All symbols summary (15-minute timeframe) |
| `{market_regime_1h}` | All symbols summary (1-hour timeframe) |
| `{BTC_market_regime}` | BTC regime only (default 5m) |
| `{BTC_market_regime_1m}` | BTC regime (1-minute) |
| `{BTC_market_regime_5m}` | BTC regime (5-minute) |
| `{BTC_market_regime_15m}` | BTC regime (15-minute) |
| `{BTC_market_regime_1h}` | BTC regime (1-hour) |

Similar patterns available for ETH, SOL, and other supported symbols.

### Regime Types

| Regime | Description |
|--------|-------------|
| `breakout` | Strong directional move with volume confirmation |
| `absorption` | Large orders absorbed without price impact (potential reversal) |
| `stop_hunt` | Wick beyond range then reversal (liquidity grab) |
| `exhaustion` | Extreme RSI with diverging CVD (trend weakening) |
| `trap` | Price breaks level but CVD/OI diverge (false breakout) |
| `continuation` | Trend continuation with aligned indicators |
| `noise` | No clear pattern, low conviction |

### Output Format

```
[BTC/5m] stop_hunt (bullish) conf=0.44 | cvd_ratio=0.286, oi_delta=0.01%, taker=1.80, rsi=50.7
```

- `[SYMBOL/TIMEFRAME]` - Symbol and timeframe context
- `regime (direction)` - Classified regime type and direction (bullish/bearish/neutral)
- `conf=X.XX` - Confidence score (0-1)
- Indicator values: cvd_ratio, oi_delta, taker ratio, RSI

### Indicator Definitions

- **cvd_ratio**: CVD / (Taker Buy + Taker Sell). Positive = net buying pressure
- **oi_delta**: Open Interest change percentage over the period
- **taker**: Taker Buy/Sell ratio. >1 = aggressive buying, <1 = aggressive selling
- **rsi**: RSI(14) momentum indicator. >70 overbought, <30 oversold

### Example Usage

```
=== MARKET REGIME ANALYSIS ===
{market_regime_description}

Current Market Regimes:
{market_regime_5m}

Hourly Context:
{BTC_market_regime_1h}
{ETH_market_regime_1h}
```

---

## Legacy Variables (Backward Compatibility)

| Variable | Description | Recommended Alternative |
|----------|-------------|------------------------|
| `{account_state}` | Raw account state text | Use `{available_cash}` + `{total_account_value}` |
| `{prices_json}` | Prices in JSON format | Use `{market_prices}` |
| `{portfolio_json}` | Portfolio in JSON format | Use `{holdings_detail}` |
| `{session_context}` | Legacy session info | Use `{runtime_minutes}` + `{current_time_utc}` |

---

## Need Help?

If you're unsure how to use these variables or want a more sophisticated trading strategy, try the **AI Prompt Generation** feature (requires membership).

The AI assistant will:
- Generate optimized prompts based on your trading style
- Intelligently select appropriate variables and indicators
- Provide professional risk management suggestions
- Support multi-turn conversations to refine your strategy

Click "AI Write Prompt" button to get started.
