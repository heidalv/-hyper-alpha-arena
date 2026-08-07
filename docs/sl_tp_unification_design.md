# 止损体系统一专项方案（Q1 → 具体合并方案）

> 日期: 2026-07-21
> 关联: 交易系统四大核心问题根因分析报告 Q1
> 状态: **已实施** — 全部 SL/TP 路径已收敛到单一入口

---

## 一、问题回顾

根因分析报告指出：系统存在**多套并行的 SL/TP 计算路径**，各路径使用不同的配置表、不同的
vol-band 查询逻辑、不同的 feature gate，导致同一笔交易在不同代码路径上可能得到截然不同的
止损价。

### 修复前的问题路径清单

| # | 文件 | 函数 | 问题 |
|---|------|------|------|
| 1 | `tp_sl_prices.py` | `compute_initial_tp_sl_prices()` | 三层 feature gate (`stage_e_active` + `RISK_USE_TIER_TP_SL_V2` + `RISK_USE_VOL_BAND_DEFAULTS`) 任一为 False 就退回旧版固定值 |
| 2 | `ai_decision_service.py` L2790 | TP/SL auto-fix (buy/sell) | 复制了 V2 逻辑但 **hardcode `"mid"` vol-band**，不看实际波动率 |
| 3 | `ai_decision_service.py` L3145 | Phase 3B hold→buy/sell TP/SL | 同上，hardcode `"mid"` + 仍检查 `RISK_USE_TIER_TP_SL_V2` gate |
| 4 | `ai_decision_service.py` L3590 | LLM Fallback 降级路径 | **第三处 hardcode `"mid"`** + 直接读 V1 `TIER_TP_SL_DEFAULTS` |
| 5 | `master_execution.py` L1851 | V5 统一门控 gate 检查 | **硬编码 `_gate_sl_pct=0.03` / `_gate_tp_pct=0.06`**，不查 vol-band |
| 6 | `master_execution.py` L1943 | 编排器覆盖 override TP/SL | 直接读 V1 `TIER_TP_SL_DEFAULTS`，不走 vol-band 分层 |
| 7 | `structure_stop_calculator.py` | `compute_sl_tp()` SL 钳制 | 硬编码 `max(0.005, min(0.015, ...))`，与 V2 的 low-band sl=2.5% 冲突 |
| 8 | `structure_stop_calculator.py` | `compute_sl_tp_v2()` | **函数名错误** `compute_tp_sl_prices`（不存在），**kwarg 不匹配** (`symbol` vs `sym`), **缺少 action 参数** → 静默 ImportError 回退旧路径 |
| 9 | `tp_sl_gates.py` | `_MIN_SL` 常量 | 与 V2 配置的最低 SL (2.5%) 不对齐 |
| 10 | `decision_core/pipeline.py` L390 | `_tier_tp_sl_defaults()` | 直接读 V1 配置，缺少 symbol→vol-band 查询 |
| 11 | `smart_prompt_generator.py` L477 | Prompt 展示 TP/SL | 直接读 V1 配置，给 LLM 的参数与实际不一致 |

---

## 二、统一后的架构

### 核心原则
**单一入口**: 所有 SL/TP 计算必须委托 `compute_initial_tp_sl_prices()`

### 调用拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                     SL/TP 计算统一入口                            │
│         compute_initial_tp_sl_prices()                          │
│         (backend/services/full_auto/tp_sl_prices.py)            │
│                                                                 │
│  1. Agent SL 路径 (swing_agent/trend_agent 的 structure_sl)     │
│  2. V2 波动率分层路径 (TIER_TP_SL_DEFAULTS_V2)                   │
│     → get_vol_band(symbol) 查 vol-band                          │
│     → ATR multiplier 调整                                       │
│  3. 旧版 fallback (TIER_TP_SL_DEFAULTS)                         │
│                                                                 │
│  Returns: (tp_price, sl_price, tp_sl_source)                    │
└──────────┬──────────┬──────────┬──────────┬────────────────────┘
           │          │          │          │
    ┌──────▼──┐ ┌─────▼───┐ ┌───▼────┐ ┌───▼──────────┐
    │  master │ │ AI决策  │ │ scalp  │ │ tp_sl_gates  │
    │  exec   │ │ service │ │ struct │ │ (_MIN_SL)    │
    └─────────┘ └─────────┘ └────────┘ └──────────────┘
```

### 所有调用方的修改

#### 1. `tp_sl_prices.py` — 统一入口（P1 修复）
```python
# 移除三层 feature gate
_use_v2 = True  # V2 路径默认生效（原: stage_e_active() and RISK_USE_TIER_TP_SL_V2）
```

#### 2. `ai_decision_service.py` L2790 — buy/sell auto-fix（Q1 修复）
```python
# 旧: RISK_USE_TIER_TP_SL_V2 gate + hardcode "mid" vol-band
# 新: 委托统一入口
from backend.services.full_auto.tp_sl_prices import compute_initial_tp_sl_prices
_tp_p, _sl_p, _ = compute_initial_tp_sl_prices(
    tier=_tier, action="buy" if operation == "buy" else "sell",
    ref_price=current_price, sym=symbol,
)
```

#### 3. `ai_decision_service.py` L3145 — Phase 3B（Q1 修复）
```python
# 同上，委托统一入口
from backend.services.full_auto.tp_sl_prices import compute_initial_tp_sl_prices
_p3b_tp, _p3b_sl, _ = compute_initial_tp_sl_prices(
    tier=_p3b_tier, action="buy" if new_op == "buy" else "sell",
    ref_price=current_price, sym=_p3b_sym,
)
```

#### 4. `structure_stop_calculator.py` — SL 钳制 + V2 委托（S4 + Q1-bugfix）
```python
# SL 钳制从 V2 配置动态读取（不再硬编码 0.5%-1.5%）
_sl_min, _sl_max = self._get_v2_sl_range(atr_pct)

# V2 委托修正：正确的函数名 + kwarg + action 参数
from backend.services.full_auto.tp_sl_prices import compute_initial_tp_sl_prices
tp_price, sl_price, _ = compute_initial_tp_sl_prices(
    tier=tier, action=_action, ref_price=price, atr_pct=atr_pct,
    sym=market_data.get("symbol", ""),
)
```

#### 5. `tp_sl_gates.py` — `_MIN_SL` 对齐（P0 修复）
```python
# _MIN_SL 已对齐 V2 最低 SL 配置（low-band short = 2.5%）
```

---

## 三、V2 波动率分层配置（TIER_TP_SL_DEFAULTS_V2）

| vol-band | tier | TP% | SL% | 盈亏比 |
|----------|------|-----|-----|--------|
| low | short | 5.0% | 2.5% | 2.0:1 |
| low | mid | 5.0% | 3.5% | 1.4:1 |
| low | long | — (D14分批) | 8.0% | — |
| mid | short | 6.0% | 3.0% | 2.0:1 |
| mid | mid | 6.5% | 4.5% | 1.4:1 |
| mid | long | — | 9.5% | — |
| high | short | 8.0% | 4.0% | 2.0:1 |
| high | mid | 9.0% | 6.0% | 1.5:1 |
| high | long | — | 12.0% | — |
| x-high | short | 11.0% | 5.5% | 2.0:1 |
| x-high | mid | 12.0% | 8.0% | 1.5:1 |
| x-high | long | — | 16.5% | — |

### vol-band 查询逻辑
```python
get_vol_band(symbol) → 'low' | 'mid' | 'high' | 'x-high'
# 基于 DEFENSIVE_VOLATILITY_TIERS_V2.symbol_vol_map 静态映射
# 未知 symbol → fallback 'mid' + 红色告警
```

---

## 四、`structure_stop_calculator._get_v2_sl_range()` 方法

根据实时 ATR% 动态映射到 vol-band，再从 V2 配置读取 SL 范围：

| ATR% | vol-band | V2 short SL | SL min (×0.6) | SL max |
|------|----------|------------|---------------|--------|
| <1.5% | low | 2.5% | 1.5% | 2.5% |
| 1.5%-3% | mid | 3.0% | 1.8% | 3.0% |
| 3%-5% | high | 4.0% | 2.4% | 4.0% |
| >5% | x-high | 5.5% | 3.3% | 5.5% |

> 注：SL 钳制下限取 V2 short SL × 0.6，确保结构止损器不会产生比 V2 配置更窄的止损。

---

## 五、验证清单

| 检查项 | 状态 | 验证方式 |
|--------|------|----------|
| `RISK_USE_TIER_TP_SL_V2` 从活跃路径移除 | ✅ | grep 确认仅存在于定义和注释中 |
| `compute_initial_tp_sl_prices` 是核心入口 | ✅ | ai_decision(3处) + master_execution(2处) + structure_stop 全部委托 |
| `compute_tp_sl_prices` 函数名错误已修复 | ✅ | structure_stop_calculator 已修正 |
| kwarg 匹配 (sym/ref_price/action) | ✅ | 与函数签名一致 |
| SL 钳制从 V2 配置读取 | ✅ | `_get_v2_sl_range()` |
| `_MIN_SL` 与 V2 最低 SL 对齐 | ✅ | tp_sl_gates.py |
| master_execution gate SL/TP 从 V2 读取 | ✅ | L1851 改为 get_vol_band + V2 |
| master_execution override TP/SL 委托统一入口 | ✅ | L1943 改为 compute_initial_tp_sl_prices |
| ai_decision_service fallback 委托统一入口 | ✅ | L3590 改为 compute_initial_tp_sl_prices |
| decision_core/pipeline V2 优先 | ✅ | `_tier_tp_sl_defaults` 增加 symbol→V2 查询 |
| smart_prompt_generator V2 优先 | ✅ | Prompt 展示值与实际一致 |
| V1 `TIER_TP_SL_DEFAULTS` 仅作 fallback | ✅ | grep 确认所有引用都在 V2 fallback 分支中 |

---

## 六、未来优化方向（非本次范围）

1. **per-symbol ATR 参数表**: 当前 vol-band 是静态映射（symbol_vol_map），未来可改为动态 ATR 计算 → 自动 band 归类
2. **杠杆缩放统一**: `ai_decision_service` 中的 `_lev_scale = 1.0 / max(lev ** 0.15, 1.0)` 是本地计算，未来可合入 `compute_initial_tp_sl_prices` 消除重复
3. **废弃 TIER_TP_SL_DEFAULTS (V1)**: 所有路径已走 V2，V1 配置仅作为 fallback，可在下个版本删除
