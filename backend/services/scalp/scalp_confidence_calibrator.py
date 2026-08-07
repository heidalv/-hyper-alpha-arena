"""ScalpConfidenceCalibrator — 短线置信度校准器（阶段一 1.2）。

问题背景
========
短线因子路由输出的 `factor_score` 是 `|direction|×100`（外加各类共振/趋势加成），
实测大多落在 20–45 区间。但下游（V5 门禁、EV 计算）却把它当成"置信度/胜率"
直接使用——一个 40 分的信号既不代表 40% 胜率，也不代表 40% 置信度，标尺完全错配。

本模块用历史真实成交（`SignalTradeFeedback` 中 `signal_type='scalp_composite'`
的行，开仓时写入因子分、平仓时由 `update_trade_pnl` 回填盈亏）把因子分**校准成
真正的胜率 `p_win`**：

- 有足够样本：按分数分桶统计经验胜率 → PAVA 保序回归拟合成单调不减曲线 → 插值查询。
- 冷启动/样本不足：回退到"锚定历史基础胜率"的线性映射。

`p_win` 直接喂给 `scalp_ev_gate` 计算期望值，也可用于统一 router 与 V5 的口径。

数据管道
========
开仓：`full_auto_trading_service._run_scalp_independent` 成交后调用
`record_scalp_composite(...)`，写入一条 `scalp_composite` 反馈行（trade_id=持仓id，
signal_value=因子分）。
平仓：`paper_trading_engine` 关仓时已有的 `update_trade_pnl(pos_id, pnl, pnl_pct)`
会按 trade_id 批量回填，本行的 `trade_pnl` 随之被填上——无需额外关仓钩子。

全程 flag 门控（`SCALP_CALIBRATOR_ENABLED`），默认开启，可秒回滚到线性映射。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SIGNAL_TYPE = "scalp_composite"
# ── MR 独立战绩标签（2026-07-11）──
# 震荡均值回归(ranging_mr)是全新打法(2026-07-09上线)，若与趋势打法共用同一个
# signal_type，会被趋势打法的历史胜率"连坐"——见 settings.py 对应注释。这里用
# 单独的 signal_type 把两类打法的真实成交样本彻底分开存、分开拟合，谁的战绩
# 归谁，互不拖累。
_SIGNAL_TYPE_MR = "scalp_composite_mr"
_STRATEGY_SIGNAL_TYPES: Dict[str, str] = {
    "trend": _SIGNAL_TYPE,
    "ranging_mr": _SIGNAL_TYPE_MR,
}

# p_win 合理区间：即便曲线外推也不允许离谱值（过度自信是短线亏损的根源之一）。
# [2026-07-10 校准] 基于真实数据：57笔胜率47%，盈亏比0.36 → 上限从0.80收到0.65
_PWIN_FLOOR = 0.30
_PWIN_CAP = 0.65


@dataclass
class CalibrationResult:
    """一次 p_win 估计的结果 + 溯源信息。"""
    p_win: float = 0.45
    source: str = "cold_linear"   # calibrated / cold_linear / fallback
    n_samples: int = 0
    base_rate: float = 0.45
    note: str = ""


@dataclass
class _CalibrationModel:
    """按 symbol（或全局）拟合出的校准模型。"""
    xs: List[float] = field(default_factory=list)   # 分数桶中心（升序）
    ys: List[float] = field(default_factory=list)   # PAVA 保序后的胜率
    n_samples: int = 0
    base_rate: float = 0.45
    fitted_at: float = 0.0
    is_calibrated: bool = False


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _pava_expand(values: List[float], weights: List[float]) -> List[float]:
    """带索引展开版 PAVA，保证输出长度 == 输入长度。"""
    n = len(values)
    # 块记录：起始idx、结束idx、加权均值、权重
    idx_start = list(range(n))
    idx_end = list(range(n))
    means = list(values)
    ws = [max(1e-9, w) for w in weights]
    blocks = list(range(n))  # 活跃块的代表下标列表

    k = 0
    while k < len(blocks) - 1:
        a = blocks[k]
        b = blocks[k + 1]
        if means[a] <= means[b] + 1e-12:
            k += 1
            continue
        merged_w = ws[a] + ws[b]
        means[a] = (means[a] * ws[a] + means[b] * ws[b]) / merged_w
        ws[a] = merged_w
        idx_end[a] = idx_end[b]
        del blocks[k + 1]
        if k > 0:
            k -= 1

    out = [0.0] * n
    for rep in blocks:
        for j in range(idx_start[rep], idx_end[rep] + 1):
            out[j] = means[rep]
    return out


class ScalpConfidenceCalibrator:
    """把因子分校准成胜率 p_win（单例）。"""

    _instance: Optional["ScalpConfidenceCalibrator"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self) -> None:
        self._lock = threading.Lock()
        # 按策略标签("trend"/"ranging_mr")分别缓存模型，互不覆盖。
        self._models: Dict[str, _CalibrationModel] = {}
        self._model_ts: Dict[str, float] = {}

    # ── 配置读取 ──
    @staticmethod
    def _cfg(name: str, default):
        from backend.config import settings as _s
        return getattr(_s, name, default)

    # ── 开仓时记录因子分快照（供日后校准） ──
    def record_scalp_composite(
        self,
        db,
        account_id: int,
        trade_id: Optional[int],
        symbol: str,
        side: str,
        factor_score: float,
        direction: str,
        strategy_tag: str = "trend",
    ) -> None:
        """写入一条 scalp_composite 反馈行；平仓时由 update_trade_pnl 自动回填盈亏。

        Args:
            strategy_tag: "trend"(默认，原趋势跟随短线) / "ranging_mr"(震荡均值回归)。
                不同标签写入不同 signal_type，拟合出各自独立的胜率曲线。

        失败静默降级（校准数据缺失只影响精度，不影响交易安全）。
        """
        signal_type = _STRATEGY_SIGNAL_TYPES.get(strategy_tag, _SIGNAL_TYPE)
        try:
            from backend.database.models import SignalTradeFeedback
            row = SignalTradeFeedback(
                account_id=account_id,
                trade_id=trade_id,
                symbol=(symbol or "").upper()[:20],
                signal_type=signal_type,
                signal_value=float(factor_score or 0.0),
                signal_direction=(direction or "neutral")[:20],
                trade_side=(side or "")[:10],
            )
            db.add(row)
            db.commit()
        except Exception as e:
            logger.debug(f"[ScalpCalibrator] record_scalp_composite 跳过: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # ── 核心：估计 p_win ──
    def estimate_p_win(
        self,
        symbol: str,
        factor_score: float,
        direction: str = "neutral",
        strategy_tag: str = "trend",
    ) -> CalibrationResult:
        """把因子分映射成校准胜率 p_win。

        Args:
            symbol: 交易对（当前用全局模型，symbol 预留给后续按币种细分）
            factor_score: ScalpFactorRouter 输出的因子总分（通常 0–100）
            direction: long/short（预留，用于后续方向分层校准）
            strategy_tag: "trend"(默认) / "ranging_mr"——决定用哪一份独立战绩拟合的
                模型，样本不足时也决定冷启动基础胜率锚点（见 _cold_linear）。

        Returns:
            CalibrationResult
        """
        score = float(factor_score or 0.0)
        cold_base_default = (
            float(self._cfg("SCALP_MR_COLD_BASE_RATE", 0.50))
            if strategy_tag == "ranging_mr"
            else 0.45
        )
        if not bool(self._cfg("SCALP_CALIBRATOR_ENABLED", True)):
            return self._cold_linear(score, base_rate=cold_base_default, note="calibrator_disabled")

        model = self._get_model(strategy_tag)
        if model is None or not model.is_calibrated:
            # 样本不足：MR 用自己独立的冷启动锚点(默认0.50)，不借用趋势打法的
            # base_rate——即使 model.base_rate 已经算出来了(样本<min但>0)，也只在
            # "trend"标签下沿用它；MR 标签始终以中性锚点为起点，避免被拖累。
            if strategy_tag == "ranging_mr":
                base = cold_base_default
            else:
                base = model.base_rate if model else cold_base_default
            n = model.n_samples if model else 0
            res = self._cold_linear(score, base_rate=base, note="insufficient_samples")
            res.n_samples = n
            res.base_rate = base
            return res

        p = self._interp(model, score)
        p = _clip(p, _PWIN_FLOOR, _PWIN_CAP)
        return CalibrationResult(
            p_win=round(p, 4),
            source="calibrated",
            n_samples=model.n_samples,
            base_rate=round(model.base_rate, 4),
            note=f"isotonic n={model.n_samples} tag={strategy_tag}",
        )

    def _cold_linear(self, score: float, base_rate: float, note: str) -> CalibrationResult:
        """冷启动线性映射：以历史基础胜率为锚，分数每偏离枢轴 1 分微调。

        [2026-07-10 校准] 基于57笔真实 scalp 数据：
        - 真实胜率 47%（base_rate 从拍脑袋的 0.45 校准到 0.47）
        - 盈亏比仅 0.36（赚$0.18 vs 亏$0.50）→ 需要更高分数才值得交易
        - 斜率从 0.003 提到 0.004（高分更自信，低分更保守）
        枢轴取 EXECUTE 阈值附近（分数达到执行门槛时 ≈ 基础胜率）。
        """
        pivot = float(self._cfg("SCALP_FACTOR_EXECUTE_THRESHOLD", 45) or 45)
        slope = 0.004
        base = _clip(base_rate, 0.35, 0.55)
        p = base + slope * (score - pivot)
        p = _clip(p, _PWIN_FLOOR, _PWIN_CAP)
        return CalibrationResult(
            p_win=round(p, 4), source="cold_linear",
            base_rate=round(base, 4), note=note,
        )

    @staticmethod
    def _interp(model: _CalibrationModel, score: float) -> float:
        xs, ys = model.xs, model.ys
        if not xs:
            return model.base_rate
        if score <= xs[0]:
            return ys[0]
        if score >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if score <= xs[i]:
                x0, x1 = xs[i - 1], xs[i]
                y0, y1 = ys[i - 1], ys[i]
                if x1 - x0 < 1e-9:
                    return y1
                t = (score - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return ys[-1]

    # ── 模型加载/拟合（带 TTL 缓存，按策略标签分开） ──
    def _get_model(self, strategy_tag: str = "trend") -> Optional[_CalibrationModel]:
        ttl = float(self._cfg("SCALP_CALIBRATOR_CACHE_TTL_SEC", 600) or 600)
        now = time.time()
        with self._lock:
            cached = self._models.get(strategy_tag)
            cached_ts = self._model_ts.get(strategy_tag, 0.0)
            if cached is not None and (now - cached_ts) < ttl:
                return cached
            model = self._fit_model(strategy_tag)
            self._models[strategy_tag] = model
            self._model_ts[strategy_tag] = now
            return model

    def _fit_model(self, strategy_tag: str = "trend") -> _CalibrationModel:
        """从 SignalTradeFeedback 拉取样本，分桶 + PAVA 拟合（按策略标签独立拟合）。"""
        min_samples = int(self._cfg("SCALP_CALIBRATOR_MIN_SAMPLES", 40) or 40)
        lookback_days = int(self._cfg("SCALP_CALIBRATOR_LOOKBACK_DAYS", 30) or 30)
        signal_type = _STRATEGY_SIGNAL_TYPES.get(strategy_tag, _SIGNAL_TYPE)

        pairs = self._load_samples(lookback_days, signal_type)
        model = _CalibrationModel(fitted_at=time.time())
        if not pairs:
            return model

        wins = sum(1 for _, won in pairs if won)
        model.n_samples = len(pairs)
        model.base_rate = _clip(wins / len(pairs), 0.05, 0.95)

        if len(pairs) < min_samples:
            logger.info(
                f"[ScalpCalibrator] [{strategy_tag}] 样本 {len(pairs)}<{min_samples}，回退线性映射 "
                f"(base_rate={model.base_rate:.3f})"
            )
            return model

        # 分桶（按分数 10 分一档，动态覆盖数据范围）
        buckets: Dict[int, List[int]] = {}
        for score, won in pairs:
            b = int(score // 10) * 10
            buckets.setdefault(b, []).append(1 if won else 0)

        centers: List[float] = []
        rates: List[float] = []
        weights: List[float] = []
        for b in sorted(buckets.keys()):
            outcomes = buckets[b]
            if len(outcomes) < 3:  # 桶太小噪声大，跳过
                continue
            centers.append(b + 5.0)
            rates.append(sum(outcomes) / len(outcomes))
            weights.append(float(len(outcomes)))

        if len(centers) < 2:
            # 有效桶不足 → 仍回退线性（但已算出 base_rate）
            return model

        iso = _pava_expand(rates, weights)
        model.xs = centers
        model.ys = [_clip(v, _PWIN_FLOOR, _PWIN_CAP) for v in iso]
        model.is_calibrated = True
        logger.info(
            f"[ScalpCalibrator] [{strategy_tag}] 校准完成 n={model.n_samples} base={model.base_rate:.3f} "
            f"buckets={list(zip([int(c) for c in centers], [round(v, 3) for v in model.ys]))}"
        )
        return model

    def _load_samples(self, lookback_days: int, signal_type: str = _SIGNAL_TYPE) -> List[Tuple[float, bool]]:
        """拉取 (factor_score, won) 样本对（按 signal_type 区分策略）。

        [2026-07-11] 样本起始线：取"回看窗口"与"脏数据截止线
        (SCALP_CALIBRATOR_SAMPLE_SINCE)"两者中更晚的一个，确保早于该截止线的
        问题订单样本(见 settings.py 注释)永远不参与拟合，直到自然滚出回看窗口。
        """
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SignalTradeFeedback
        except Exception as e:
            logger.debug(f"[ScalpCalibrator] 无法加载模型依赖: {e}")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        try:
            since_str = self._cfg("SCALP_CALIBRATOR_SAMPLE_SINCE", "")
            if since_str:
                sample_since = datetime.fromisoformat(since_str)
                if sample_since.tzinfo is None:
                    sample_since = sample_since.replace(tzinfo=timezone.utc)
                if sample_since > cutoff:
                    cutoff = sample_since
        except Exception as e:
            logger.debug(f"[ScalpCalibrator] SCALP_CALIBRATOR_SAMPLE_SINCE 解析失败，忽略: {e}")
        db = SessionLocal()
        try:
            rows = (
                db.query(SignalTradeFeedback.signal_value, SignalTradeFeedback.trade_pnl)
                .filter(
                    SignalTradeFeedback.signal_type == signal_type,
                    SignalTradeFeedback.created_at >= cutoff,
                    SignalTradeFeedback.trade_pnl.isnot(None),
                )
                .all()
            )
        except Exception as e:
            logger.debug(f"[ScalpCalibrator] 样本查询失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return []
        finally:
            db.close()

        pairs: List[Tuple[float, bool]] = []
        for score_val, pnl in rows:
            try:
                s = float(score_val or 0.0)
                won = float(pnl or 0.0) > 0.0
                pairs.append((s, won))
            except (TypeError, ValueError):
                continue
        return pairs

    def get_stats(self) -> Dict[str, object]:
        """给前端/可观测性用的快照（trend + ranging_mr 两套模型分别展示）。"""

        def _snap(tag: str) -> Dict[str, object]:
            model = self._get_model(tag)
            if model is None:
                return {"calibrated": False, "n_samples": 0}
            return {
                "calibrated": model.is_calibrated,
                "n_samples": model.n_samples,
                "base_rate": round(model.base_rate, 4),
                "curve": [
                    {"score": int(x), "p_win": round(y, 4)}
                    for x, y in zip(model.xs, model.ys)
                ],
                "fitted_at": model.fitted_at,
            }

        trend = _snap("trend")
        # 兼容旧字段：顶层字段仍指向 trend 模型，新增 by_strategy 细分。
        trend["by_strategy"] = {"trend": trend.copy(), "ranging_mr": _snap("ranging_mr")}
        return trend


# 全局单例
scalp_confidence_calibrator = ScalpConfidenceCalibrator()
