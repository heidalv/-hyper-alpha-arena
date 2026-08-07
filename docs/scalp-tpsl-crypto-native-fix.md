# 短线 TP/SL 加密原生适配修复方案

> 日期: 2026-07-30
> 触发: 用户反馈"一直是微利就走，然后开仓，这不是刷手续费么"
> 实证: XMR 5 笔交易全部 breakeven_tp 出场，每笔 +0.9-1.6%，手续费 $0.0022/笔

## 一、问题诊断

### 1.1 交易数据分析

| 时间 | 方向 | 入场 | 出场 | 涨幅 | PnL | 出场原因 |
|------|------|------|------|------|-----|---------|
| 22:31 | 买 | 354.974 | 358.271 | +0.93% | +$0.41 | breakeven_tp |
| 22:23 | 买 | 354.974 | 359.06 | +1.15% | +$0.51 | breakeven_tp |
| 22:05 | 买 | 354.974 | 360.735 | +1.62% | +$0.72 | breakeven_tp |
| 21:34 | 买 | 354.974 | 359.465 | +1.27% | +$0.56 | breakeven_tp |
| 21:15 | 买 | 354.974 | 359.905 | +1.39% | +$0.61 | breakeven_tp |

特征:
- **5/5 笔都是 breakeven_tp 出场** — 100% 微利出场
- 每笔涨 0.9-1.6% 就走，PnL $0.4-0.7
- 每笔手续费 $0.0044（双边），净利几乎等于价格变动×仓位
- 反复开平 → 刷手续费

### 1.2 根因链路（6 层问题）

**层1: TP1 触发太早** (`paper_trading_engine.py` `_run_unified_staged_tp`)
```
trending: tp1_mult = 1.5  → 浮盈 1.5×ATR 触发 TP1
ranging:  tp1_mult = 2.0
```
XMR 5m ATR ≈ 0.5-0.8%，TP1 在 ~0.75-1.2% 就触发。

**层2: TP1 后 SL 推太紧**
```
TP1 触发 → SL → entry + ATR_price × 0.3  (≈ entry + 0.15-0.24%)
```
保本 SL 离入场价只有 0.15-0.24%，加密 5m 正常波动 0.5-1% 轻松击穿。

**层3: 保本 SL buffer 太小** (`profit_protection_manager.py`)
```
_BREAKEVEN_BUFFER = 0.003  (0.3%)   ← 传统股市参数
```
`breakeven_tp_progress = 0.70`（short tier）→ 盈利达 TP 的 70% 就推保本。

**层4: UnifiedExitStateMachine breakeven 触发太低** (`tier_exit_strategies.py`)
```
ShortTierExit:
  BREAKEVEN_TRIGGER_PCT = 2.0   (2% 浮盈就推保本)
  BREAKEVEN_OFFSET_PCT  = 0.005 (SL→entry+0.5%)
```

**层5: 5+ 张冲突的 TP/SL 参数表**
| 表 | scalp TP | scalp SL | RR |
|----|---------|---------|-----|
| tp_sl_authority.py | 2.5% | 1.2% | 2.08 |
| paper_tp_sl.py DEFAULT | 1.2% | 2.5% | 0.48 ← 反了！ |
| tp_sl_gates._NATURE_SL | — | 5.0% | — |
| tp_sl_gates._LIMITS | 0.5-8% | 1.2-5% | — |
| position_memory_manager | sl_base=2.5% | — | 1.5/1.2 |
| position_sizing_agent | — | 1.2% (floor) | — |

`paper_tp_sl.py` 的 `MIN_TP_SL_RATIO = 2.5` 强制 RR≥2.5，但 TP 默认只有 1.2%、SL 默认 2.5% → TP 被拉远到 6.25%，完全不切实际。

**层6: EV gate funding_rate NameError**
```python
# scalp_ev_gate.py L177
funding_rate = float(market_data.get("funding_rate", 0) or 0)  # ← market_data 未定义!
```
函数签名没有 `market_data` 参数，被 except 兜底，funding_cost 从未生效，EV 偏高。

### 1.3 对比传统市场 vs 加密 5m

| 指标 | 传统股市日线 | 加密 5m（应适配） |
|------|------------|-----------------|
| ATR% | 0.5-1.5% | 0.3-1.0% (BTC/ETH), 0.5-1.5% (alt) |
| 噪音带 | 0.1-0.3% | 0.3-0.8% |
| TP 目标 | 1-3% | 1.5-3% (至少 2× 噪音带) |
| SL | 0.5-1.5% | 0.8-1.5% (至少 1× ATR) |
| 保本 buffer | 0.1-0.3% | 0.5-1.0% (至少 1× ATR) |
| RR | 2-3 | 1.5-2.5 |
| 保本触发 | 70% of TP | 85%+ of TP |
| TP1 触发 | 1.5×ATR | 2-2.5×ATR |

## 二、修复方案

### 修复 A: REGIME_TP_PARAMS — 提升 TP1/TP2 触发门槛

**文件**: `backend/services/paper_trading_engine.py` L187-190

```python
# 旧
"trending": {"sl_mult":2.5, "tp1_mult":1.5, "tp2_mult":2.5, "tp3_mult":4.0, "trail_mult":2.5, "dd_hard":4.0},
"ranging":  {"sl_mult":3.0, "tp1_mult":2.0, "tp2_mult":3.5, "tp3_mult":5.0, "trail_mult":3.0, "dd_hard":4.0},
"extreme":  {"sl_mult":3.5, "tp1_mult":2.5, "tp2_mult":4.0, "tp3_mult":6.0, "trail_mult":3.5, "dd_hard":4.0},

# 新
"trending": {"sl_mult":2.0, "tp1_mult":2.0, "tp2_mult":3.0, "tp3_mult":5.0, "trail_mult":2.0, "dd_hard":4.0},
"ranging":  {"sl_mult":2.5, "tp1_mult":2.5, "tp2_mult":4.0, "tp3_mult":6.0, "trail_mult":2.5, "dd_hard":4.0},
"extreme":  {"sl_mult":3.0, "tp1_mult":3.0, "tp2_mult":5.0, "tp3_mult":8.0, "trail_mult":3.0, "dd_hard":4.0},
```
- tp1_mult 1.5→2.0: 不再在 1% 微利时就触发 TP1
- sl_mult 降低: 不再设过宽 SL
- trail_mult 降低: 追踪止损给更多空间

### 修复 B: TP1 SL push buffer — 从 ATR×0.3 提升到 ATR×0.8

**文件**: `backend/services/paper_trading_engine.py` `_run_unified_staged_tp` L2393-2402

```python
# 旧: SL → entry + ATR_price × 0.3  (≈ entry + 0.15%)
self._tighten_sl_unified(pos, _entry + _atr_price * 0.3 * _side_dir, "staged_tp1")

# 新: SL → entry + ATR_price × 0.8  (≈ entry + 0.4-0.8%)
self._tighten_sl_unified(pos, _entry + _atr_price * 0.8 * _side_dir, "staged_tp1")
```
保本 SL 给足 0.8×ATR 的呼吸空间，不被正常波动击穿。

### 修复 C: profit_protection_manager — 保本 buffer + 触发阈值

**文件**: `backend/services/profit_protection_manager.py`

```python
# 旧
_BREAKEVEN_BUFFER = 0.003             # 0.3%
_BREAKEVEN_ACTIVATION_MARGIN_PCT = 0.35  # 35% margin

# 新
_BREAKEVEN_BUFFER = 0.008             # 0.8% (crypto 5m 噪音带下限)
_BREAKEVEN_ACTIVATION_MARGIN_PCT = 0.50  # 50% margin (延迟激活)
```

**文件**: `backend/config/settings.py` TIER_PROTECTION_PARAMS

```python
# short tier
"breakeven_tp_progress": 0.85,  # 0.70 → 0.85 (等盈利达 TP 85% 才推保本)
```

### 修复 D: tier_exit_strategies — ShortTierExit breakeven

**文件**: `backend/services/exit/tier_exit_strategies.py` L152-153

```python
# 旧
BREAKEVEN_TRIGGER_PCT = 2.0    # 2% 浮盈推保本
BREAKEVEN_OFFSET_PCT  = 0.005  # SL→entry+0.5%

# 新
BREAKEVEN_TRIGGER_PCT = 3.0    # 3% 浮盈才推保本
BREAKEVEN_OFFSET_PCT  = 0.008  # SL→entry+0.8%
```

### 修复 E: tp_sl_gates — SL 兜底 + 硬限制

**文件**: `backend/services/full_auto/tp_sl_gates.py`

```python
# 旧
_NATURE_SL = {"scalp": 0.05, ...}
_LIMITS = {"scalp": (0.005, 0.08, 0.012, 0.05), ...}

# 新
_NATURE_SL = {"scalp": 0.02, ...}  # 5%→2% (SL 兜底不要太宽)
_LIMITS = {"scalp": (0.008, 0.04, 0.008, 0.025), ...}  # min_tp 0.8%, max_tp 4%, min_sl 0.8%, max_sl 2.5%
```

### 修复 F: paper_tp_sl — 默认值 + RR 比率

**文件**: `backend/services/full_auto/paper_tp_sl.py`

```python
# 旧
DEFAULT_TP_SL_BY_NATURE = {"scalp": (0.012, 0.025), ...}  # TP 1.2%, SL 2.5% ← TP<SL 反了!
MIN_TP_SL_RATIO = 2.5

# 新
DEFAULT_TP_SL_BY_NATURE = {"scalp": (0.020, 0.012), ...}  # TP 2%, SL 1.2%
MIN_TP_SL_RATIO = 1.8  # 2.5 太高，强制拉远 TP 导致 breakeven 频繁触发
```

### 修复 G: tp_sl_authority — 统一权威值

**文件**: `backend/services/tp_sl_authority.py`

```python
# 旧
NATURE_TP_SL["scalp"] = (0.025, 0.012)  # tp 2.5%, sl 1.2%

# 新
NATURE_TP_SL["scalp"] = (0.020, 0.012)  # tp 2%, sl 1.2% (RR=1.67, 适合 crypto scalp)
```

### 修复 H: _MIN_SL_DISTANCE_BY_NATURE — scalp SL 下限

**文件**: `backend/services/paper_trading_engine.py` L120-123

```python
# 旧
_MIN_SL_DISTANCE_BY_NATURE = {"scalp": 0.025, ...}  # 2.5%

# 新
_MIN_SL_DISTANCE_BY_NATURE = {"scalp": 0.010, ...}  # 1.0% (5m crypto SL 不需要 2.5% 那么宽)
```

### 修复 I: EV gate funding_rate NameError

**文件**: `backend/services/scalp/scalp_ev_gate.py`

`market_data` 变量未定义 → 从函数签名接收或从其他数据源获取 funding_rate。

### 修复 J: REGIME_TP_PARAMS — trailing_mult 降低

追踪止损从 2.5×ATR 降到 2.0×ATR，给更多空间不被正常波动甩出。

## 三、修复后预期效果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| TP1 触发点 | ~1% (1.5×ATR) | ~1.4% (2×ATR) |
| 保本 SL buffer | 0.3% | 0.8% |
| 保本触发 | 70% of TP | 85% of TP |
| breakeven_tp 频率 | ~100% | 预计 <40% |
| 单笔盈利 | $0.4-0.7 | 预计 $0.8-2.0 |
| 刷手续费 | 严重 | 缓解 |

## 四、修改文件清单

| 文件 | 修改项 |
|------|--------|
| paper_trading_engine.py | REGIME_TP_PARAMS, _MIN_SL_DISTANCE, TP1 SL push buffer |
| profit_protection_manager.py | _BREAKEVEN_BUFFER, _BREAKEVEN_ACTIVATION_MARGIN_PCT |
| config/settings.py | breakeven_tp_progress (short) |
| exit/tier_exit_strategies.py | ShortTierExit BREAKEVEN_TRIGGER/OFFSET |
| full_auto/tp_sl_gates.py | _NATURE_SL, _LIMITS (scalp) |
| full_auto/paper_tp_sl.py | DEFAULT_TP_SL, MIN_TP_SL_RATIO |
| tp_sl_authority.py | NATURE_TP_SL (scalp) |
| scalp/scalp_ev_gate.py | funding_rate NameError fix |
