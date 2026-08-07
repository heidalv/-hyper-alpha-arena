# Hyper Alpha Arena API Documentation

## Overview

Hyper Alpha Arena is a cryptocurrency perpetual contract trading platform with AI-powered decision making. The API provides access to trading, portfolio management, signal generation, and more.

## Base URL

```
Development: http://localhost:8000
Production: https://your-domain.com
```

## Authentication

### Session-based Authentication

1. Login to get a session token:
```bash
POST /api/users/login
Content-Type: application/json

{
  "username": "your_username",
  "password": "your_password"
}
```

2. Use the session token in subsequent requests:
```bash
GET /api/users/profile?session_token=your_token
```

## API Versioning

The API supports versioning via the `X-API-Version` header:

```
X-API-Version: v3
```

Current version: **v3**

## Endpoints

### Health Check

**GET** `/api/health`

Check if the API is running.

Response:
```json
{
  "status": "healthy",
  "message": "Trading API is running",
  "version": "0.5.0"
}
```

### Account Management

**GET** `/api/accounts`

Get all trading accounts for the current user.

**GET** `/api/accounts/{account_id}`

Get specific account details.

**PUT** `/api/accounts/{account_id}`

Update account configuration.

### Trading

**POST** `/api/binance/order`

Place a Binance order.

Request:
```json
{
  "account_id": 1,
  "symbol": "BTCUSDT",
  "side": "BUY",
  "order_type": "MARKET",
  "quantity": 0.01
}
```

**DELETE** `/api/binance/order/{order_id}`

Cancel an order.

### Positions

**GET** `/api/positions`

Get all current positions.

**GET** `/api/positions/{position_id}`

Get specific position details.

### AI Decision Logs

**GET** `/api/ai-decisions`

Get AI trading decision logs.

Query Parameters:
- `account_id`: Filter by account
- `operation`: Filter by operation (buy/sell/hold)
- `symbol`: Filter by symbol
- `start_date`: Start date filter
- `end_date`: End date filter
- `limit`: Number of results (default: 50)

### Signals

**GET** `/api/signals`

Get trading signals.

**POST** `/api/signals/generate`

Generate new signals.

### Market Data

**GET** `/api/market/klines`

Get K-line (candlestick) data.

Query Parameters:
- `symbol`: Trading symbol (e.g., BTC)
- `period`: Time period (1m, 5m, 15m, 1h, 4h, 1d)
- `limit`: Number of candles

**GET** `/api/market/price/{symbol}`

Get current price for a symbol.

**GET** `/api/market/prices`

Batch prices — Hub 内存快照优先，miss 时降级 REST bulk。

Query Parameters:
- `symbols`: Comma-separated list (e.g. `BTC,ETH,SOL`)

**GET** `/api/market/prices/snapshots`

Full MarketDataHub snapshots (price, funding, OI, volume, bid/ask).

**GET** `/api/market/hub/status`

MarketDataHub health and throughput stats.

### ATAS (Advanced Trading Automation System)

**GET** `/api/atas/status`

Get ATAS system status.

**POST** `/api/atas/backtest`

Run a backtest.

### Configuration

**GET** `/api/config`

Get system configuration.

**PUT** `/api/config`

Update system configuration.

### WebSocket

**WS** `/ws`

Real-time WebSocket connection for:
- Price updates
- Order status
- Position changes
- AI decisions

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| General API | 100 requests/minute |
| Trading | 50 requests/minute |
| Market Data | 200 requests/minute |

## Error Responses

Error responses follow this format:

```json
{
  "detail": "Error message description",
  "code": "ERROR_CODE",
  "timestamp": "2024-01-01T00:00:00Z"
}
```

Common HTTP Status Codes:
- `200`: Success
- `400`: Bad Request
- `401`: Unauthorized
- `403`: Forbidden
- `404`: Not Found
- `429`: Too Many Requests
- `500`: Internal Server Error

## Python SDK Example

```python
import requests

BASE_URL = "http://localhost:8000"

# Login
response = requests.post(
    f"{BASE_URL}/api/users/login",
    json={"username": "user", "password": "pass"}
)
token = response.json()["session_token"]

# Get accounts
headers = {"session_token": token}
response = requests.get(f"{BASE_URL}/api/accounts", headers=headers)
accounts = response.json()
```

## JavaScript/TypeScript SDK Example

```typescript
const API_BASE = 'http://localhost:8000';

class HyperAlphaAPI {
  private token: string = '';
  
  async login(username: string, password: string) {
    const response = await fetch(`${API_BASE}/api/users/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const data = await response.json();
    this.token = data.session_token;
  }
  
  async getAccounts() {
    const response = await fetch(`${API_BASE}/api/accounts?session_token=${this.token}`);
    return response.json();
  }
}
```

## Support

- Documentation: https://docs.hyperalpha.ai
- GitHub Issues: https://github.com/hyper-alpha/arena/issues
- Discord: https://discord.gg/hyperalpha

---

## Arbitrage API (`/api/arbitrage`)

V3 统计套利：资金费率 / 跨所价差 / 基差（Paper/Live）。

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/arbitrage/status` | 引擎 + orchestrator 状态 |
| GET | `/api/arbitrage/positions?status=active` | DB 仓位列表 |
| GET | `/api/arbitrage/opportunities` | 扫描缓存机会 |
| GET | `/api/arbitrage/metrics` | 实时仓位指标 |
| GET | `/api/arbitrage/capital-pool` | 资金池状态 |
| GET | `/api/arbitrage/alerts` | 监控告警（leg/pool/funding/熔断） |
| GET | `/api/arbitrage/mid-cache/status` | 跨所 mid 缓存 TTL/staleness |
| GET | `/api/arbitrage/execution-authority` | 执行权威（FullAuto 为自动路径） |
| GET | `/api/arbitrage/cross-arb/spreads` | 跨所价差扫描 |
| PUT | `/api/arbitrage/mode` | 切换 paper/live（需 `confirm=true`） |
| POST | `/api/arbitrage/close/{position_id}` | 手动平仓 |

配置：`backend/config/arb_config.yaml`  
设计：`docs/SYSTEM_UPGRADE_DESIGN_V3.md` §3

---

## Rebate Arbitrage API (`/api/rebate`)

积分/返佣驱动套利 S1–S8。

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/rebate/status` | 引擎状态 |
| GET | `/api/rebate/opportunities` | 策略评估结果 |
| GET | `/api/rebate/positions` | 活跃仓位 |
| POST | `/api/rebate/scan` | 手动触发扫描 |
| POST | `/api/rebate/execute` | 执行策略 |
| POST | `/api/rebate/positions/{id}/close` | 平仓 |
| GET | `/api/rebate/capital` | 资金分配 |
| GET | `/api/rebate/events` | 事件日志 |
| GET | `/api/rebate/risk/breakers` | 熔断器状态 |
| PATCH | `/api/rebate/mode` | 切换 paper/live |
| GET/PATCH | `/api/rebate/config/*` | 运行时配置 |

配置：`backend/config/rebate_arb_config.yaml`  
设计：`docs/rebate-arb-automation.md`
