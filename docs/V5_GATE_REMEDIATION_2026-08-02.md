# V5 / 开仓门控整改落地说明（2026-08-02）

## 前提（已遵守）

- 模拟盘日开仓数量不降、可加；短线与中长线配额解耦
- 中长线一体（`trend_follow` + `trend_daily_cap`），不设独立 swing 日配额
- 整改目标：提高有效样本质量、减少重复误杀——不是少开单

## 已落地

### 配额（P0）

| 键 | 值 |
|----|-----|
| `data/runtime_tuning.json` → `scalp_daily_cap` | **150** |
| → `trend_daily_cap` | **15** |
| Paper 单币 | **12**（`V5_MAX_SYMBOL_TRADES_PER_DAY_PAPER`） |

无效旧键已从 runtime 文件移除：`daily_cap_scalp` / `daily_cap_swing` / `daily_cap_trend_follow`。

### 按 nature 的 RR/TP（P1）

| nature | Live RR / min_tp | Paper RR / min_tp |
|--------|------------------|-------------------|
| scalp/intraday | 1.4 / 0.6% | 1.3 / 0.5% |
| ranging_mr | 既有 MR 下限 | 同左 |
| trend_follow/position/swing | 1.8 / 1.2% | 1.6 / 0.8% |

选币 `AUTO_COIN_V5_MIN_RR` 默认改为 **1.8**。

### 短线去重（P2）

- `SCALP_GATE_DEFER_REGIME_TO_V5=true`：ExecutionGate 不因 regime 硬拦
- `SCALP_MTF_HARD_ONLY_ANCHOR=true`：MTF/共振默认缩仓不 hold
- `SHORT_TIER_SKIP_CONFIDENCE=true`：去掉与 V5 重复的 conf 闸
- Paper 同向冷却 `SHORT_TIER_SAME_DIR_COOLDOWN_PAPER_S=1800`
- `SCALP_EV_MIN_PCT=0.0003`；Live EV 异常 fail-closed

### 文档/验收（P3）

- `README.md` V5 段已更新
- `scripts/v5_acceptance_check.py` 日交易线改为读 tier 配额合计

## 重启

改动涉及 `.env` 与 settings，需**重启后端**后全部生效。runtime_tuning 热改键可在运行中生效。
