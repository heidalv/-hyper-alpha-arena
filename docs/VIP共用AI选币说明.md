# VIP 共用 AI 选币（特色产品）

## 一句话

数据中心共用行情 + **管理员租户 LLM** 做深度选币 → 产出短线 / 长线两套推荐；VIP 在独立页面阅读理由，决定是否加入自己的交易会话。  
各账户**交易决策**仍用自己的 LLM Key；仅选币这一条链路走管理员 LLM。

选币扫描**只读数据中心**（ticker 缓存 / `symbol_catalog` / 宇宙状态），**禁止**再单独 `MarketScanner` / ccxt 拉交易所目录。

## 谁能用

| 条件 | 说明 |
|------|------|
| `tier=vip` 或 `role=admin` | 侧栏出现「VIP AI 选币」；API 挂 `require_feature("ai_coin_select")` |
| 用户开关 `coin_select_enabled` | 默认关；打开后可读完整看板、可采纳 |
| 管理员 | 始终可看管理面板；可「立即重扫」 |
| **会话内自动选币** | 同样仅 VIP/管理员：`/api/auto-coin/*/start|scan-now`、启动会话带 `auto_coin_enabled`、改 `auto_coin_max_slots` 均会 403；非 VIP 可关闭已开功能 |

## 页面与操作

- 前端：`frontend-next` → `/coin-select`
- 顶栏：启用开关、自动跟投短线（默认关）、选择会话
- Tab：短线 / 长线；分区：强烈推荐 / 观察列表
- 「加入会话」：短线写入 `auto_coin_symbols`；长线写入会话固定 `symbols`（须手动确认）
- 长线**永不**自动跟投

## API

前缀：`/api/coin-select`（需登录 + VIP feature）

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/settings` | 读开关 |
| PATCH | `/settings` | `{enabled, auto_follow_scalp, default_session_id, ...}` |
| GET | `/board?horizon=scalp\|midlong` | 共用看板 |
| GET | `/sessions` | 我的可采纳会话 |
| POST | `/adopt` | `{symbol, horizon, session_id, candidate_id?}` |
| POST | `/scan-now` | 仅管理员强制扫描 |
| GET | `/admin/detail` | 扫描日志、LLM 就绪、候选（含拒绝） |
| POST | `/admin/delist` | `{candidate_id, listed}` 上下架 |

## 配置（.env）

```
COIN_SELECT_ADMIN_TENANT_ID=326   # 管理员用户 id（heida）
COIN_SELECT_PLATFORM_ENABLED=true
COIN_SELECT_SCAN_INTERVAL_SEC=1800
COIN_SELECT_AI_MAX_CANDIDATES=15
COIN_SELECT_BOARD_TTL_HOURS=12
AUTO_COIN_SOURCE=platform_board   # 会话只跟投看板；legacy=旧独立扫描
FEATURE_FACTOR_EXPOSURE_ENABLED=true
AUTO_COIN_SOFT_FACTOR=true
COIN_RANK_ENGINE_ENABLED=true
COIN_RANK_GATES_ENABLED=true
```

交易路径仍遵守 `FORBID_SHARED_PLATFORM_LLM=true`。

升级设计全文：[`AI选币全面升级设计_2026-08.md`](./AI选币全面升级设计_2026-08.md)。

## 与旧「会话内 AutoCoin」关系（已统一）

**唯一真相源**：管理员 VIP 共用短线看板（管理员 LLM 审核）。

| 角色 | 做什么 |
|------|--------|
| 平台 `coin-select` | 扫描行情 + 管理员 LLM → 写出短线/长线看板 |
| 会话 `auto-coin` | **只跟投**短线看板里的 approve（及高信心 watch）→ 写入 `auto_coin_symbols` |
| 交易决策 | 仍用各账户自己的 LLM Key |

### 两张表（互不占槽）

| 表 | 字段 | 用途 | 槽位 |
|----|------|------|------|
| 固定交易对 | `session.symbols` | 手动配置，走长线 | **不占** AI 选币槽 |
| AI 选币 | `session.auto_coin_symbols` | 看板跟投/短线 | 仅此表受 `auto_coin_max_slots`（5–10）限制 |

规则：同币不得同时在两张表；已在固定表的币不会被 AI 注入，也不会占用 AI 槽位。

环境变量：`AUTO_COIN_SOURCE=platform_board`（默认）。若需紧急回退旧独立扫描，设 `legacy`。

- 会话内开关：VIP 短线自动跟投；不再另跑一套账户 LLM 选币
- 长线采纳不会进 `auto_coin_symbols` 短线池
- 训练期封锁**不**作用于看板跟投（看板已是管理员审过）

## 验收清单

1. VIP 关开关看不到完整看板；开开关能看短/长线理由并加入会话  
2. 管理员能重扫并看到扫描日志与完整候选  
3. 交易决策 LLM 仍是各账户自己的  
4. 长线采纳不会进 `auto_coin_symbols` 短线池  
5. 看板可筛 composite/trap/verdict；降质（非 AI）显性标注  
6. 有样本时卡片显示 24h 跟投绩效  
7. 固定交易对数量变化不影响 AI 选币槽位占用（`auto_coin_symbols` 长度独立计数）  
