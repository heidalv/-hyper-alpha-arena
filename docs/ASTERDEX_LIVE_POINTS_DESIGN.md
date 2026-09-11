# Asterdex 实盘交易 × Rh 积分一体化 — 规划与设计（v2，2026-09）

## 1. 官方规则研究结论（docs.asterdex.com 原文要点）

来源：[Aster Convergence: Stage 6](https://docs.asterdex.com/program-and-rewards/points-and-campaigns/aster-convergence-stage-6) · [Stage 3 Rh 积分说明](https://docs.asterdex.com/stage-3-dawn/how-do-rh-points-work-in-aster-dawn-stage-3) · [api-docs CHANGELOG](https://github.com/asterdex/api-docs/blob/master/CHANGELOG.md)

| 规则 | 官方原文要点 | 对策略的含义 |
|---|---|---|
| 积分公式 | `Final = (交易 + 持仓 + Aster资产 + 清算 + 盈亏) × 团队加成 + 推荐` | 五类并行累加，团队加成乘数 |
| 权重 | **具体权重与公式不公开** | 只能按比例优化，计量为估算值 |
| Epoch | 每周一 00:00 UTC ~ 周日 23:59 UTC 结算 | 周维度考核 |
| 交易积分 | 手续费贡献 + **Maker 流动性贡献** + 币种加成；每小时更新 | **Maker 挂单 = 0 费率 + 白赚积分**，是唯一"零成本"积分来源 |
| 持仓积分 | 仅合约；规模 × 时长；**无上限**；T+1 结算 | 持仓本身就在赚分，不需要额外操作 |
| 资产积分 | 仅合约；ASTER/asBNB/USDF 作保证金；**无上限**；T+1 | 需全仓模式（改保证金语义，有爆仓风险，默认关） |
| 盈亏积分 | 每小时净盈亏（**不含资金费**）；每小时更新 | 正常交易顺带产生，双向都算 |
| 清算积分 | 被清算时按清算费计分 | **纯损失补偿，必须避免**（杠杆控制） |
| 团队加成 | 全赛季累计、不重置 | 账号运营层面，加入团队即可 |
| Wash 政策 | **对冲刷分/操纵/多账号刷分直接取消资格** | 同所同币严禁对开；只做单边方向仓 |
| 做市商 | MM 计划参与者不能赚积分 | 本系统非 MM，不受影响 |
| V3 认证 | 2026-09-01 起主钱包需有充值记录（-5050 DEPOSIT_REQUIRED） | 开通账户后需先充一笔 |
| STP | V3 提供自成交预防（EXPIRE_TAKER/MAKER/BOTH） | 可配置账户级防自成交 |

**核心洞察**：持仓积分/资产积分无上限 + Maker 0 费率计分 → 让"本来就要持有的实盘仓"自然挂单成交、自然持仓，就是最优积分打法；不需要任何额外刷量。

## 2. 总原则

1. **绝不刷分**：只优化「本来就要成交」的实盘单（资金费套利腿、主交易单），不新增任何以积分为目的的交易；
2. **执行即计量**：每次 Asterdex 实盘成交自动写入积分账本（估算值），官方快照对账；
3. **开关显式**：积分一体化的启用由**交易所配置里的专门开关**控制（默认关）；
4. **风险不变**：积分优化不得改变既有风控（仓位、杠杆、止损语义不变）。

## 3. 积分开关（交易所配置）

`exchange_credentials` 表新增（已实施）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `points_enabled` | bool 默认 false | **积分总开关**：开启后实盘 asterdex 成交才计量 + maker-first 才生效 |
| `points_config` | JSON | `{maker_first: bool, maker_timeout_s: float, asset_points_enabled: bool}` |

UI：交易所管理 → **API 凭证** 页 → 添加/编辑 **Asterdex** 凭证时出现「积分一体化」区块：
- 积分总开关（Switch，默认关）
- Maker 优先挂单开关 + 超时秒数（建议套利 30s / 主交易 90s）
- 资产积分开关（USDF/ASTER/asBNB 保证金，默认关，附全仓模式风险提示）
- 凭证卡片上显示「积分 ON/OFF」徽标

后端语义（`live_points_engine.get_policy()`，60s 缓存）：
- `points_enabled=false` → `record_fill/record_close` 静默跳过、maker-first 不启用（普通市价单照常）；
- 非 asterdex 凭证的积分字段恒 False/None（路由层强制）。

## 4. 五类积分的执行策略与风险边界

| 类别 | 执行策略 | 风险边界 |
|---|---|---|
| 交易积分 | Maker-first post-only 挂 best bid/ask，超时撤单市价兜底；平仓始终市价（离场优先） | 套利 asterdex 腿 maker 等待 ≤30s（另一腿先成交，delta 窗口受控）；主交易 ≤90s |
| 持仓积分 | 不干预持仓时长（套利腿本就持仓收资金费；主交易按策略持仓） | 持仓时长由交易逻辑决定，积分为副产品 |
| 资产积分 | 默认**关**；开启需全仓模式 + 持有 USDF/ASTER/asBNB | 改变保证金语义/爆仓风险，需用户显式确认 |
| 盈亏积分 | 正常交易顺带 | — |
| 清算积分 | 不追求（纯损失） | 杠杆上限不因积分调高 |
| Wash 防护 | 资金费套利 asterdex 腿开仓前检查同所同币已有持仓 → 有则跳过（`wash_guard`） | 官方取消资格红线 |

## 4.1 策略层配合（兼容性设计，2026-09 已实施）

1. **双所价差定腿**（修复 EV 错配）：套利决策不再用单所费率——用 `perp_funding` 双所最新费率：
   - 费率高的所 = 空腿（收 funding），费率低的所 = 多腿（付 funding），净收益恒 = |价差|；
   - 年化 = |价差|×3×365，低于门槛（默认 15%）剔除；
   - **积分开关开启时门槛降至 6%**（`ARB_FUNDING_MIN_ANNUAL_WITH_POINTS`）：maker 0 费率 + 流动性/持仓积分是价差外的确定性补贴；
   - asterdex 腿无论多空都接积分计量与 wash 守卫。
2. **双腿编排**：非 asterdex 腿先开（市价秒成），asterdex 腿最后开（maker 挂单等待窗口内只暴露一条腿）。
3. **失败处理**：任一腿失败 → 紧急市价平掉已开腿（保留原语义，扩展到任意顺序）。
4. **保证金预检**：asterdex 可用余额 < 名义/杠杆×1.2 → 跳过下单。
5. **杠杆跟随策略配置**：`arb_config.yaml funding.leverage`（默认 3x，env `ARB_FUNDING_LEVERAGE`）——套利是实盘合约交易的**附带**，双腿对冲、低杠杆即可，不写死在代码里。
5. **平仓核算**：`close_position` 按 exchange_long/short 平腿，asterdex 腿平仓时结算持仓积分（小时数来自开仓登记）。
6. **主交易路径**：当前实盘主交易在 binance；切 asterdex 后由 Phase 2b 接入同一计量/执行策略。
7. **与 S8/rebate 域并存**：rebate 自动策略仍处官方暂停（rule_sync_gate），互不触发；paper 刷分系统与 live 积分引擎职责分离（wash_trade_avoider 只管 paper）。

## 5. 计量与对账

- 账本表 `asterdex_live_points_events`（Analytics DB）：open 事件记交易积分（fee×100 + maker $1k×1.0），close 事件记持仓积分（$1k×0.5×小时）；
- 积分模型单一来源 `rule_registry.STAGE6_POINT_MODEL`（权重为可调估算，YAML 可覆盖）；
- `GET /api/rebate/asterdex-points/summary` 返回汇总 + 开关策略 + 官方快照对账（`totalRhPoints`，300s 缓存）；
- UI：交易所管理 → **积分账本** tab（7 天汇总、maker 占比、官方积分、空投资格、投机性标注）。

## 6. 实施状态

- [x] Phase 1：账本模型/计量引擎/maker-first 下单/套利主腿接入（wash 守卫 + 计量）/summary API/前端账本面板
- [x] Phase 2a：交易所配置积分开关（DB 列 + 路由 + 凭证表单 Switch + 卡片徽标）
- [ ] Phase 2b：主交易会话 asterdex 实盘单接入同一计量/执行策略（账户切 asterdex 后生效）
- [ ] Phase 3：每日自动对账 + 漂移告警、周报（epoch 维度）、团队加成/推荐展示

## 7. 待办依赖（用户操作）

1. Asterdex 开通合约账户 + 一笔小额充值（2026-09-01 新规 -5050）；
2. 开通后重跑 `scripts/asterdex_register_key.py` 派生 apiKey/apiSecret 入库；
3. 在交易所配置里打开 Asterdex 凭证的积分开关（如需 maker-first 等策略微调）；
4. Coinglass 免费 key（链上净流入层，与积分无关但同样待办）。
