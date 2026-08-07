# 因子系统升级设计文档

> **状态**：待执行
> **日期**：2026-06-18
> **前提**：基于对 `factor_engine/`、`scalp_factor_router.py`、`live_pipeline_backtest_engine.py` 的深度审计

---

## 一、现状诊断（5 个根因）

### 根因 1：三套因子系统未打通

| 系统 | 因子数 | 进生产 | 文件 |
|---|---|---|---|
| 生产引擎 `FactorEngine`（硬编码） | 21 | ✅ 唯一在用 | `base_factors.py:53-624` |
| Registry 注册（装饰器自动扫描） | 130+ | ❌ 死代码 | `factor_registry.py` + `factors/` 8 子目录 |
| AI 生成 | 127 | ❌ 死代码 | `backend/factor_engine/factors/ai_generated/` |

生产链路全部调 `factor_engine.compute_all_factors()`（`base_factors.py:209`），它遍历 `self.FACTORS` 字典（21 个硬编码因子）。Registry 的 130+ 因子和 AI 的 127 个因子**完全不参与计算**。

### 根因 2：评估/衰变/动态权重三件套写了没用

| 组件 | 文件 | 实现状态 | 生产调用 |
|---|---|---|---|
| 因子评估（IC/ICIR/换手/单调性） | `factor_evaluator.py` | ✅ 完整 | ❌ 零调用 |
| 因子衰变监控 | `factor_decay_monitor.py` | ✅ 完整 | ❌ 零调用 |
| 动态权重（6 regime 查表） | `factor_weighting.py` | ✅ 完整 | ❌ `generate_signals` 生产全用等权 |

### 根因 3：AI 因子发现双重 Bug

- **目录错位**：`ai_factor_discovery_service.py:161` 写入 `backend/factor_engine/factors/ai_generated/`，但 `factor_loader.py:30` 只扫 `backend/services/factor_engine/factors/`
- **缩进破损**：生成模板的 `{factor.python_code}` 整体 0 缩进（`IndentationError`）
- **缺装饰器**：生成的因子类没有 `@register_factor()`
- **结果**：127 个 AI 因子文件全部是死代码

### 根因 4：回测引擎无因子级反馈

`live_pipeline_backtest_engine.py:627` 调 `compute_all_factors` + `generate_signals`，但：
- 回测结果不回写因子级 IC/贡献度
- 无 "factor-only" 回测路径
- 因子只是 pipeline_signal 的一个子输入（权重 0.3），无独立评估

### 根因 5：ScalpFactorRouter 回退逻辑质量低

`scalp_factor_router.py:158-176` 的回退逻辑（无 factor_signal 时）：
- RSI（反向，±50）+ MACD（顺势，±30）+ EMA（顺势，±20）直接相加
- **量纲混乱**：RSI 是 0-100 倒映、MACD 是绝对价格差×100、EMA 是比例×20
- **反向+顺势混用**会互相抵消
- **丢失了生产引擎 21 因子里最有价值的** taker_ratio/oi_delta/funding_rate/cvd_ratio

---

## 二、升级目标

1. **统一因子入口**：一套注册 + 一套计算 + 一套评估，消除三套系统
2. **接入评估闭环**：IC/ICIR 实时评估 → 衰变监控 → 自动降权/退休
3. **修复 AI 因子发现**：目录对齐 + 模板修复 + 评估准入
4. **因子级回测反馈**：回测产出因子 IC → 反馈权重优化
5. **ScalpRouter 直接调因子引擎**：不用手搓 RSI/MACD 回退

---

## 三、详细设计

### 3.1 统一因子入口（根因 1）

**现状**：`FactorEngine`（`base_factors.py:53`）用 `self.FACTORS` 字典硬编码 21 个因子。`FactorRegistry`（`factor_registry.py:309`）用装饰器注册 130+ 因子。两者完全独立。

**改造**：让 `FactorEngine.compute_all_factors` 从 `FactorRegistry` 动态加载因子，而非只遍历硬编码字典。

```python
# base_factors.py FactorEngine 改造
class FactorEngine:
    def __init__(self):
        self.FACTORS = {}        # 保留硬编码因子（向后兼容）
        self._register_all_factors()
        self._merge_registry()   # 新增：合并 Registry 因子

    def _merge_registry(self):
        """从 FactorRegistry 合并已注册因子到 self.FACTORS。"""
        try:
            from backend.services.factor_engine.factor_registry import registry
            from backend.services.factor_engine.factor_loader import FactorLoader
            # 确保所有因子已发现加载
            FactorLoader().discover_and_load_all()
            for factor_id, factor_cls in registry.list_all().items():
                if factor_id not in self.FACTORS:
                    self.FACTORS[factor_id] = {
                        'category': factor_cls.category,
                        'name': factor_cls.name,
                        'compute': self._adapter_compute(factor_cls),
                    }
        except Exception as e:
            logger.warning(f"[FactorEngine] Registry 合并失败（降级为纯硬编码）: {e}")

    @staticmethod
    def _adapter_compute(factor_cls):
        """适配 BaseFactor 子类的 calculate 为 compute_all_factors 兼容的签名。"""
        def _compute(klines, market_data=None):
            instance = factor_cls()
            data = {'klines': klines, 'market_data': market_data or {}}
            result = instance.calculate(data)
            return float(result) if result is not None else None
        return _compute
```

**向后兼容**：硬编码 21 因子保留（key 冲突时硬编码优先），Registry 因子作为扩展。现有调用 `factor_engine.compute_all_factors(klines, market_data)` 签名不变，返回 `Dict[str, FactorValue]` 不变。

**影响范围**：`compute_all_factors` 的所有调用点自动获得 130+ 因子（行 209 的返回字典变大）。下游 `FactorSignalGenerator.generate_signals` 自动处理更多因子。

**风险控制**：
- Registry 因子计算失败不影响硬编码因子（try/except per factor）
- 新增因子初始权重 = 硬编码因子均权（不抢权重）
- 可配置开关 `FACTOR_MERGE_REGISTRY=true/false`（默认 true）

### 3.2 接入评估闭环（根因 2）

**现状**：`FactorEvaluator` / `FactorDecayMonitor` / `DynamicFactorWeighting` 三个组件完整但零生产调用。

**改造**：在 `compute_all_factors` 之后接入评估 + 权重。

```python
# 新增：factor_evaluation_pipeline.py
class FactorEvaluationPipeline:
    """因子评估流水线 — 衔接 compute_all_factors 和 generate_signals。"""

    def __init__(self):
        from backend.services.factor_engine.factor_evaluator import FactorEvaluator
        from backend.services.factor_engine.factor_decay_monitor import FactorDecayMonitor
        from backend.services.factor_engine.factor_weighting import DynamicFactorWeighting
        self.evaluator = FactorEvaluator()
        self.decay_monitor = FactorDecayMonitor()
        self.weighting = DynamicFactorWeighting()

    def compute_weighted_signals(
        self,
        factor_values: Dict[str, FactorValue],
        market_data: Dict,
        forward_returns: Optional[pd.Series] = None,
    ) -> CompositeSignal:
        """计算加权因子信号（替代 generate_signals 的等权调用）。"""
        # 1. 评估（如果有前瞻收益数据）
        if forward_returns is not None:
            for name, fv in factor_values.items():
                ic = self.evaluator.compute_ic(fv.value, forward_returns)
                self.decay_monitor.record_ic(name, ic)

        # 2. 衰变惩罚
        weight_penalties = {}
        for name in factor_values:
            weight_penalties[name] = self.decay_monitor.get_factor_weight_penalty(name)

        # 3. 市场状态自适应权重
        regime = self.weighting.detect_regime(factor_values)
        base_weights = self.weighting.get_regime_weights(regime)

        # 4. 合成：base_weights × penalty
        final_weights = {k: base_weights.get(k, 1.0) * weight_penalties.get(k, 1.0)
                         for k in factor_values}

        # 5. 生成信号
        from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator
        return FactorSignalGenerator().generate_signals(factor_values, weights=final_weights)
```

**接入点**：
- **实时（live tick）**：`full_auto_trading_service.py:13291` 的 factor_engine handler → 用 `compute_weighted_signals` 替代 `generate_signals(等权)`
- **回测**：`live_pipeline_backtest_engine.py:633` → 同上
- **ScalpRouter**：`scalp_factor_router.py` 的 `_extract_factor_signal` → 优先用 pipeline 输出

**评估数据来源**：
- 实时模式：无前瞻收益（未来未知），只做衰变惩罚（基于历史 IC 滑动窗口）+ regime 权重
- 回测模式：有前瞻收益，完整 IC 评估 + 权重学习

**调度频率**：
- IC 评估：每次回测后批量计算（离线）
- 衰变检查：每 7 天扫描一次（`FactorDecayMonitor.check_interval_days=7`）
- 权重调整：每个 tick 实时（regime 检测 + 衰变惩罚）

### 3.3 修复 AI 因子发现（根因 3）

**Bug 1 目录错位**：
```python
# ai_factor_discovery_service.py:161 修复
# 旧：os.path.join("backend", "factor_engine", "factors", "ai_generated")
# 新：
_OUTPUT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "services", "factor_engine", "factors", "ai_generated"
)
```

**Bug 2 缩进破损 + 缺装饰器**：
```python
# ai_factor_discovery_service.py:131-149 inject_factor 修复
_FACTOR_TEMPLATE = '''"""AI 生成因子（自动生成，勿手改）。"""
from backend.services.factor_engine.factor_base import BaseFactor, FactorCategory
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class {factor.class_name}(BaseFactor):
    """{factor.description}"""
    factor_id = "{factor.factor_id}"
    category = FactorCategory.{factor.category}
    name = "{factor.display_name}"

    def calculate(self, data):
        klines = data.get("klines")
{factor.indented_code}
'''
# 关键：{factor.indented_code} 是 8 空格缩进的 calculate 逻辑
```

**评估准入**：AI 生成因子后，先写入 `_pending/` 子目录（不直接生效），跑 7 天影子 IC 评估，IC > 0.02 才迁移到正式目录。

### 3.4 因子级回测反馈（根因 4）

**改造**：`live_pipeline_backtest_engine.py` 新增 `_evaluate_factors_contribution` 方法。

```python
class LivePipelineBacktestEngine:
    def run(self, ...):
        # ... 现有回测逻辑 ...
        result = self._calculate_metrics(...)

        # 新增：因子贡献度评估
        factor_eval = self._evaluate_factors_contribution(trades, klines_df)
        result.factor_ic_report = factor_eval  # 每个因子的 IC/ICIR/贡献度
        return result

    def _evaluate_factors_contribution(self, trades, klines_df):
        """评估每个因子对交易盈亏的预测能力。"""
        from backend.services.factor_engine.factor_evaluator import FactorEvaluator
        evaluator = FactorEvaluator()

        report = {}
        for factor_name in factor_engine.FACTORS:
            factor_series = self._extract_factor_series(factor_name, klines_df)
            forward_returns = klines_df["close"].pct_change(5).shift(-5)
            metrics = evaluator.evaluate_factor(factor_name, factor_series, forward_returns)
            report[factor_name] = metrics  # IC/ICIR/grade/decay

        # 回写衰变监控
        from backend.services.factor_engine.factor_decay_monitor import decay_monitor
        for name, m in report.items():
            decay_monitor.record_ic(name, m.ic_mean)

        return report
```

**反馈链**：回测 → 因子 IC → `decay_monitor.record_ic` → 下次实时 tick 的 `compute_weighted_signals` 自动使用更新后的衰变惩罚。

### 3.5 ScalpRouter 直接调因子引擎（根因 5）

**改造**：`scalp_factor_router.py` 的 `_extract_factor_signal` 移除手搓 RSI/MACD/EMA 回退，改为直接调因子引擎。

```python
class ScalpFactorRouter:
    def _extract_factor_signal(self, symbol, market_data):
        # 优先：QAA factor_engine handler 的输出
        factor_signal = market_data.get("factor_signal") or market_data.get("composite_signal")
        if factor_signal:
            # ... 现有逻辑不变 ...

        # 回退：直接调因子引擎（替代手搓 RSI/MACD/EMA）
        klines = market_data.get("klines")
        if klines is not None and len(klines) > 20:
            from backend.services.factor_engine.base_factors import factor_engine
            factor_values = factor_engine.compute_all_factors(klines, market_data)
            from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator
            composite = FactorSignalGenerator().generate_signals(factor_values)
            score = int(abs(composite.direction) * composite.strength * 100)
            direction = "long" if composite.direction > 0.1 else "short" if composite.direction < -0.1 else "neutral"
            breakdown = {k: v.direction for k, v in composite.signals.items()}
            return score, direction, breakdown

        # 最终回退：无 K线数据，不交易
        return 0, "neutral", {}
```

**效果**：ScalpRouter 回退路径从 3 个指标（RSI/MACD/EMA）升级为 21+ 个因子（含 taker_ratio/oi_delta/funding_rate/cvd_ratio 等衍生品因子）。

---

## 四、实施顺序

| 步骤 | 内容 | 风险 | 验证标准 |
|---|---|---|---|
| 1 | 统一因子入口（`_merge_registry`） | 中（因子数 21→150+，计算变慢） | compute_all_factors 返回 150+ 因子无报错 |
| 2 | 接入评估闭环（`FactorEvaluationPipeline`） | 低（新增不替代） | generate_signals 用加权替代等权 |
| 3 | ScalpRouter 直接调因子引擎 | 低（改善回退质量） | 回退路径用 21+ 因子替代 3 指标 |
| 4 | 修复 AI 因子发现（目录+模板） | 低（修复死代码） | 新生成因子能被 loader 发现 + 注册 |
| 5 | 因子级回测反馈 | 中（回测变慢） | 回测产出 factor_ic_report |
| 6 | 全量测试 + 模拟盘 24h | — | 因子数 150+、权重生效、衰变监控有输出 |

---

## 五、配置项

```python
# settings.py 新增
FACTOR_MERGE_REGISTRY = True           # 合并 Registry 因子到生产引擎
FACTOR_EVALUATION_ENABLED = True       # 启用评估闭环
FACTOR_DECAY_CHECK_INTERVAL_DAYS = 7   # 衰变检查间隔
FACTOR_AI_DISCOVERY_ENABLED = False    # AI 因子发现（修好后默认关，手动开）
FACTOR_AI_PENDING_DAYS = 7             # AI 因子影子评估天数
```

---

## 六、与现有系统的兼容性

| 现有模块 | 改动 | 兼容性 |
|---|---|---|
| `base_factors.py` FactorEngine | 新增 `_merge_registry` | 向后兼容（硬编码因子不变） |
| `factor_signal_generator.py` | 不改（已有 weights 参数） | 完全兼容 |
| `factor_evaluator.py` | 不改（被新 pipeline 调用） | 从死代码变活 |
| `factor_decay_monitor.py` | 不改（被新 pipeline 调用） | 从死代码变活 |
| `factor_weighting.py` | 不改（被新 pipeline 调用） | 从死代码变活 |
| `scalp_factor_router.py` | 改 `_extract_factor_signal` | 接口不变 |
| `live_pipeline_backtest_engine.py` | 新增 `_evaluate_factors` | 不影响现有回测 |
| `ai_factor_discovery_service.py` | 修目录 + 模板 | 修复死代码 |
| QAA TickOrchestrator | 不动 | 完全兼容 |
| full_auto _execute_master_decisions | 不动 | 完全兼容 |

---

## 七、预期收益

| 指标 | 当前 | 目标 |
|---|---|---|
| 生产因子数 | 21 | 150+（Registry 合并） |
| 权重方式 | 等权（全一样） | regime 自适应 + 衰变惩罚 |
| 因子评估 | 无 | IC/ICIR 实时 + 回测反馈 |
| AI 因子 | 127 个死代码 | 修复后可自动生成+评估+准入 |
| ScalpRouter 回退 | 3 指标（量纲混乱） | 21+ 因子（含衍生品） |
| 衰变处理 | 无 | 自动降权/退休 |

---

## 八、开放问题

1. **Registry 因子计算性能**——从 21 个到 150+ 个，`compute_all_factors` 耗时可能从 <50ms 增到 200ms+。需测压，必要时做异步并行计算。
2. **因子冗余**——150+ 因子里很多高度相关（如 rsi_7/rsi_14/sma_5/sma_10）。需用正交性检验（已有 `check_orthogonality`）做去重。
3. **AI 因子质量**——LLM 生成的因子代码质量参差。评估准入门槛（IC > 0.02）是否够严？
4. **权重学习频率**——regime 权重是实时的，但 IC 反推权重是离线的（回测后）。是否需要在线学习？
