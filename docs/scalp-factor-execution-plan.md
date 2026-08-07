# 短线因子系统全面整改执行文档

> 版本：v1.0 | 日期：2026-07-30
> 依据：factor-pipeline-deep-audit.md v2.0 + crypto-scalp-factor-optimization.md + 20篇arXiv文献

---

## 整改总览

| 阶段 | 内容 | 预计工时 | 影响面 |
|---|---|---|---|
| P0 | IC评估器bug修复 + 因子清理 | 1天 | 评估准确性 |
| P0 | 指标周期适配加密5m | 0.5天 | scalp信号质量 |
| P0 | 资金费率计入EV | 0.5天 | 开仓决策准确性 |
| P1 | TP/SL regime自适应 | 1天 | 止损止盈 |
| P1 | 周末时段过滤 | 0.5天 | 避开低流动性 |
| P1 | 持仓上限收紧 | 0.5天 | 风控 |
| P2 | 因子链条闭环（验证+清洗+影子期+退役） | 3天 | 因子质量 |
| P2 | 加密原生因子编码 | 3天 | 因子库重建 |
| P2 | 回测验证框架 | 2天 | 验证基础设施 |

---

## P0-1：IC评估器BUG修复

### 问题

`factor_ic_evaluator.py:155` 每次评估只取最近一批 `SignalTradeFeedback` 样本算IC，不是全部历史。

**实证**：`ai_gen_trend_rev` 存储IC=1.0/std=0，但用全部513条样本重算IC=0.12。

### 修改

**文件**：`backend/services/factor_ic_evaluator.py`

**1. 查询改为取全部历史样本（不只取最近一批）**

L109 `run_factor_ic_evaluation` 的查询逻辑：
```python
# 改前：只取 lookback_days 天的样本
cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
rows = db.query(SignalTradeFeedback).filter(
    SignalTradeFeedback.created_at >= cutoff.replace(tzinfo=None),
).all()

# 改后：取全部已配对样本（但权重按时间衰减）
rows = db.query(SignalTradeFeedback).filter(
    SignalTradeFeedback.signal_type.like("factor:%"),
    SignalTradeFeedback.trade_pnl.isnot(None),
).all()
```

**2. IC改用Rank IC（Spearman）替代Pearson**

L95 `_pearson` 函数替换为 Rank IC：
```python
from scipy.stats import spearmanr

def _rank_ic(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 30:  # 提高最小样本数（从8改到30）
        return None
    corr, _ = spearmanr(xs, ys)
    if corr is None or math.isnan(corr):
        return None
    return max(-1.0, min(1.0, float(corr)))
```

**3. MIN_SAMPLES 从8提高到30**

L32: `MIN_SAMPLES = 30`

**4. 清除IC=1.0/std=0的假数据**

执行SQL：
```sql
-- 标记IC=1.0的记录为可疑
UPDATE factor_quality_reports SET grade='SUSPECT' WHERE ic_mean >= 0.99;
-- 用全部样本重算后更新
```

---

## P0-2：因子清理

### 问题

1021个AI因子中38%IC为负（反向有害），99%是传统指标变体，全部无验证直接上线。

### 执行

**1. 保留IC>0.3且IC标准差>0.1的因子（约33个）**

```python
# 查询保留名单
SELECT factor_id FROM factor_quality_reports 
WHERE ic_mean > 0.3 AND factor_id IN (
    SELECT factor_name FROM factor_performance_logs 
    WHERE ic_value IS NOT NULL 
    GROUP BY factor_name 
    HAVING stddev(ic_value) > 0.1
)
```

**2. 移动988个低质量因子到隔离目录**

```bash
mkdir -p backend/services/factor_engine/factors/_ai_gen_quarantine/
# factor_loader.py L44 已有逻辑：if category_dir.name.startswith('_'): continue
# 所以 _ai_gen_quarantine 不会被加载
```

**3. 更新 factor_runtime_weights.json**

保留的33个因子权重设为1.0，其余全部设为0.1（退役）。

---

## P0-3：指标周期适配加密5m

### 文献依据

- Hurst 0.42→0.49（2402.11930）：市场趋有效，传统因子需更高频
- 5m/1H自相关显著（2003.13517）：但周期需匹配日内节律
- Hummingbot默认3m+MACD 21/42/9：竞品已适配加密

### 修改

**文件**：`backend/services/factor_engine/base_factors.py`

| 指标 | 当前 | 改后 | 修改位置 | 理由 |
|---|---|---|---|---|
| RSI | 14 | **7** | L687-703 | 5m×7=35min覆盖半小时动量 |
| ATR | 14 | **20** | L804-819 | 5m×20=100min≈1.7h覆盖日内节律 |
| MACD | 12/26, signal 9 | **8/21, signal 5** | L705-718 | 更快响应 |
| EMA趋势 | 9/21/50 | **8/13/21** | L922-938 | 斐波那契适合短周期 |
| BB/zscore | 20 | **40** | L761-790 | 5m×40≈3.3h覆盖半天 |
| ADX | 14 | **10** | L736-757 | 更短周期捕捉趋势切换 |
| momentum/roc | 10 | **6** | L720-734 | 加密动量衰减更快 |

### 具体代码

**RSI（L687-703）**：
```python
# 改前
if len(close) < 14: return float('nan')
gain = np.where(delta > 0, delta, 0)
loss = np.where(delta < 0, -delta, 0)
avg_gain = np.mean(gain[-14:])
avg_loss = np.mean(loss[-14:])

# 改后
_RSI_PERIOD = 7  # 加密5m适配（原14=股市日线）
if len(close) < _RSI_PERIOD: return float('nan')
gain = np.where(delta > 0, delta, 0)
loss = np.where(delta < 0, -delta, 0)
avg_gain = np.mean(gain[-_RSI_PERIOD:])
avg_loss = np.mean(loss[-_RSI_PERIOD:])
```

**ATR（L804-819）**：
```python
_ATR_PERIOD = 20  # 加密5m适配（原14=70min，改20=100min≈1.7h）
tr = np.maximum(...)
atr = np.mean(tr[-_ATR_PERIOD:])
```

**EMA趋势（L922-938）**：
```python
# 改前：EMA 9/21/50
ema9 = self._ema(close, 9)
ema21 = self._ema(close, 21)
ema50 = self._ema(close, 50)

# 改后：EMA 8/13/21
ema_fast = self._ema(close, 8)
ema_mid = self._ema(close, 13)
ema_slow = self._ema(close, 21)
```

---

## P0-4：资金费率计入EV

### 文献依据

- 资金费率Granger因果于价格（1912.03270）
- 资金感知策略优于经典（2605.06405）
- 做多高费率时持仓成本可达TP的3.75%

### 修改

**文件**：`backend/services/scalp/scalp_ev_gate.py:163-173`

```python
# 改前
round_trip_cost = (fee_rate + slippage) * 2

# 改后
funding_rate = float(market_data.get("funding_rate", 0) or 0)
expected_hold_hours = 0.5  # scalp平均30分钟
funding_cost = abs(funding_rate) * expected_hold_hours / 8  # 8h结算一次
round_trip_cost = (fee_rate + slippage) * 2 + funding_cost
```

---

## P1-1：TP/SL regime自适应

### 修改

**文件**：`backend/services/scalp/structure_stop_calculator.py:94-100`

```python
# 改前
sl_pct = max(0.005, min(0.015, sl_pct))
tp_pct = max(0.015, min(0.04, sl_pct * 2.5))

# 改后
regime = market_data.get("regime", {}).get("name", "unknown")
if regime == "ranging":
    rr_mult = 1.5; sl_min, sl_max = 0.008, 0.020
elif regime == "trending":
    rr_mult = 2.5; sl_min, sl_max = 0.005, 0.015
else:  # volatile/crash/unknown
    rr_mult = 0; sl_min, sl_max = 0.015, 0.030  # 不开仓
sl_pct = max(sl_min, min(sl_max, sl_pct))
tp_pct = max(0.015, min(0.04, sl_pct * rr_mult))
```

---

## P1-2：周末/时段过滤

### 文献依据

- 周末波动率略低（2111.15351）
- UTC 22:00-00:00流动性最薄

### 修改

**文件**：`backend/services/full_auto/loops/scalp_loop.py`

在 scalp_loop 开仓检查前加：
```python
from datetime import datetime, timezone
_now = datetime.now(timezone.utc)
_is_weekend = _now.weekday() >= 5  # Saturday=5, Sunday=6
_utc_hour = _now.hour

if _is_weekend:
    # 周末缩仓50%
    _size_multiplier *= 0.5
    logger.info(f"[ScalpRouter] 周末低流动性，缩仓50%")

# UTC 22-00 最薄时段
if 22 <= _utc_hour or _utc_hour < 1:
    _size_multiplier *= 0.7
    logger.info(f"[ScalpRouter] UTC低流动性时段({_utc_hour}h)，缩仓30%")
```

---

## P1-3：持仓上限收紧

**文件**：`backend/config/settings.py:1942`

```python
# 改前
AUTO_COIN_MAX_HOLD_HOURS_SHORT: int = int(os.getenv("AUTO_COIN_MAX_HOLD_HOURS_SHORT", "72"))

# 改后
AUTO_COIN_MAX_HOLD_HOURS_SHORT: int = int(os.getenv("AUTO_COIN_MAX_HOLD_HOURS_SHORT", "2"))
```

---

## P2-1：因子链条闭环

### 验证环节

**对保留的33个因子做walk-forward验证**

文件：`backend/services/factor_engine/factor_backtest_scorer.py`

```python
# 遍历33个保留因子，每个用6个月5m K线跑walk-forward(3折)
for factor_id in retained_factors:
    score = factor_backtest_scorer.score_formula(
        factor_id, symbols=['BTC','ETH','SOL'], period='5m', days=180
    )
    # 写入 factor_quality_reports
```

### 清洗环节

**正交去冗余**

文件：`backend/services/evolution/purge_pipeline.py`

```python
# 计算33个因子的IC时间序列相关矩阵
# |corr| > 0.8 的聚类，只保留IC最高的
# 目标：33 → ≤20个去冗余因子
```

### 影子期

**文件**：`backend/services/evolution/shadow_judge.py`

```python
# 新因子上线前在paper账户跑7天真实信号
# 记录每笔信号的forward return
# 7天后算真实Sharpe → Sharpe>0.5 + IC>0.02 → 晋升ACTIVE
```

### 退役机制

**文件**：`backend/services/factor_ic_evaluator.py`

```python
# IC连续7天<0 → 自动降权到0.5
# IC连续30天<0 → 自动退役（权重降到0.1）
# 移到 _ai_gen_quarantine/
```

---

## P2-2：加密原生因子编码

### 文献依据

| 因子 | IC证据 | arXiv |
|---|---|---|
| OFI | 最稳定短线IC | 2602.00776 |
| 资金费率 | Granger因果 | 1912.03270 |
| 链上净流入 | 1-6h预测力 | 2411.06327 |
| USDT mint | 5-30min正因子 | 2501.05232 |
| 一刻钟周期 | 样本外可预测 | 2607.09426 |
| 清算数据 | 日强平3.51% | 2102.04591 |

### 新建因子文件

每个因子一个 `.py` 文件，放在 `backend/services/factor_engine/factors/crypto_native/`：

```python
# factors/crypto_native/ofi_imbalance.py
@register_factor()
class OrderFlowImbalance(BaseFactor):
    """订单流失衡因子（加密原生，IC最稳定 2602.00776）"""
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 需要 LOB 数据（bid/ask volume）
        bid_vol = data.get('bid_volume', pd.Series(0, index=data.index))
        ask_vol = data.get('ask_volume', pd.Series(0, index=data.index))
        ofi = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-10)
        return ofi.rolling(20).mean().clip(-1, 1)
```

```python
# factors/crypto_native/funding_deviation.py
@register_factor()
class FundingRateDeviation(BaseFactor):
    """资金费率偏离因子（Granger因果 1912.03270）"""
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        funding = data.get('funding_rate', pd.Series(0, index=data.index))
        rolling_mean = funding.rolling(96).mean()  # 8h×12=96根5m
        rolling_std = funding.rolling(96).std()
        deviation = (funding - rolling_mean) / (rolling_std + 1e-10)
        return deviation.clip(-3, 3) / 3  # 归一化到[-1,1]
```

```python
# factors/crypto_native/liquidation_magnet.py
@register_factor()
class LiquidationMagnetFactor(BaseFactor):
    """清算磁吸强度因子（日强平3.51% 2102.04591）"""
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        liq_vol = data.get('liquidation_volume', pd.Series(0, index=data.index))
        oi = data.get('open_interest', pd.Series(1, index=data.index))
        intensity = liq_vol / (oi + 1e-10)
        return intensity.rolling(12).mean().clip(0, 1)
```

```python
# factors/crypto_native/quarter_hour_effect.py
@register_factor()
class QuarterHourEffect(BaseFactor):
    """一刻钟周期效应因子（样本外可预测 2607.09426）"""
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'timestamp' not in data.columns:
            return pd.Series(0, index=data.index)
        ts = pd.to_datetime(data['timestamp'], unit='s', utc=True)
        minute = ts.dt.minute
        # 整点（0/15/30/45分钟）给正信号
        is_quarter = (minute % 15 == 0).astype(float)
        return is_quarter * 2 - 1  # 0→-1, 1→+1
```

---

## P2-3：回测验证框架

### 设计

| 维度 | 方法 | 依据 |
|---|---|---|
| 数据 | 5m K线 6个月 | — |
| 滑点 | 按订单规模vs顶档深度（价格回复型） | 2305.07559 |
| 手续费 | Aster maker 0.005%×2 | — |
| 资金费率 | 8h结算×持仓时长×费率 | 1912.03270 |
| 清算 | 模拟维持保证金3X多/5X空 | 2102.04591 |
| 重尾 | 滚动窗+t分布 | 2402.11930 |
| 存活筛选 | 排除已退市币种 | 2308.08554 |
| 频率 | 因子5m，OFI用1s | 2607.09426 |

### 验证指标

| 指标 | 优化前基线 | 优化后目标 |
|---|---|---|
| 胜率 | 7% | >40% |
| 盈亏比 | 0.3-0.7 | >1.5 |
| 夏普 | 负 | >0.5 |
| 最大回撤 | — | <10% |

---

## 风险点

| 风险 | 缓解 |
|---|---|
| 改指标周期后旧回测数据失效 | 只向前用新参数，不回溯 |
| 清理988个因子可能影响已有策略 | 策略不直接依赖因子名，依赖因子合成信号方向 |
| 加密原生因子数据源不完整 | 先实现已有数据源的因子（funding/OI/清算），LOB/链上后续接 |
| 周末过滤可能错过周末行情 | 缩仓而非停仓，保留参与度 |

---

## 执行顺序

```
P0-1 IC评估器修复 → P0-2 因子清理 → P0-3 指标周期适配 → P0-4 资金费率计入EV
→ P1-1 TP/SL regime自适应 → P1-2 周末过滤 → P1-3 持仓上限
→ P2-1 链条闭环 → P2-2 加密原生因子 → P2-3 回测框架
→ 全量验证
```
