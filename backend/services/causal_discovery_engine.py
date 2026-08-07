"""
因果发现引擎 (P0.1) — 从交易数据中发现因果规律，而非仅统计聚合

核心职责：
1. Granger 因果检验：检测因子-收益率之间的时序因果关系
   （加密适配：使用 1h/4h 频率，非传统日频）
2. 条件独立性检验：排除虚假因果（如"OTC 流量→价格"被"交易所储备"解释）
3. LLM 因果叙事生成：将统计因果规则转化为可理解的自然语言描述
4. 累积交易→按阈值触发（N=30，加密加速）→因果发现→规则入库

与 learning_bus 集成：
  learning_bus 调用 record_trade() 累积计数，
  达到阈值后触发 discover()，
  产生的因果规则写入 StrategyMemory.discovered_rules。

设计原则：
- 因果 > 相关：拒绝虚假相关性伪装成"规律"
- 轻量级：Granger + 条件独立性检验 O(n²)，不需要大型贝叶斯网络
- LLM 辅助：统计结果由 LLM 转化为可审查的叙事，避免黑箱规则
"""

import logging
import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal

logger = logging.getLogger(__name__)

# ── 加密适配：因果发现触发阈值 ──
CAUSAL_DISCOVERY_TRIGGER_N = 30         # 加密市场加速：30笔（非股票惯例50笔）
GRANGER_MAX_LAG = 12                    # 最大滞后阶数（4h数据12期=2天，1h数据12期=半天）
GRANGER_SIGNIFICANCE_LEVEL = 0.01       # p < 0.01 才算因果
COND_INDEP_SIGNIFICANCE_LEVEL = 0.05    # 条件独立性检验阈值
MIN_SAMPLES_FOR_DISCOVERY = 30          # 最少交易数才能启动因果发现

# ── 加密一等因子（用于因果发现的目标变量） ──
CRYPTO_CAUSAL_CANDIDATES = [
    "funding_rate",           # 费率 → 价格方向（反向指标）
    "oi_change_pct",          # OI变化率 → 波动率/方向
    "liquidation_imbalance",  # 爆仓偏差 → 短期反转
    "stablecoin_flow",        # 稳定币流 → 潜在买盘
    "volume_ratio",           # 量比 → 趋势持续性
    "rsi_14",                 # RSI → 均值回归
    "ema_trend_slope",        # EMA趋势斜率 → 方向延续
    "btc_correlation",        # BTC相关性 → 山寨币方向
    "volatility_30d",         # 波动率 → 止损距离
    "regime_score",           # 市况分数 → 策略适用性
]

# ── 因果规则依赖的市场/事件条件（用于后续过期判定） ──
CONDITION_TAGS = [
    "bull_market", "bear_market", "ranging",
    "btc_halving_window", "etf_narrative", "regulatory_event",
    "weekend", "asia_session", "eu_session", "us_session",
    "high_volatility", "low_volatility",
]


@dataclass
class CausalRule:
    """单条因果规则"""
    rule_id: str                              # 唯一标识
    strategy_id: str                          # 所属策略
    symbol: str                               # 标的（可为 '*' 表示通用）
    cause_variable: str                       # 原因变量名
    effect_variable: str                      # 结果变量名
    direction: str                            # "positive" | "negative" | "nonlinear"
    granger_p_value: float                    # Granger检验 p值
    cond_indep_p_value: Optional[float] = None  # 条件独立性检验 p值
    confidence: float = 0.0                   # 综合置信度 (0-1)
    narrative: str = ""                       # LLM 生成的因果叙事
    condition_tags: List[str] = field(default_factory=list)  # 市场条件标签
    sample_count: int = 0                     # 参与检验的样本数
    discovered_at: str = ""                   # 发现时间 ISO
    status: str = "active"                    # active | shadow | deprecated | expired
    hit_count: int = 0                        # 被引用的次数
    consecutive_failures: int = 0             # 连续失败计数（>=5→deprecated）
    created_at: str = ""

    def __post_init__(self):
        if not self.discovered_at:
            self.discovered_at = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = self.discovered_at


@dataclass
class TradeRecord:
    """因果发现用的精简交易记录"""
    strategy_id: str
    symbol: str
    side: str                                # "buy" | "sell"
    pnl_pct: float
    opened_at: datetime
    closed_at: datetime
    duration_minutes: float
    # 加密专属因子快照（入场时刻）
    funding_rate: float = 0.0
    oi_change_pct: float = 0.0
    liquidation_imbalance: float = 0.0
    stablecoin_flow: float = 0.0
    volume_ratio: float = 1.0
    rsi_14: float = 50.0
    ema_trend_slope: float = 0.0
    btc_correlation: float = 0.0
    volatility_30d: float = 0.0
    regime_score: float = 0.5
    # 元数据
    regime_label: str = ""
    time_of_day_hour: int = 0
    is_weekend: bool = False


class CausalDiscoveryEngine:
    """因果发现引擎（单例） — P0.1"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # 累积的交易记录（按策略+标的组合分组）
        self._trade_buffer: Dict[str, List[TradeRecord]] = defaultdict(list)
        self._buffer_lock = threading.Lock()

        # 已发现的因果规则缓存
        self._rules_cache: Dict[str, List[CausalRule]] = defaultdict(list)

        # LLM 调用锁（防止并发调用）
        self._llm_lock = threading.Lock()

        # 上次发现时间（冷却）
        self._last_discovery_at: Dict[str, datetime] = {}
        self._discovery_cooldown_hours = 6  # 每6小时最多触发一次（加密加速）

        logger.info("[CausalDiscovery] 因果发现引擎初始化完成 "
                     f"(trigger={CAUSAL_DISCOVERY_TRIGGER_N}, "
                     f"granger_lag={GRANGER_MAX_LAG}, "
                     f"significance={GRANGER_SIGNIFICANCE_LEVEL})")

    # ══════════════════════════════════════════════════
    #  交易记录接口
    # ══════════════════════════════════════════════════

    def record_trade(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        pnl_pct: float,
        opened_at: datetime,
        closed_at: datetime,
        *,
        funding_rate: float = 0.0,
        oi_change_pct: float = 0.0,
        liquidation_imbalance: float = 0.0,
        stablecoin_flow: float = 0.0,
        volume_ratio: float = 1.0,
        rsi_14: float = 50.0,
        ema_trend_slope: float = 0.0,
        btc_correlation: float = 0.0,
        volatility_30d: float = 0.0,
        regime_score: float = 0.5,
        regime_label: str = "",
        fingerprint: Optional[Dict] = None,
    ) -> None:
        """记录一笔交易结果，累积到缓冲区。

        由 learning_bus / unified_learning_service 在每笔平仓后调用。
        当某组合累积达到阈值时自动触发因果发现。

        加密适配：从 fingerprint 中提取加密专属因子快照。
        """
        duration = (closed_at - opened_at).total_seconds() / 60.0 if opened_at and closed_at else 0
        hour = opened_at.hour if opened_at else 0
        is_weekend = opened_at.weekday() >= 5 if opened_at else False

        record = TradeRecord(
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            pnl_pct=pnl_pct,
            opened_at=opened_at,
            closed_at=closed_at,
            duration_minutes=duration,
            funding_rate=funding_rate,
            oi_change_pct=oi_change_pct,
            liquidation_imbalance=liquidation_imbalance,
            stablecoin_flow=stablecoin_flow,
            volume_ratio=volume_ratio,
            rsi_14=rsi_14,
            ema_trend_slope=ema_trend_slope,
            btc_correlation=btc_correlation,
            volatility_30d=volatility_30d,
            regime_score=regime_score,
            regime_label=regime_label,
            time_of_day_hour=hour,
            is_weekend=is_weekend,
        )

        # 尝试从 fingerprint 中补充因子数据
        if fingerprint:
            record.funding_rate = fingerprint.get("funding_rate", record.funding_rate)
            record.oi_change_pct = fingerprint.get("oi_change_pct", record.oi_change_pct)
            record.liquidation_imbalance = fingerprint.get("liquidation_imbalance", record.liquidation_imbalance)
            record.stablecoin_flow = fingerprint.get("stablecoin_flow", record.stablecoin_flow)

        cache_key = f"{strategy_id}:{symbol}"
        with self._buffer_lock:
            self._trade_buffer[cache_key].append(record)

        count = len(self._trade_buffer.get(cache_key, []))
        logger.debug(f"[CausalDiscovery] record_trade {cache_key}: count={count}/{CAUSAL_DISCOVERY_TRIGGER_N}")

    def should_trigger(self, strategy_id: str, symbol: str) -> bool:
        """判断是否应触发因果发现。"""
        cache_key = f"{strategy_id}:{symbol}"
        with self._buffer_lock:
            count = len(self._trade_buffer.get(cache_key, []))

        if count < CAUSAL_DISCOVERY_TRIGGER_N:
            return False

        # 冷却检查
        last = self._last_discovery_at.get(cache_key)
        if last:
            hours_since = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if hours_since < self._discovery_cooldown_hours:
                return False

        return True

    # ══════════════════════════════════════════════════
    #  因果发现核心
    # ══════════════════════════════════════════════════

    def discover(
        self,
        db: Session,
        strategy_id: str,
        symbol: str,
    ) -> List[CausalRule]:
        """执行因果发现流程。

        1. Granger 因果检验：检测因子→收益率
        2. 条件独立性检验：排除虚假因果
        3. LLM 叙事生成
        4. 规则入库

        Returns:
            新发现的因果规则列表
        """
        cache_key = f"{strategy_id}:{symbol}"
        with self._buffer_lock:
            records = list(self._trade_buffer.get(cache_key, []))

        if len(records) < MIN_SAMPLES_FOR_DISCOVERY:
            logger.info(f"[CausalDiscovery] 样本不足 {cache_key}: {len(records)}/{MIN_SAMPLES_FOR_DISCOVERY}")
            return []

        self._last_discovery_at[cache_key] = datetime.now(timezone.utc)
        logger.info(f"[CausalDiscovery] 开始因果发现 {cache_key}: {len(records)} 笔交易")

        # ── Step 1: Granger 因果检验 ──
        granger_results = self._run_granger_tests(records)

        # ── Step 2: 条件独立性检验 ──
        verified_results = self._verify_conditional_independence(granger_results, records)

        # ── Step 3: LLM 因果叙事生成 ──
        rules = self._generate_narratives(verified_results, records, strategy_id, symbol)

        # ── Step 4: 入库 ──
        if rules:
            self._persist_rules(db, strategy_id, symbol, rules)
            with self._buffer_lock:
                self._rules_cache[cache_key].extend(rules)

        # ── 清空已处理的缓冲区（保留最近10笔作为种子） ──
        with self._buffer_lock:
            self._trade_buffer[cache_key] = records[-10:]

        logger.info(f"[CausalDiscovery] {cache_key} 完成: {len(rules)} 条因果规则")
        return rules

    # ══════════════════════════════════════════════════
    #  Granger 因果检验
    # ══════════════════════════════════════════════════

    def _run_granger_tests(self, records: List[TradeRecord]) -> List[Dict]:
        """对每个候选因子做 Granger 因果检验。

        加密适配：
        - 使用交易序列（按时间排序）而非固定时间窗口
        - 滞后阶数限制为 GRANGER_MAX_LAG（避免过拟合）
        - p < GRANGER_SIGNIFICANCE_LEVEL（0.01）才算显著

        Returns:
            [{cause, effect, direction, p_value, best_lag, f_statistic}, ...]
        """
        if len(records) < GRANGER_MAX_LAG + 5:
            return []

        # 按 opened_at 排序
        sorted_records = sorted(records, key=lambda r: r.opened_at)

        # 构建因子时间序列和收益率序列
        n = len(sorted_records)
        pnl_series = np.array([r.pnl_pct for r in sorted_records])

        factor_map = {
            "funding_rate": np.array([r.funding_rate for r in sorted_records]),
            "oi_change_pct": np.array([r.oi_change_pct for r in sorted_records]),
            "liquidation_imbalance": np.array([r.liquidation_imbalance for r in sorted_records]),
            "stablecoin_flow": np.array([r.stablecoin_flow for r in sorted_records]),
            "volume_ratio": np.array([r.volume_ratio for r in sorted_records]),
            "rsi_14": np.array([r.rsi_14 for r in sorted_records]),
            "ema_trend_slope": np.array([r.ema_trend_slope for r in sorted_records]),
            "btc_correlation": np.array([r.btc_correlation for r in sorted_records]),
            "volatility_30d": np.array([r.volatility_30d for r in sorted_records]),
            "regime_score": np.array([r.regime_score for r in sorted_records]),
        }

        results = []
        for factor_name, factor_series in factor_map.items():
            # 跳过方差为零的因子
            if np.std(factor_series) < 1e-10:
                continue

            result = self._granger_causality_test(
                cause_series=factor_series,
                effect_series=pnl_series,
                max_lag=GRANGER_MAX_LAG,
            )
            if result:
                results.append({
                    "cause": factor_name,
                    "effect": "pnl_pct",
                    "direction": result["direction"],
                    "p_value": result["p_value"],
                    "best_lag": result["best_lag"],
                    "f_statistic": result["f_statistic"],
                })

        # 按 p_value 排序（最显著的在前）
        results.sort(key=lambda x: x["p_value"])
        return results

    def _granger_causality_test(
        self,
        cause_series: np.ndarray,
        effect_series: np.ndarray,
        max_lag: int,
    ) -> Optional[Dict]:
        """单变量 Granger 因果检验。

        H0: cause 不 Granger-cause effect。
        对每个 lag 做 F 检验，取最小 p_value。
        """
        n = len(cause_series)
        if n < max_lag + 5:
            return None

        best_p = 1.0
        best_lag = 0
        best_f = 0.0
        best_direction = "none"

        for lag in range(1, min(max_lag + 1, n // 3)):
            try:
                # 构建回归：effect[t] = α + β₁*effect[t-1] + ... + βₚ*effect[t-p]  (restricted)
                #            effect[t] = α + β₁*effect[t-1] + ... + γ₁*cause[t-1] + ...  (unrestricted)
                n_obs = n - lag
                if n_obs < lag + 5:
                    continue

                # restricted: 仅滞后 effect
                X_r = np.zeros((n_obs, lag + 1))
                X_r[:, 0] = 1.0  # intercept
                for j in range(lag):
                    X_r[:, j + 1] = effect_series[lag - j - 1 : n - j - 1]

                # unrestricted: 滞后 effect + 滞后 cause
                X_u = np.zeros((n_obs, 2 * lag + 1))
                X_u[:, 0] = 1.0
                for j in range(lag):
                    X_u[:, j + 1] = effect_series[lag - j - 1 : n - j - 1]
                    X_u[:, lag + j + 1] = cause_series[lag - j - 1 : n - j - 1]

                y = effect_series[lag:]

                # OLS 拟合
                ssr_r = self._ols_ssr(X_r, y)
                ssr_u = self._ols_ssr(X_u, y)

                if ssr_u < 1e-15:
                    continue

                # F 统计量
                f_stat = ((ssr_r - ssr_u) / lag) / (ssr_u / (n_obs - 2 * lag - 1))
                if f_stat <= 0 or math.isnan(f_stat) or math.isinf(f_stat):
                    continue

                # 近似 p_value（使用 F 分布近似）
                from scipy.stats import f as f_dist
                p_value = 1.0 - f_dist.cdf(f_stat, lag, n_obs - 2 * lag - 1)

                if p_value < best_p:
                    best_p = p_value
                    best_lag = lag
                    best_f = f_stat

                    # 方向判定：取 unrestricted 模型中 cause 系数的符号加权平均
                    try:
                        _, _, _, _, residuals = np.linalg.lstsq(X_u, y, rcond=None)
                        coefs = np.linalg.lstsq(X_u, y, rcond=None)[0]
                        # cause 系数之和的符号
                        cause_coefs = coefs[lag + 1 : 2 * lag + 1]
                        cause_sum = np.sum(cause_coefs)
                        if cause_sum > 0.01:
                            best_direction = "positive"
                        elif cause_sum < -0.01:
                            best_direction = "negative"
                        else:
                            best_direction = "nonlinear"
                    except Exception:
                        best_direction = "nonlinear"
            except Exception:
                continue

        if best_p < GRANGER_SIGNIFICANCE_LEVEL:
            return {
                "p_value": best_p,
                "best_lag": best_lag,
                "f_statistic": best_f,
                "direction": best_direction,
            }
        return None

    @staticmethod
    def _ols_ssr(X: np.ndarray, y: np.ndarray) -> float:
        """计算 OLS 残差平方和。"""
        try:
            coefs, residuals, _, _ = np.linalg.lstsq(X, y, rcond=None)
            if len(residuals) > 0:
                return float(residuals[0]) if residuals[0] > 0 else 0.0
            pred = X @ coefs
            return float(np.sum((y - pred) ** 2))
        except Exception:
            return 1e10

    # ══════════════════════════════════════════════════
    #  条件独立性检验
    # ══════════════════════════════════════════════════

    def _verify_conditional_independence(
        self, granger_results: List[Dict], records: List[TradeRecord]
    ) -> List[Dict]:
        """条件独立性检验：排除虚假因果。

        对每个 Granger 显著的结果，检查是否被第三方变量解释。
        例：funding_rate → pnl 可能被 oi_change_pct 完全解释。

        方法：偏相关检验。
        """
        if not granger_results:
            return []

        sorted_records = sorted(records, key=lambda r: r.opened_at)
        n = len(sorted_records)

        pnl_series = np.array([r.pnl_pct for r in sorted_records])

        factor_map = {
            "funding_rate": np.array([r.funding_rate for r in sorted_records]),
            "oi_change_pct": np.array([r.oi_change_pct for r in sorted_records]),
            "liquidation_imbalance": np.array([r.liquidation_imbalance for r in sorted_records]),
            "stablecoin_flow": np.array([r.stablecoin_flow for r in sorted_records]),
            "volume_ratio": np.array([r.volume_ratio for r in sorted_records]),
            "rsi_14": np.array([r.rsi_14 for r in sorted_records]),
            "ema_trend_slope": np.array([r.ema_trend_slope for r in sorted_records]),
            "btc_correlation": np.array([r.btc_correlation for r in sorted_records]),
            "volatility_30d": np.array([r.volatility_30d for r in sorted_records]),
            "regime_score": np.array([r.regime_score for r in sorted_records]),
        }

        verified = []
        for gr in granger_results:
            cause_name = gr["cause"]
            cause_series = factor_map.get(cause_name)
            if cause_series is None:
                continue

            # 计算偏相关：控制其他因子
            controlled_p = self._partial_correlation_test(
                cause_series=cause_series,
                effect_series=pnl_series,
                control_candidates={
                    name: series
                    for name, series in factor_map.items()
                    if name != cause_name and np.std(series) > 1e-10
                },
            )

            if controlled_p is not None and controlled_p > COND_INDEP_SIGNIFICANCE_LEVEL:
                # 被其他因子解释 → 可能是虚假因果，降低置信度但不直接丢弃
                gr["cond_indep_p_value"] = controlled_p
                gr["spurious_warning"] = True
                logger.debug(f"[CausalDiscovery] {cause_name} 条件独立性检验不通过 p={controlled_p:.4f}")
            else:
                gr["cond_indep_p_value"] = controlled_p
                gr["spurious_warning"] = False

            verified.append(gr)

        return verified

    def _partial_correlation_test(
        self,
        cause_series: np.ndarray,
        effect_series: np.ndarray,
        control_candidates: Dict[str, np.ndarray],
    ) -> Optional[float]:
        """偏相关检验：控制第三方变量后 cause 与 effect 是否仍显著相关。

        Returns:
            最大的条件独立性 p_value（越大说明越可能被解释）
        """
        if not control_candidates:
            return None

        n = len(cause_series)

        # 对每个候选控制变量做偏相关系数计算
        max_p = 0.0
        for control_name, control_series in control_candidates.items():
            if len(control_series) != n:
                continue
            try:
                # 计算偏相关系数: r_{xy·z} = (r_{xy} - r_{xz}*r_{yz}) / sqrt((1-r_{xz}²)(1-r_{yz}²))
                r_xy = np.corrcoef(cause_series, effect_series)[0, 1]
                r_xz = np.corrcoef(cause_series, control_series)[0, 1]
                r_yz = np.corrcoef(effect_series, control_series)[0, 1]

                denom = math.sqrt((1 - r_xz ** 2) * (1 - r_yz ** 2))
                if abs(denom) < 1e-10:
                    continue

                r_xy_z = (r_xy - r_xz * r_yz) / denom
                # 转为 t 统计量
                t_stat = r_xy_z * math.sqrt((n - 3) / max(1 - r_xy_z ** 2, 1e-10))
                # 近似 p_value
                from scipy.stats import t as t_dist
                p_value = 2.0 * (1.0 - t_dist.cdf(abs(t_stat), n - 3))

                if p_value > max_p:
                    max_p = p_value
            except Exception:
                continue

        return max_p if max_p > 0 else None

    # ══════════════════════════════════════════════════
    #  LLM 因果叙事生成
    # ══════════════════════════════════════════════════

    def _generate_narratives(
        self,
        verified_results: List[Dict],
        records: List[TradeRecord],
        strategy_id: str,
        symbol: str,
    ) -> List[CausalRule]:
        """用 LLM 将统计因果转化为可理解的叙事。

        策略：
        - 先用规则引擎生成公式化描述
        - 再用 LLM 补充市场直觉和边界条件
        - LLM 失败时降级为纯统计描述
        """
        rules = []

        for vr in verified_results[:5]:  # 最多5条（避免信息过载）
            direction = vr.get("direction", "none")
            p_value = vr.get("p_value", 1.0)
            best_lag = vr.get("best_lag", 0)
            spurious = vr.get("spurious_warning", False)
            cond_p = vr.get("cond_indep_p_value")

            # 综合置信度
            confidence = 1.0 - p_value
            if spurious:
                confidence *= 0.5  # 条件独立性不通过 → 置信度减半

            # 规则引擎基础描述
            base_desc = self._build_base_description(vr, records)

            # LLM 增强叙事
            narrative = self._call_llm_narrative(
                cause=vr["cause"],
                effect=vr["effect"],
                direction=direction,
                p_value=p_value,
                best_lag=best_lag,
                confidence=confidence,
                sample_count=len(records),
                symbol=symbol,
                strategy_id=strategy_id,
                base_description=base_desc,
            )

            # 推断条件标签（事件绑定，支持后续过期判定）
            condition_tags = self._infer_condition_tags(records, vr["cause"])

            rule_id = f"causal_{strategy_id}_{symbol}_{vr['cause']}_{int(datetime.now(timezone.utc).timestamp())}"

            rule = CausalRule(
                rule_id=rule_id,
                strategy_id=strategy_id,
                symbol=symbol,
                cause_variable=vr["cause"],
                effect_variable=vr["effect"],
                direction=direction,
                granger_p_value=p_value,
                cond_indep_p_value=cond_p,
                confidence=round(confidence, 4),
                narrative=narrative,
                condition_tags=condition_tags,
                sample_count=len(records),
                status="shadow" if spurious else "active",
            )
            rules.append(rule)

        return rules

    def _build_base_description(self, vr: Dict, records: List[TradeRecord]) -> str:
        """构建规则引擎基础描述（LLM 失败时的降级方案）。"""
        cause = vr["cause"]
        direction = vr.get("direction", "none")
        p_value = vr.get("p_value", 1.0)
        best_lag = vr.get("best_lag", 0)

        direction_text = {
            "positive": "正向推动",
            "negative": "反向压制",
            "nonlinear": "非线性影响",
            "none": "无显著影响",
        }.get(direction, direction)

        # 计算均值辅助描述
        sorted_records = sorted(records, key=lambda r: r.opened_at)
        pnl_mean = np.mean([r.pnl_pct for r in sorted_records])

        desc = (
            f"Granger因果检验发现：{cause} 对 pnl_pct 具有 {direction_text}作用 "
            f"(p={p_value:.4f}, 滞后 {best_lag} 期)。"
            f"样本量 {len(records)}，平均收益 {pnl_mean:.2%}。"
        )
        return desc

    def _call_llm_narrative(
        self,
        cause: str,
        effect: str,
        direction: str,
        p_value: float,
        best_lag: int,
        confidence: float,
        sample_count: int,
        symbol: str,
        strategy_id: str,
        base_description: str,
    ) -> str:
        """调用 LLM 生成因果叙事。

        如有异常则降级为基础描述。
        """
        if confidence < 0.5:
            return base_description

        try:
            from backend.services.opencode_bridge import opencode_bridge

            prompt = f"""你是一位加密货币量化交易系统的高级分析师。

一条新的因果规律需要你用自然语言解释并验证其合理性：

## 统计发现
- 原因变量: {cause}
- 结果变量: {effect}
- 关系方向: {direction}
- Granger 因果检验 p值: {p_value:.6f}
- 最佳滞后阶数: {best_lag}
- 综合置信度: {confidence:.1%}
- 样本交易数: {sample_count}
- 交易标的: {symbol}

## 基础描述
{base_description}

## 要求
1. 用 1-2 句话解释这条因果规律的市场直觉（为什么这个因果关系可能存在/不存在）
2. 指出这条规律在什么市场条件下最可能生效/失效（加密市场特有：资金费率极端、周末低流动性、减半周期等）
3. 如果这条规律看起来像是虚假因果，请说明理由

只输出最终的因果叙事文本（纯文本，不超过200字）。不要输出思考过程。"""

            with self._llm_lock:
                result = opencode_bridge.quick_chat(
                    prompt=prompt,
                    max_tokens=300,
                    temperature=0.3,
                    timeout_seconds=30,
                )

            if result and len(result.strip()) > 20:
                return result.strip()

        except ImportError:
            logger.debug("[CausalDiscovery] opencode_bridge 不可用，使用基础描述")
        except Exception as e:
            logger.warning(f"[CausalDiscovery] LLM 叙事生成失败: {e}")

        return base_description

    def _infer_condition_tags(self, records: List[TradeRecord], cause: str) -> List[str]:
        """根据交易记录的上下文推断条件标签（用于事件绑定过期）。"""
        tags = []

        # 市场状态
        regimes = [r.regime_label for r in records if r.regime_label]
        if regimes:
            from collections import Counter
            top_regime = Counter(regimes).most_common(1)[0][0]
            if "bull" in top_regime.lower():
                tags.append("bull_market")
            elif "bear" in top_regime.lower():
                tags.append("bear_market")
            else:
                tags.append("ranging")

        # 时间特征
        weekend_ratio = sum(1 for r in records if r.is_weekend) / max(len(records), 1)
        if weekend_ratio > 0.3:
            tags.append("weekend")

        hours = [r.time_of_day_hour for r in records]
        if hours:
            avg_hour = sum(hours) / len(hours)
            if 0 <= avg_hour < 8:
                tags.append("asia_session")
            elif 8 <= avg_hour < 16:
                tags.append("eu_session")
            else:
                tags.append("us_session")

        # 波动率特征
        vol_values = [r.volatility_30d for r in records if r.volatility_30d > 0]
        if vol_values:
            avg_vol = sum(vol_values) / len(vol_values)
            if avg_vol > 0.8:
                tags.append("high_volatility")
            elif avg_vol < 0.3:
                tags.append("low_volatility")

        return tags

    # ══════════════════════════════════════════════════
    #  持久化
    # ══════════════════════════════════════════════════

    def _persist_rules(
        self,
        db: Session,
        strategy_id: str,
        symbol: str,
        rules: List[CausalRule],
    ) -> None:
        """将因果规则持久化到 StrategyMemory.discovered_rules。

        策略：
        - 新规则追加到 discovered_rules
        - 同名 cause 的旧规则标记 superseded
        - 限制每个策略最多保留 30 条规则
        """
        try:
            from backend.database.models import StrategyMemory

            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()

            if not memory:
                logger.debug(f"[CausalDiscovery] StrategyMemory 不存在 {strategy_id}")
                return

            # 现有规则
            existing_rules = list(memory.discovered_rules or [])

            # 标记同名旧规则为 superseded
            new_causes = {r.cause_variable for r in rules}
            for existing in existing_rules:
                if isinstance(existing, dict) and existing.get("cause_variable") in new_causes:
                    existing["status"] = "superseded"

            # 追加新规则
            for rule in rules:
                rule_dict = asdict(rule)
                rule_dict.pop("created_at", None)  # 由 DB 管理
                existing_rules.append(rule_dict)

            # 限制总数（保留最新的 30 条）
            if len(existing_rules) > 30:
                # 按 discovered_at 排序，保留最新
                existing_rules.sort(
                    key=lambda x: x.get("discovered_at", ""),
                    reverse=True,
                )
                existing_rules = existing_rules[:30]

            memory.discovered_rules = existing_rules
            memory.updated_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(
                f"[CausalDiscovery] 规则入库完成: {strategy_id} "
                f"新增 {len(rules)} 条, 总计 {len(existing_rules)} 条"
            )
        except Exception as e:
            logger.error(f"[CausalDiscovery] 规则入库失败: {e}", exc_info=True)
            db.rollback()

    # ══════════════════════════════════════════════════
    #  查询接口
    # ══════════════════════════════════════════════════

    def get_rules_for_strategy(
        self, strategy_id: str, status_filter: str = "active"
    ) -> List[CausalRule]:
        """获取某策略的因果规则（从缓存）。"""
        rules = []
        for cache_key, cached_rules in self._rules_cache.items():
            if cache_key.startswith(f"{strategy_id}:"):
                for r in cached_rules:
                    if r.status == status_filter:
                        rules.append(r)
        return rules

    def format_for_llm_prompt(self, strategy_id: str, symbol: str) -> str:
        """格式化因果规则为 LLM prompt 注入文本。

        仅输出 active/shadow 且 confidence >= 0.7 的规则。
        """
        cache_key = f"{strategy_id}:{symbol}"
        rules = self._rules_cache.get(cache_key, [])

        active_rules = [
            r for r in rules
            if r.status in ("active", "shadow") and r.confidence >= 0.7
        ]

        if not active_rules:
            return ""

        lines = ["\n### 已发现的因果规律（由因果发现引擎自动检测）\n"]
        for i, rule in enumerate(active_rules[:5], 1):
            direction_icon = {"positive": "↑", "negative": "↓", "nonlinear": "~"}.get(
                rule.direction, "?"
            )
            lines.append(
                f"{i}. {rule.cause_variable} {direction_icon} → {rule.effect_variable} "
                f"(置信度 {rule.confidence:.0%}, p={rule.granger_p_value:.4f})"
            )
            if rule.narrative and rule.narrative != rule._build_base_description({}, []):
                lines.append(f"   {rule.narrative}")
            if rule.condition_tags:
                lines.append(f"   适用条件: {', '.join(rule.condition_tags)}")

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """获取引擎统计信息。"""
        stats = {
            "total_buffered_trades": 0,
            "total_rules_cached": 0,
            "buffer_by_pair": {},
        }
        with self._buffer_lock:
            for cache_key, records in self._trade_buffer.items():
                stats["buffer_by_pair"][cache_key] = len(records)
                stats["total_buffered_trades"] += len(records)
        for rules in self._rules_cache.values():
            stats["total_rules_cached"] += len(rules)
        return stats


# ══════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════

_causal_discovery_instance: Optional[CausalDiscoveryEngine] = None


def get_causal_discovery_engine() -> CausalDiscoveryEngine:
    """获取因果发现引擎单例。"""
    global _causal_discovery_instance
    if _causal_discovery_instance is None:
        _causal_discovery_instance = CausalDiscoveryEngine()
    return _causal_discovery_instance
