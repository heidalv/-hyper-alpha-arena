# 三周期杠杆统一架构 + Aster 双 API 切换 — 设计文档

> 文档版本：v1.0
> 创建日期：2026-07-22
> 作者：ZCode 深度调研
> 适用范围：Hyper-Alpha-Arena 全量代码

---

## 一、问题根因分析

### 1.1 用户描述的问题

三周期（scalp / mid / long）策略各自配置了不同杠杆倍数，但交易所层面对同一 symbol 只支持**一个** leverage 设置。三周期"分仓"在线上完全失效。

### 1.2 实证调查结论

#### 1.2.1 三周期杠杆配置确实不一致（已确认）

| 配置位置 | short/scalp | mid/swing | long/trend |
|---|---|---|---|
| `position_memory_manager.TIER_LEVERAGE`（硬编码） | **20x** | **10x** | **5x** |
| `settings.LEVERAGE_CAP_BY_TIER` | 20 | 20 | 12 |
| `settings.LEVERAGE_CAP_BY_NATURE` | 20 | 20 | 20 |
| `settings.DYNAMIC_LEVERAGE_MIN/MAX`（生产 .env） | 5 / 10 | 5 / 10 | 5 / 10 |
| `scalp_loop.py:660` 实际取值 | **直接读 TIER_LEVERAGE=20** | - | - |
| `position_sizing_agent._resolve_leverage` | - | 动态计算 5-10x | 动态计算 5-10x |
| 生产实际持仓 leverage（DB 实测） | **20x** | **20x** | **20x** |

**矛盾发现**：
- `TIER_LEVERAGE` 配置确实是 20/10/5 三档（用户描述属实）
- scalp_loop 直接硬读 `TIER_LEVERAGE["scalp"]=20`，**完全绕开动态 leverage 计算**
- mid/long 走 `position_sizing_agent._resolve_leverage` 动态计算（5-10x），但被 `position_memory_manager.evaluate_trade` 的 `_tier_leverage` cap 二次压制
- 由于 `_unify_leverage_for_side` 净额模式取 max，scalp 的 20x 会污染 swing/trend 仓位
- **生产 DB 所有持仓 leverage=20x** —— 证实上述链路

#### 1.2.2 交易所 API 层 — 同 symbol 多 leverage 必然冲突（已确认）

**Binance USDⓈ-M Futures**（`ccxt/binance.py` `set_leverage` 源码注释）：
```python
# WARNING: THIS WILL INCREASE LIQUIDATION PRICE FOR OPEN ISOLATED LONG POSITIONS
# AND DECREASE LIQUIDATION PRICE FOR OPEN ISOLATED SHORT POSITIONS
```
- `set_leverage` 是 **symbol 级别**（不是 per-position）
- 同 symbol 上再次 set_leverage 会改变**所有现有持仓**的强平价
- isolated 模式下有仓位时降低杠杆会被拒（错误码 `-4161 ISOLATED_LEVERAGE_REJECT_WITH_POSITION`）

**Hyperliquid**（`hyperliquid_trading_client.py:1141-1143`）：
```python
# Set leverage before placing order
result = self.sdk_exchange.update_leverage(leverage, symbol, is_cross=is_cross)
```
- `place_order` 每次下单前**都调 update_leverage**
- 三周期策略各自调 `place_order(leverage=20/10/5)` → 最后一个 leverage 覆盖整个 symbol
- 用户描述的"被交易所强制整合为最后一个杠杆倍数的单一仓位"**完全属实**

**Aster (V3 Pro API)**（Aster 官方文档 + ccxt 源码）：
- `POST /fapi/v3/leverage` 同样是 **symbol 级别**
- 与 Binance 行为一致（错误码、isolated 限制都沿用 Binance）
- 多次 set_leverage 同样会互相覆盖

### 1.3 当前代码的缺陷清单

| # | 文件:行 | 缺陷 | 影响 |
|---|---|---|---|
| 1 | `position_memory_manager.py:194-198` `TIER_LEVERAGE` | 三档不一致（scalp=20/swing=10/trend=5）硬编码 | 一旦 cap 生效就会导致同 symbol 多次 set_leverage 覆盖 |
| 2 | `scalp_loop.py:657-662` | 直接读 `TIER_LEVERAGE["scalp"]=20`，**绕开 dynamic_leverage_calculator** | scalp 永远 20x，不受动态计算控制 |
| 3 | `paper_trading_engine.py:3848-3906` `_unify_leverage_for_side` | 净额模式取 max(leverage_i) 强制对齐 → scalp 的 20x 污染 swing/trend | 所有 tier 实际 leverage 被钉死在历史最大值 |
| 4 | `paper_trading_engine.py:1016-1026` | 按 `(symbol, side, trade_nature)` 分仓但 netting 又合并 | DB 层分仓 + netting 层合并，逻辑矛盾 |
| 5 | `hyperliquid_trading_client.py:1141-1162` `place_order` | 每次下单前都调 `update_leverage`，失败被静默吞掉 | 实盘必然互相覆盖；set 失败无感知 |
| 6 | `paper_trading_engine.py:1018, 1130` | 开仓后强行 unify leverage → trend 仓位的 P&L 计算被扭曲（5x 被改成 20x） | 风控参数失真，强平价计算错误 |
| 7 | `decision_sizing.py:241` `if stage_e_active()` | StageE 默认关闭，cap 完全不生效 | 当前所有持仓 lev=20x，TIER_LEVERAGE 配置形同虚设 |
| 8 | `position_sizing_agent.py:327-391` `_resolve_leverage` | 确定性公式重算覆盖 AI 建议，但 scalp_loop 不走这条路径 | scalp 和 mid/long 走两套完全不同的 leverage 决策链 |
| 9 | `Account.selected_exchange` | 单选标量，migration default `hyperliquid` 与代码 default `asterdex` 不一致 | 新建账号 DB 默认仍 hyperliquid |
| 10 | `asterdex_adapter.py:27` | URL 硬编码 `ASTERDEX_FUTURES_URL` | 无法支持 Aster 双 API（Pro/Normal）切换 |
| 11 | `ExchangeCredential` 表 | 已有 `label` 字段但 `get_or_create_global_client` 只 `.first()` | 即使存了两套 key 也只用第一套 |
| 12 | `paper_netting.py:180-261` `aggregate_rows_to_net` | 净额聚合时 `unified_leverage = max(leverage_i)` | 与 `_unify_leverage_for_side` 重复逻辑，且都取 max |

---

## 二、Aster 双 API 调研对比表

### 2.1 V1（Standard / 普通 API）vs V3（Pro API / 专业 API）

| 维度 | **V1（Standard）** | **V3（Pro API / 推荐）** |
|---|---|---|
| **状态** | 2026-03-25 起停止新签发，老 key 可用 | 当前推荐，唯一新签发渠道 |
| **认证方式** | HMAC SHA256（Binance 兼容） | **EIP-712 类型化数据签名** |
| **凭证** | `apiKey` + `secret`（EOA private key 衍生） | **钱包 EOA private key** + wallet address + signer(Agent) address |
| **签名参数** | `timestamp` + `recvWindow` + `signature` | `nonce`（微秒）+ `user`（钱包地址）+ `signer`（Agent 地址）+ `signature` |
| **Header** | `X-MBX-APIKEY: <apiKey>` | 不传 apiKey header，签名在 query/form |
| **签名域** | 无 | `name="AsterSignTransaction"`, `version="1"`, `chainId=1666`（主网）/ `714`（测试网） |
| **Binance 兼容度** | 几乎 100%（URL/参数/错误码） | API 路径兼容（`/fapi/v3/*`），**签名层不兼容** |
| **部署模型** | 中心化代理 | Builder/Agent 模型：用户钱包签 EIP-712 授权 Agent 地址代下单 |
| **ccxt 4.5.59 支持** | ❌ 已移除（v4.5.52 起只要 privateKey） | ✅ `ccxt.aster` 完整实现（REST + WS） |

### 2.2 功能能力（V1/V3 一致）

| 能力 | 支持 | 端点 |
|---|---|---|
| 交易下单 | ✅ | `POST /fapi/v3/order`、`/sapi/v3/order` |
| 批量下单 | ✅ | `POST /fapi/v3/batchOrders` |
| 撤单 | ✅ | `DELETE /fapi/v3/order`、`/fapi/v3/allOpenOrders` |
| 设置杠杆 | ✅ | `POST /fapi/v3/leverage`（1–125x，**symbol 级别**）|
| 仓位模式切换 | ✅ | `POST /fapi/v3/positionSide/dual`（one-way / hedge）|
| 保证金模式 | ✅ | `POST /fapi/v3/marginType`（CROSSED / ISOLATED）|
| 资金划转 | ✅ | `POST /fapi/v3/asset/wallet/transfer` |
| 只读行情 | ✅ | 公开端点无需签名 |

订单类型：LIMIT / MARKET / STOP / STOP_MARKET / TAKE_PROFIT / TAKE_PROFIT_MARKET / TRAILING_STOP_MARKET。
最小名义价值：5 USDT。
**关键限制**：`-4161 ISOLATED_LEVERAGE_REJECT_WITH_POSITION` — isolated 模式有仓位时不能降杠杆。

### 2.3 网络 Endpoints

| 用途 | URL |
|---|---|
| Futures 主网 REST | `https://fapi.asterdex.com/fapi` |
| Spot 主网 REST | `https://sapi.asterdex.com/api` |
| Futures 测试网 REST | `https://fapi.asterdex-testnet.com` |
| Futures WebSocket 主网 | `wss://fstream.asterdex.com/ws` |
| V3 签名 chainId | 主网 `1666`，测试网 `714` |

### 2.4 限频策略

- **基于 IP**（不是 API key）
- 请求权重：约 **1200 weight/min**
- 订单速率：Binance 风格动态返回（看 `/exchangeInfo` 或 429 响应）
- 错误码：`-1003 TOO_MANY_REQUESTS`、`-1015 TOO_MANY_ORDERS`、`-2025 MAX_OPEN_ORDER_EXCEEDED`
- HTTP：429=限频、418=IP 封禁

### 2.5 ccxt 4.5.59 `ccxt.aster` 实现状态

✅ 已实现（全部走 V3 EIP-712）：fetch_ticker / fetch_ohlcv / fetch_order_book / create_order / create_orders（批量）/ cancel_order(s) / **set_leverage** / set_margin_mode / set_position_mode / fetch_positions / fetch_balance / fetch_ledger / transfer / withdraw / add_margin / reduce_margin / fetch_trading_fee。

ccxt.pro.aster：watch_ticker / watch_order_book / watch_trades / watch_orders / watch_mark_price(s) / watch_bids_asks。

**ccxt 使用示例**：
```python
import ccxt
ex = ccxt.aster({
    'privateKey': '0x<your_eoa_private_key>',  # 不是 apiKey/secret
    'options': {'builderFee': False},  # 关闭自动给 ccxt builder 授权
})
ex.set_leverage(10, 'BTC/USDT:USDT')
ex.set_position_mode(False)  # one-way
ex.create_order('BTC/USDT:USDT', 'limit', 'buy', 0.01, 50000)
```

### 2.6 文档资源

- 官方总文档：https://docs.asterdex.com/for-developers/aster-api
- API Reference：https://asterdex.github.io/aster-api-website/
- V3 EN Markdown：https://github.com/asterdex/api-docs/blob/master/V3(Recommended)/EN/aster-finance-futures-api-v3_EN.md
- Testnet 文档：https://github.com/asterdex/api-docs/blob/master/V3(Recommended)/EN/aster-finance-futures-api-testnet.md
- API Management UI：https://www.asterdex.com/en/api-management

---

## 三、统一杠杆方案设计

### 3.1 核心原则

1. **单一 leverage 真相源**：每个 (account, symbol) 在交易所层面只有一个生效的 leverage 值
2. **三周期虚拟分仓**：策略层各自维护虚拟 leverage（用于 P&L 估算/止损计算），但**实际下单用统一的"账户级 leverage"**
3. **账户级 leverage 动态计算**：沿用现有 `DYNAMIC_LEVERAGE_MIN=5 / MAX=10` 区间，按波动率/regime/资金规模动态裁定（5x-10x），三周期共用同一个动态值。**取三周期建议的最小值**（保守优先，避免 trend 想 5x 但 scalp 强行提到 20x 把 trend 仓位也提到 20x）
4. **paper 层保留虚拟分仓语义**：让回测和 paper 仍能看到三档虚拟 P&L（用虚拟 leverage 估算，但下单/风控用统一动态 leverage）

### 3.2 新增 `account_symbol_leverage` 表（统一 leverage 状态）

```sql
CREATE TABLE account_symbol_leverage (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    -- 交易所层面唯一生效的 leverage（动态 5-10x）
    unified_leverage INTEGER NOT NULL,
    -- 决策来源：'dynamic_calc'（动态计算）/ 'risk_cap'（风控压制）/ 'manual'（手动）
    source VARCHAR(32) NOT NULL DEFAULT 'dynamic_calc',
    -- 最后一次 set_leverage 成功的时间（避免重复调 API）
    last_synced_at TIMESTAMP,
    -- 三档虚拟 leverage（仅 paper 层和 P&L 计算用，不传交易所）
    -- 沿用 DYNAMIC_LEVERAGE 5-10x 动态计算结果，三档保持一致
    virtual_scalp_leverage INTEGER DEFAULT 5,
    virtual_swing_leverage INTEGER DEFAULT 5,
    virtual_trend_leverage INTEGER DEFAULT 5,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, symbol)
);
```

### 3.3 Leverage 决策算法（动态 5-10x）

新增 `unified_leverage_resolver.py`：

```python
def resolve_unified_leverage(
    account_id: int,
    symbol: str,
    tier: str,  # short / mid / long（仅用于虚拟 leverage 记录，不影响实际 leverage）
    market_summary: dict,
) -> int:
    """决定该 (account, symbol) 的统一 leverage（动态 5-10x）。
    
    算法（沿用现有 dynamic_leverage_calculator 的输出）：
    1. 调 calculate_dynamic_leverage(account_id, symbol) 拿基础动态值
       - 该函数已综合 volatility/funding/regime/drawdown 四维权重
       - 输出已 clamp 到 [DYNAMIC_LEVERAGE_MIN=5, DYNAMIC_LEVERAGE_MAX=10]
    2. 查 account_symbol_leverage 表的当前 unified_leverage
    3. 若新值 < 已有值 且该 symbol 有 open 仓位 → 保持原值（isolated 不能降杠杆）
       若新值 > 已有值 → 升到新值（可以升）
       若无仓位 → 直接用新值
    4. 三档虚拟 leverage 都用同一个动态值（不再区分 5/10/20）
    
    返回：unified_leverage（int，范围 5-10）
    
    关键：用户确认 DYNAMIC_LEVERAGE_MIN=5 / MAX=10（5x 太低的话也只到 10x 上限）
    """
```

### 3.4 三层架构改造

```
┌─────────────────────────────────────────────────────────┐
│ 策略层（scalp_loop / midlong_loop / trend_agent）        │
│  - 各自计算"虚拟 leverage"（5/5/3）用于 P&L 估算         │
│  - 各自维护虚拟 TP/SL                                    │
└─────────────────────┬───────────────────────────────────┘
                      │ 开仓请求 (sym, side, size, virtual_lev)
                      ▼
┌─────────────────────────────────────────────────────────┐
│ Leverage 统一层（unified_leverage_resolver）             │
│  - 查 account_symbol_leverage 表                         │
│  - 决定本次实际 leverage                                 │
│  - 写回虚拟 leverage 到对应 tier 字段                    │
└─────────────────────┬───────────────────────────────────┘
                      │ (sym, side, size, unified_lev)
                      ▼
┌─────────────────────────────────────────────────────────┐
│ 交易所适配层（live_executor / hyperliquid_trading_client）│
│  - 仅当 unified_lev != last_synced 时调 set_leverage     │
│  - create_order 用 unified_lev                           │
└─────────────────────────────────────────────────────────┘
```

### 3.5 关键修改点

#### 3.5.1 移除 `place_order` 内的 set_leverage 重复调用

**文件**：`backend/services/hyperliquid_trading_client.py:1141-1143`

```python
# 改前：每次下单都调
result = self.sdk_exchange.update_leverage(leverage, symbol, is_cross=is_cross)

# 改后：只在 leverage 变化时调（用 account_symbol_leverage.last_synced_at 比对）
if _need_sync_leverage(account_id, symbol, leverage):
    result = self.sdk_exchange.update_leverage(leverage, symbol, is_cross=is_cross)
    _mark_leverage_synced(account_id, symbol, leverage)
```

#### 3.5.2 `TIER_LEVERAGE` 改为"虚拟 leverage"语义（动态 5-10x）

**文件**：`backend/services/position_memory_manager.py`

```python
# 改前：三档不一致（20/10/5）
TIER_LEVERAGE = {
    'scalp': 20,        # 短线 20x
    'swing': 10,        # 中线 10x
    'trend_follow': 5,  # 长线 5x
}

# 改后：三档统一上限，由 dynamic_leverage_calculator 在 5-10x 区间动态决定
# TIER_LEVERAGE 仅作为各 tier 的上限（cap），实际 leverage 由
# resolve_unified_leverage() 动态计算（5-10x），三档共用同一个动态值。
TIER_LEVERAGE = {
    'scalp': 10,        # 短线上限 10x（动态 5-10x）
    'swing': 10,        # 中线上限 10x（动态 5-10x）
    'trend_follow': 10, # 长线上限 10x（动态 5-10x）
}
# 用户确认：5x 太低，保留动态 5-10x 区间，三档统一上限 10x
```

#### 3.5.3 `_unify_leverage_for_side` 改为查 `account_symbol_leverage` 表

**文件**：`backend/services/paper_trading_engine.py:3848`

```python
# 改后：用统一 leverage，不再强行同步（虚拟 leverage 保留在 virtual_*_leverage 字段）
def _unify_leverage_for_side(self, db, account_id, symbol, side, target_leverage):
    # 查 account_symbol_leverage.unified_leverage
    _unified = get_unified_leverage(db, account_id, symbol)
    # 所有持仓用 unified_leverage，但记录各自 tier 的 virtual_leverage 到 PaperPosition
    ...
```

#### 3.5.4 `PaperPosition` 加 `virtual_leverage` 字段

```sql
ALTER TABLE paper_positions ADD COLUMN virtual_leverage FLOAT DEFAULT 1.0;
-- 用途：记录"策略层期望的 leverage"，用于 P&L 估算和审计
-- 实际下单用 leverage 字段（统一值）
```

---

## 四、Aster 双 API 配置方案

### 4.1 `ExchangeCredential` 表扩展

```sql
ALTER TABLE exchange_credentials ADD COLUMN api_mode VARCHAR(16) DEFAULT 'normal';
-- 'normal' = V1 HMAC（兼容旧 Binance 凭证）
-- 'pro'    = V3 EIP-712（Aster 推荐，只需 privateKey）
ALTER TABLE exchange_credentials ADD COLUMN private_key_encrypted TEXT;
-- V3 专用：EOA private key（加密存储）
ALTER TABLE exchange_credentials ADD COLUMN wallet_address VARCHAR(64);
-- V3 专用：用户钱包地址
ALTER TABLE exchange_credentials ADD COLUMN signer_address VARCHAR(64);
-- V3 专用：Agent/Builder 签名地址（可选，默认用 ccxt builder）
ALTER TABLE exchange_credentials ADD COLUMN api_url_override TEXT;
-- 覆盖默认 endpoint（如测试网 https://fapi.asterdex-testnet.com）
```

### 4.2 `AsterdexAdapter` 改造

**文件**：`backend/services/exchange/asterdex_adapter.py`

```python
class AsterdexAdapter(CcxtBaseAdapter):
    ASTERDEX_FUTURES_URL = "https://fapi.asterdex.com"
    ASTERDEX_TESTNET_URL = "https://fapi.asterdex-testnet.com"
    
    def __init__(self, api_key=None, secret=None, private_key=None,
                 wallet_address=None, signer_address=None,
                 api_mode="pro", testnet=False, api_url_override=None):
        self.api_mode = api_mode  # 'pro' or 'normal'
        # ... 根据 api_mode 选择 ccxt.aster (V3) 或 ccxt.binance+override_url (V1)
```

### 4.3 `ExchangeManager` 按 api_mode 选择 client

```python
def get_or_create_global_client(self, exchange: str, api_mode: str = None):
    """根据 api_mode 选择对应凭证创建 client。
    
    - api_mode='pro'：用 private_key 创建 ccxt.aster (V3 EIP-712)
    - api_mode='normal'：用 api_key+secret 创建 ccxt.binance 覆盖 URL (V1 HMAC)
    - api_mode=None：保持旧行为（向后兼容）
    """
```

### 4.4 推荐配置流程（用户操作步骤）

1. 在 https://www.asterdex.com/en/api-management 创建 **Pro API**（V3）
2. 拿到 EOA private key + wallet address
3. 调 `POST /api/exchange/credentials` 提交：
   ```json
   {
     "exchange": "asterdex",
     "api_mode": "pro",
     "private_key": "0x...",
     "wallet_address": "0x...",
     "testnet": false,
     "enabled": true,
     "label": "aster-pro-main"
   }
   ```
4. 调 `PUT /api/accounts/14` body `{"selected_exchange": "asterdex"}`
5. 重启 backend，系统自动用 V3 路径连接 Aster

---

## 五、配置迁移方案

### 5.1 `.env` 新增项

```bash
# 统一 leverage（动态 5-10x，三周期共用）
# 沿用现有 DYNAMIC_LEVERAGE_MIN/MAX，用户确认 5-10x 区间
DYNAMIC_LEVERAGE_MIN=5
DYNAMIC_LEVERAGE_MAX=10

# Aster Pro API（V3 EIP-712，主网）
ASTER_API_MODE=pro
ASTER_CHAIN_ID=1666
ASTER_PRIVATE_KEY=0xYOUR_EOA_PRIVATE_KEY
ASTER_WALLET_ADDRESS=0xYOUR_WALLET_ADDRESS
ASTER_BUILDER_FEE=false

# 默认交易所（已配置，确认）
DEFAULT_EXCHANGE=asterdex
```

### 5.2 DB Migration 顺序

1. `add_account_symbol_leverage_table.py`（新建统一 leverage 表）
2. `add_virtual_leverage_to_paper_positions.py`（加虚拟 leverage 字段）
3. `add_api_mode_to_exchange_credentials.py`（加 V3 字段）
4. `migrate_tier_leverage_to_virtual.py`（把现有 paper_positions.leverage 复制到 virtual_leverage）
5. `set_unified_leverage_for_existing_positions.py`（把所有 open 持仓 leverage 统一为 5x）

### 5.3 代码改动顺序（建议 PR 分批）

**PR1（紧急修复，1 天）**：
- 改 `TIER_LEVERAGE` 为 VIRTUAL_TIER_LEVERAGE，明确语义
- 改 `place_order` 内 set_leverage 加去重（避免重复调 API）
- 启用 StageE（`RISK_USE_LEVERAGE_CAP_BY_TIER=True` + `stage_e_active` 返回 True）

**PR2（Aster 切换，2-3 天）**：
- `ExchangeCredential` 加 api_mode/private_key/wallet_address 字段
- `AsterdexAdapter` 支持 V3 EIP-712
- `ExchangeManager` 按 api_mode 选 client
- 前端凭证表单加"Pro API / Normal API"切换

**PR3（统一 leverage 完整方案，3-5 天）**：
- 新建 `account_symbol_leverage` 表
- 实现 `unified_leverage_resolver.py`
- `_unify_leverage_for_side` 改用统一 leverage
- paper_positions 加 virtual_leverage 字段
- paper 引擎 P&L 计算用 virtual_leverage，下单用 unified_leverage

---

## 六、改造影响范围评估

### 6.1 高影响（核心交易路径）

| 模块 | 文件 | 改动类型 |
|---|---|---|
| 实盘下单 | `hyperliquid_trading_client.py:1141` | set_leverage 去重 |
| Paper 下单 | `paper_trading_engine.py:3848` | 统一 leverage 逻辑 |
| 杠杆决策 | `position_memory_manager.py` | TIER_LEVERAGE → VIRTUAL |
| 杠杆 cap | `decision_sizing.py:241` | StageE 默认开启 |
| 风控 | `risk_band_resolver.py` | 接入 unified_leverage |

### 6.2 中影响（配置/适配）

| 模块 | 文件 | 改动类型 |
|---|---|---|
| Aster 适配 | `asterdex_adapter.py` | 支持 V3 EIP-712 |
| 凭证管理 | `models.py:945` ExchangeCredential | 加 api_mode 等字段 |
| 凭证读取 | `exchange_manager.py:179` | 按 api_mode 选 client |
| 数据库 | 新建 migration | 3-5 个迁移脚本 |

### 6.3 低影响（UI/审计）

| 模块 | 文件 | 改动类型 |
|---|---|---|
| 前端凭证 | `frontend-next/src/app/exchange/page.tsx` | 加 Pro/Normal 切换 |
| 前端杠杆 | `frontend-next/src/app/paper-trading/page.tsx` | 显示 virtual vs unified leverage |
| 审计日志 | unified_leverage 变更记录 | 新增审计事件 |

### 6.4 风险点

1. **现有 open 持仓 leverage 迁移**：需要把 20x 统一降到 5x，**但在 isolated 模式有仓位时不能降杠杆**（Aster/Binance 限制）。建议：等现有仓位平仓后再切，或用 `reduce_margin` 逐步调整。
2. **Aster V3 builder fee**：ccxt 默认会给 `0x1F58...` builder 授权 10bps fee，需要 `options.builderFee=False` 关闭或自建 builder。
3. **V1 → V3 凭证迁移**：用户需要重新生成 Pro API 的 EOA private key，老 V1 凭证（apiKey+secret）不能直接复用。
4. **Paper P&L 计算偏差**：virtual_leverage 改用 5x 后，历史 20x 的回测数据不能直接对比。

---

## 七、附录

### 7.1 当前账户配置（生产实测）

| account_id | name | selected_exchange | default_lev | max_lev | mode | active |
|---|---|---|---|---|---|---|
| 14 | 小资金 | **asterdex** | 10 | 20 | paper | **true** |
| 7 | 套利测试 | asterdex | 10 | 20 | live | false |
| 1-5 | 其它 | hyperliquid | 10 | 20 | mixed | false |

**主账户 id=14 已经切到 asterdex**，但当前跑 paper 模式，所以问题未暴露。一旦切 live，三周期 leverage 冲突立即触发。

### 7.2 当前 paper_positions leverage 分布

| symbol | tier | side | leverage | count |
|---|---|---|---|---|
| BTC | short | long | 20x | 1 |
| ETH | long | short | 20x | 1 |
| AAVE | short | long | 20x | 1 |
| LIT | short | short | 20x | 1 |
| ONDO | short | short | 20x | 1 |

**所有持仓都是 20x** —— 证实：
- `TIER_LEVERAGE`（20/10/5）的三档差异在 `_unify_leverage_for_side` 强制对齐 max 后失效
- scalp_loop 直接用 `TIER_LEVERAGE["scalp"]=20` 开仓，20x 成为该 symbol 历史最大 leverage
- 后续 swing/trend 仓位即使设计为 10x/5x，也被 `_unify_leverage_for_side` 强行拉到 20x

**根本原因链（Agent 实证）**：
1. `scalp_loop.py:660` 直接读 `TIER_LEVERAGE.get("scalp", 20)` = 20，绕开动态计算
2. `paper_trading_engine.py:3890-3891` 净额模式下取 `max(leverage_i)` 作为统一值
3. 一旦 scalp 在某 symbol 开过 20x，该 symbol 所有后续 tier 仓位 leverage 都被钉死在 20x
4. `hyperliquid_trading_client.py:1141` 每次下单前都调 `update_leverage`，最后一次覆盖整个 symbol

**修复方案**：把 TIER_LEVERAGE 三档统一为 10（动态 5-10x），从源头消除差异；同时给 set_leverage 加去重，避免重复调 API。

### 7.3 Aster V3 EIP-712 签名结构（参考）

```typescript
// AsterSignTransaction 域
{
  name: "AsterSignTransaction",
  version: "1",
  chainId: 1666,  // 主网
  verifyingContract: "0x0000000000000000000000000000000000000000"
}

// 签名消息字段
{
  nonce: <microsecond_timestamp>,
  user: "<wallet_address>",
  signer: "<agent_address>",
  // ... 业务参数
}
```

### 7.4 ccxt.aster V3 初始化示例

```python
import ccxt

ex = ccxt.aster({
    'privateKey': '0xabc...',  # EOA private key
    'options': {
        'builderFee': False,          # 关闭 ccxt 默认 builder
        'v3ChainId': 1666,            # 主网
        'defaultType': 'swap',        # U 本位合约
    },
})

# 设置统一 leverage（symbol 级别）
ex.set_leverage(5, 'BTC/USDT:USDT')

# One-way 模式
ex.set_position_mode(False)

# 下单
ex.create_order('BTC/USDT:USDT', 'market', 'buy', 0.01)
```
