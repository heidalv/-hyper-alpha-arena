# L1 做市 · 实盘对接设计（F246，2026-09-16）

> 目标：让"模拟盘 → 实盘"的每一步都预先写好、可配置、可一键回退。
> 当前状态：车道在模拟盘（paper）模式运行，账户为车道专属模拟账户（id=101）。
> 本文件是**设计 + 任务清单**，实盘任何一步都必须先过「晋升判定 + 资金限额审批」才可启用。

## 1. 原则（按优先级）

1. **模拟盘先行**：只有晋升判定 ready + 模拟盘连续 N 日正收益（多日确认）后，才允许进入实盘灰度。
2. **资金隔离**：实盘用独立子账户/独立 API 钱包，与模拟账户**完全隔离**，不共享任何资金与风控额度。
3. **风控先于交易**：实盘启用前必须部署：单笔上限、单币上限、组合上限、日亏熔断、滑点保护、
   撤单超时、API 断连处理——这些在模拟盘已全部存在（60/30/100% + 日亏闸 10%），实盘复用它，
   只是把数值按实盘资金重算。
4. **可观测**：实盘所有订单/成交/仓位写同一个六维账本体系，任何时点可对账。

## 2. 交易所 API 对接（asterdex，已按官方文档核实）

> 官方文档：<https://github.com/asterdex/api-docs>（V1(Legacy) / V3(Recommended)）；
> 概览：<https://raw.githubusercontent.com/asterdex/api-docs/master/Aster%20API%20Overview.md>

### 2.1 两代 API 与凭证（关键事实，2026-09-16 核实）

| | V1 (Legacy) | V3 (Recommended) |
|---|---|---|
| 认证 | API Key + Secret，`X-MBX-APIKEY` 头 + HMAC SHA256 签名（Binance 同构） | API 钱包/AGENT + EIP-712/ECDSA 签名（chainId 714，`AsterSignTransaction` v1）；`user`(主钱包) + `signer`(API 钱包) + `nonce`(微秒) + `signature` |
| 端点 | `/fapi/v1/*`、`/fapi/v2/*` | `/fapi/v3/*` |
| 主网 | `https://fapi.asterdex.com` | `https://fapi.asterdex.com` |
| 测试网 | — | `https://fapi.asterdex-testnet.com`（官方测试网，含行情/下单/回报全链路） |
| 现状 | **2026-03-25 起停止新发 API Key**，存量 key 继续可用 | 官方推荐；基于 Aster L1，下单/成交性能更强；nonce 防重放 |
| 创建入口 | 已关闭 | 主网 `www.asterdex.com/en/api-wallet`（切到 Pro API）· 测试网 `www.asterdex-testnet.com/en/api-wallet`，创建 AGENT（API 钱包，自带私钥） |

**结论**：新对接必须走 **V3**。现有 `AsterdexAdapter`（ccxt binance HMAC）是 V1 时代的网关，
只在用户已有存量 V1 key 时可用；公开行情（`/fapi/v1` 无签名端点）不受影响，采集器照常。

### 2.2 V3 对做市最重要的原生能力（逐条核实）

1. **`POST /fapi/v3/order`**：LIMIT/MARKET/STOP…，`timeInForce` 含 **GTX（post-only）**、HIDDEN；
   `quantityUnit = BASE | QUOTE`；`positionSide = BOTH | LONG | SHORT`；`newClientOrderId`（≤36 字符，
   正则 `^[\.A-Z\:/a-z0-9_-]{1,36}$`）；`newOrderRespType = ACK | RESULT`。
2. **`POST /fapi/v3/batchOrders`**：一次 5 单（**MM 白名单账户 10 单**），weight 5 ——
   我们的 5 币 × 2 边 = 10 单一次拍出（白名单后）。
3. **`PUT /fapi/v3/batchOrders`**：**批量改单**（LIMIT only，改 price/quantity，单笔终身 ≤10000 次修改；
   批量 5 单，MM 白名单 10 单）。改单保留 orderId —— 比"撤旧挂新"少一半往返，且不会短暂裸奔。
4. **`POST /fapi/v3/chase`**：**服务器端报价追逐单**——GTX 限价自动贴 `bid1 − chaseOffset` /
   `ask1 + chaseOffset`，每秒自动改价跟随 BBO；`maxChaseOffset` 触发自动撤单；
   `clientStrategyId`（≤28 字符）。这正是「被动做市挂单」的交易所原生实现。
5. **`DELETE /fapi/v3/order` / `batchOrders` / `allOpenOrders`**：撤单（batch 一次 10 单）。
6. **用户数据流**：`POST /fapi/v3/listenKey` + keepalive，回报事件含 **Order Update /
   Balance and Position Update / Margin Call / Stream Expired**（挂单成交回报、强平预警都在这条流）。
7. **资金类**：`POST /fapi/v3/asset/wallet/transfer`（现货↔合约互转）；入金为链上转账
   （官方 aster-skills-hub 有 deposit skill）；多资产模式 `/fapi/v1/multiAssetsMargin`
   （USDF/asBNB 作保证金，已有适配器方法）。

### 2.3 限频与过滤器（照抄官方，写进执行器）

- 限频：`REQUEST_WEIGHT 2400/分钟`、`ORDERS 1200/分钟`（**按 IP 计**）；429 → 退避，连续违反 →
  418 封 IP（2 分钟到 3 天）。响应头 `X-MBX-USED-WEIGHT-*` / `X-MBX-ORDER-COUNT-*` 必须监控。
- `recvWindow ≤ 5000ms`；**HTTP 503 = 结果未知**（可能已成交）——回报对账必须容忍"未知状态"，
  不得盲目重发下单。
- 符号过滤器：`PRICE_FILTER`（tickSize）、`LOT_SIZE`（stepSize）、`MIN_NOTIONAL`（每单最小名义）、
  `PERCENT_PRICE`（挂单价 vs 标记价 ±15%）、`MAX_NUM_ORDERS 200/符号`。我们 12bp 宽度远在
  PERCENT_PRICE 内；fill_notional ≈ $27.6 也远高于 MIN_NOTIONAL（~$1–5）。
- **MM 白名单**：官方文档明确有 "MM-whitelisted accounts"（batch 5→10 单）——上线前应申请。

### 2.4 实盘执行器选型（两种，先 A 后 B 评估）

- **方案 A（首选，先做）**：自研 V3 REST 执行器，语义与模拟盘完全同构：
  每 tick 用 `PUT /fapi/v3/batchOrders` 改价（保留 orderId，改价后按交易所规则重新排队），
  挂新单用 `POST batchOrders`（GTX post-only），撤单 `DELETE batchOrders`；
  成交判定以 **user stream 回报**为准（ORDER_TRADE_UPDATE），本地账本照搬六维结构。
  *优点*：与 paper 的 plan_tick 输出 1:1 对齐，对账口径一致，回归测试可复用。
- **方案 B（评估，不做第一版）**：`/fapi/v3/chase` 把改价交给交易所每秒自动贴 BBO。
  *优点*：省掉撤/改单往返与排队成本；*风险*：chaseOffset 相对 BBO 而非我们的 mid 口径
  （spread 中位 0.14bp，差异可忽略）；chase 的成交/撤单语义与我们 stop_loss / vol_pause /
  counter_trend 暂停逻辑要重新适配；需测试网单独 A/B 验证后才考虑上线。

## 3. 配置（新增 lane meta.live 块）

```json
{
  "live": {
    "enabled": false,
    "venue": "asterdex",
    "account": "live-sub-1",
    "api_version": "v3",
    "signer_address": "0x…（API 钱包地址，私钥放 secrets 文件，不进 git）",
    "initial_capital_usd": 0,
    "compound_ratio": 0.1,
    "limits": {
      "max_net_exposure_pct": 60,
      "max_symbol_exposure_pct": 30,
      "max_gross_exposure_pct": 100,
      "daily_loss_stop_pct": 10,
      "max_order_notional_usd": 100
    },
    "kill_switch": {"enabled": true, "reason": "未过晋升判定"}
  }
}
```

## 4. 执行链路（每 tick，与模拟盘同一 plan_tick）

1. fetch_market（现有，公开行情）；
2. plan_tick 产出 bid/ask 报价（现有纯函数，实盘/模拟共用 ✓）；
3. 实盘执行器（方案 A）：批量改价 → 校验资金与敞口 → 批量挂新单（GTX）→ 撤多余 →
   收 user stream 回报 → 记账；
4. 成交判定：实盘以**交易所回报**为准；模拟盘仍以桶数据为准。任何不一致（漏单/幻影单）→
   对账页立即可见 + 报警。

## 5. 切换开关与回退

- 车道 mode：`paper` → `live`（前端已有 ModeBadge，后端 setMode 已存在）；
- 首次切换强制要求：晋升判定 ready + `meta.live.kill_switch.enabled=false` 由管理员显式置位；
- 回退：`live → paper` 一键；`kill_switch` 置位后**下一个 tick 内 `DELETE allOpenOrders` 撤掉
  全部实盘挂单**（已有 drill/暂停机制可复用）。

## 6. 任务清单（顺序）

- [ ] A1 secrets/配置结构 + `meta.live` 校验（V3：signer 地址 + 私钥文件 + user 主钱包地址；
      拒绝缺凭证就切 live）
- [ ] A2 V3 REST 网关（自研，不依赖 ccxt）：EIP-712 签名模块 + ping/exchangeInfo/order/
      batchOrders(增改删)/allOpenOrders/listenKey + user stream 断线重连 —— **全部在测试网验证**
- [ ] A3 实盘执行器接线到 runner（复用 plan_tick 输出，先 dry-run 只打日志 + 测试网真下单）
- [ ] A4 实盘对账（交易所回报 vs 本地账本，逐笔 + 定时快照；503 未知态处理）
- [ ] A5 kill_switch / 一键回退 / 断连自保（撤全部挂单 + 暂停车道）
- [ ] A6 晋升判定 + 资金限额审批流
- [ ] A7 测试网全流程验证 ≥1 周 → 主网灰度（最小资金）→ 逐步放大
- [ ] A8 申请 Aster MM 白名单（batch 5→10 单；上线前）

## 7. 未决（需要你拍板）

1. 实盘资金从多少开始？（建议 ≤ $1,000，且 compound_ratio 0.1 起）
2. 凭证路线：你手上是否已有 **V1 存量 API Key**（若有用旧网关最快，但官方已停发，长期必须 V3）；
   没有的话需要在 `asterdex-testnet.com/en/api-wallet` 创建测试网 AGENT（Pro API，几分钟）
   并在主网同样创建——这一步需要你的钱包操作，我可以给逐步指引。
3. 是否先做 A2 的测试网对接（不接钱，只验证下单/撤单/改单/回报链路）？我建议：是，且
   用方案 A（batchOrders 改价），chase 留到 A/B 阶段再评。
4. MM 白名单申请要不要现在提？（不阻塞 A2，只影响批量单数上限）
