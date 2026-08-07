"""ConfidenceCalibrator — 通用置信度校准器（S1-1，泛化自 scalp_confidence_calibrator）。

问题背景
========
中线(swing)/长线(trend_follow) 的开仓分数（SwingAgent 的 `confidence`、TrendAgent 的
`trend_score`）是 LLM 主观打分 + 少量规则修正，**从未用历史真实战绩校准**——一个
"60 分"的信号既不代表 60% 胜率，也不代表 60% 置信度。下游若把它当胜率用（尤其 EV
闸门），标尺完全错配。

本模块把 `scalp_confidence_calibrator` 的 PAVA 保序回归校准逻辑**参数化泛化**成可复用类，
按 `signal_type` 隔离样本、按 `config_prefix` 读取各自阈值，从而同时服务 swing / trend：

- 有足够样本：按分数分桶统计经验胜率 → PAVA 保序回归拟合成单调不减曲线 → 插值查询。
- 冷启动/样本不足：回退到"锚定历史基础胜率"的线性映射（枢轴=各自开仓门槛附近）。

数据管道（与 scalp 完全一致，复用现成回填）
============================================
开仓：中长线独立循环成交后调用 `record_score(...)`，写入一条 `{signal_type}` 反馈行
（trade_id=持仓id，signal_value=分数）。
平仓：`paper_trading_engine.update_trade_pnl(pos_id, pnl, pnl_pct)` 按 trade_id 批量
回填 `trade_pnl`——无需额外关仓钩子。

全程 flag 门控：总开关 `MIDLONG_CALIBRATOR_ENABLED` + 各自 `{PREFIX}_ENABLED`，
默认开启，可秒回滚到线性映射。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# p_win 合理区间：即便曲线外推也不允许离谱值（过度自信是亏损的根源之一）。
_PWIN_FLOOR = 0.30
_PWIN_CAP = 0.80


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
    """按 signal_type（全局）拟合出的校准模型。"""
    xs: List[float] = field(default_factory=list)   # 分数桶中心（升序）
    ys: List[float] = field(default_factory=list)   # PAVA 保序后的胜率
    n_samples: int = 0
    base_rate: float = 0.45
    fitted_at: float = 0.0
    is_calibrated: bool = False


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _pava_expand(values: List[float], weights: List[float]) -> List[float]:
    """带索引展开版 PAVA，保证输出长度 == 输入长度（保序回归，单调不减）。"""
    n = len(values)
    idx_start = list(range(n))
    idx_end = list(range(n))
    means = list(values)
    ws = [max(1e-9, w) for w in weights]
    blocks = list(range(n))

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


class ConfidenceCalibrator:
    """把开仓分数校准成胜率 p_win 的通用校准器（可实例化，非单例）。

    Args:
        signal_type: SignalTradeFeedback 里隔离样本用的类型标签（如 swing_agent_score）
        config_prefix: settings 里各阈值的前缀（如 SWING_CALIBRATOR）
        pivot_default: 冷启动线性映射的枢轴分数（≈开仓门槛，分数达此值时≈基础胜率）
    """

    def __init__(self, signal_type: str, config_prefix: str, pivot_default: float = 52.0):
        self._signal_type = signal_type
        self._prefix = config_prefix
        self._pivot_default = float(pivot_default)
        self._lock = threading.Lock()
        self._model: Optional[_CalibrationModel] = None
        self._model_ts: float = 0.0

    # ── 配置读取 ──
    @staticmethod
    def _get(name: str, default):
        from backend.config import settings as _s
        return getattr(_s, name, default)

    def _cfg(self, suffix: str, default):
        """读 {PREFIX}_{suffix}。"""
        return self._get(f"{self._prefix}_{suffix}", default)

    def _enabled(self) -> bool:
        """总开关 MIDLONG_CALIBRATOR_ENABLED 且 各自 {PREFIX}_ENABLED 都开才启用。"""
        master = bool(self._get("MIDLONG_CALIBRATOR_ENABLED", True))
        own = bool(self._cfg("ENABLED", True))
        return master and own

    # ── 开仓时记录分数快照（供日后校准） ──
    def record_score(
        self,
        db,
        account_id: int,
        trade_id: Optional[int],
        symbol: str,
        side: str,
        score: float,
        direction: str,
    ) -> None:
        """写入一条 {signal_type} 反馈行；平仓时由 update_trade_pnl 自动回填盈亏。

        失败静默降级（校准数据缺失只影响精度，不影响交易安全）。
        """
        try:
            from backend.database.models import SignalTradeFeedback
            row = SignalTradeFeedback(
                account_id=account_id,
                trade_id=trade_id,
                symbol=(symbol or "").upper()[:20],
                signal_type=self._signal_type,
                signal_value=float(score or 0.0),
                signal_direction=(direction or "neutral")[:20],
                trade_side=(side or "")[:10],
            )
            db.add(row)
            db.commit()
        except Exception as e:
            logger.debug(f"[Calibrator:{self._prefix}] record_score 跳过: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # ── 核心：估计 p_win ──
    def estimate_p_win(
        self,
        symbol: str,
        score: float,
        direction: str = "neutral",
    ) -> CalibrationResult:
        """把开仓分数映射成校准胜率 p_win。"""
        s = float(score or 0.0)
        if not self._enabled():
            return self._cold_linear(s, base_rate=0.45, note="calibrator_disabled")

        model = self._get_model()
        if model is None or not model.is_calibrated:
            base = model.base_rate if model else 0.45
            n = model.n_samples if model else 0
            res = self._cold_linear(s, base_rate=base, note="insufficient_samples")
            res.n_samples = n
            res.base_rate = base
            return res

        p = self._interp(model, s)
        p = _clip(p, _PWIN_FLOOR, _PWIN_CAP)
        return CalibrationResult(
            p_win=round(p, 4),
            source="calibrated",
            n_samples=model.n_samples,
            base_rate=round(model.base_rate, 4),
            note=f"isotonic n={model.n_samples}",
        )

    def _cold_linear(self, score: float, base_rate: float, note: str) -> CalibrationResult:
        """冷启动线性映射：以历史基础胜率为锚，分数每偏离枢轴 1 分微调 0.3%。"""
        pivot = float(self._cfg("PIVOT", self._pivot_default) or self._pivot_default)
        slope = 0.003
        base = _clip(base_rate, 0.35, 0.60)
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

    # ── 模型加载/拟合（带 TTL 缓存） ──
    def _get_model(self) -> Optional[_CalibrationModel]:
        ttl = float(self._cfg("CACHE_TTL_SEC", 900) or 900)
        now = time.time()
        with self._lock:
            if self._model is not None and (now - self._model_ts) < ttl:
                return self._model
            model = self._fit_model()
            self._model = model
            self._model_ts = now
            return model

    def _fit_model(self) -> _CalibrationModel:
        """从 SignalTradeFeedback 拉取样本，分桶 + PAVA 拟合。"""
        min_samples = int(self._cfg("MIN_SAMPLES", 30) or 30)
        lookback_days = int(self._cfg("LOOKBACK_DAYS", 45) or 45)

        pairs = self._load_samples(lookback_days)
        model = _CalibrationModel(fitted_at=time.time())
        if not pairs:
            return model

        wins = sum(1 for _, won in pairs if won)
        model.n_samples = len(pairs)
        model.base_rate = _clip(wins / len(pairs), 0.05, 0.95)

        if len(pairs) < min_samples:
            logger.info(
                f"[Calibrator:{self._prefix}] 样本 {len(pairs)}<{min_samples}，回退线性映射 "
                f"(base_rate={model.base_rate:.3f})"
            )
            return model

        buckets: Dict[int, List[int]] = {}
        for score, won in pairs:
            b = int(score // 10) * 10
            buckets.setdefault(b, []).append(1 if won else 0)

        centers: List[float] = []
        rates: List[float] = []
        weights: List[float] = []
        for b in sorted(buckets.keys()):
            outcomes = buckets[b]
            # [2026-07-20 修复] 原阈值 3 太低：3笔胜率只能是 0%/33%/67%/100%，方差极大，
            # 却被直接当成该分数段的"校准胜率"喂进单调回归曲线，一两笔亏损就能把整条
            # 曲线压死（这正是 swing 校准器把 52-90 分全部映射到 30%-38.5% 的直接原因
            # 之一）。提到 8，桶内样本不足则跳过该桶，宁可曲线点少也不用噪声点污染。
            if len(outcomes) < 8:
                continue
            centers.append(b + 5.0)
            rates.append(sum(outcomes) / len(outcomes))
            weights.append(float(len(outcomes)))

        if len(centers) < 2:
            return model

        iso = _pava_expand(rates, weights)
        model.xs = centers
        model.ys = [_clip(v, _PWIN_FLOOR, _PWIN_CAP) for v in iso]
        model.is_calibrated = True
        logger.info(
            f"[Calibrator:{self._prefix}] 校准完成 n={model.n_samples} base={model.base_rate:.3f} "
            f"buckets={list(zip([int(c) for c in centers], [round(v, 3) for v in model.ys]))}"
        )
        return model

    def _load_samples(self, lookback_days: int) -> List[Tuple[float, bool]]:
        """拉取 (score, won) 样本对。"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SignalTradeFeedback
        except Exception as e:
            logger.debug(f"[Calibrator:{self._prefix}] 无法加载模型依赖: {e}")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        db = SessionLocal()
        try:
            rows = (
                db.query(SignalTradeFeedback.signal_value, SignalTradeFeedback.trade_pnl)
                .filter(
                    SignalTradeFeedback.signal_type == self._signal_type,
                    SignalTradeFeedback.created_at >= cutoff,
                    SignalTradeFeedback.trade_pnl.isnot(None),
                )
                .all()
            )
        except Exception as e:
            logger.debug(f"[Calibrator:{self._prefix}] 样本查询失败: {e}")
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
        """给前端/可观测性用的快照。"""
        model = self._get_model()
        if model is None:
            return {"signal_type": self._signal_type, "calibrated": False, "n_samples": 0}
        return {
            "signal_type": self._signal_type,
            "calibrated": model.is_calibrated,
            "n_samples": model.n_samples,
            "base_rate": round(model.base_rate, 4),
            "curve": [
                {"score": int(x), "p_win": round(y, 4)}
                for x, y in zip(model.xs, model.ys)
            ],
            "fitted_at": model.fitted_at,
        }


# ── 中长线两个校准器实例 ──
# 枢轴取各自开仓门槛（见 runtime_tuning 校准：swing≈52 / trend≈56），
# 分数达门槛时 p_win≈基础胜率，向上/向下线性微调。
swing_calibrator = ConfidenceCalibrator(
    signal_type="swing_agent_score", config_prefix="SWING_CALIBRATOR", pivot_default=52.0,
)
trend_calibrator = ConfidenceCalibrator(
    signal_type="trend_agent_score", config_prefix="TREND_CALIBRATOR", pivot_default=56.0,
)


def get_calibrator_for_nature(nature: str) -> ConfidenceCalibrator:
    """按 trade_nature 返回对应校准器（trend_follow/position→trend，其余→swing）。"""
    n = (nature or "").lower()
    if n in ("trend_follow", "position"):
        return trend_calibrator
    return swing_calibrator
