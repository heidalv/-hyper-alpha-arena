"""AiDecisionConfidenceCalibrator — S2-8 LLM 置信度校准器（ai_decision_logs → conf→胜率曲线）。

问题背景
========
全链路 LLM 决策（`ai_decision_logs`）里每个 buy/sell 都带 `confidence`（LLM 声称的
确信程度 0-1），平仓后经 arena_routes 的 sync 端点回填 `realized_pnl`。但 LLM 的
confidence 从未被历史战绩校验过：一个"0.8 置信"的信号是否真有 80% 胜率？过度自信
（高 conf 低胜率）是 AI 主驾模式亏损的根源之一，D1 决策溯源缺少量化证据。

与 S1-1 `ConfidenceCalibrator` 的区别
======================================
- 样本源：S1-1 用 `SignalTradeFeedback`（中长线开仓分数，按 signal_type 隔离）；
  本校准器用 `ai_decision_logs`（全链路 LLM 决策，decision_snapshot 的 confidence）。
- 分数语义：S1-1 是 0-100 开仓分数；本校准器是 0-1 概率型置信度。
- 消费方：S1-1 供 midlong EV 闸门算 p_win；本校准器供决策质量审计 / 前端看板
  （S2-11 决策链路视图）/ 未来 conf 改写闸门。

方法
====
- 有足够样本：confidence 按 0.1 分桶 → 桶内经验胜率 → PAVA 保序回归拟合成
  单调不减曲线 → 线性插值查询 `estimate_p_win(conf)`。
- 冷启动/样本不足：回退到锚定历史基础胜率的线性映射（枢轴=0.5，斜率 0.3/0.1 档）。

confidence 提取兼容两种历史格式：
- `decision_snapshot` JSON 的 `confidence` 字段（0-1；早期规则引擎写回时为 0-100，
  >1 自动 /100）；
- 缺失时回退到三周期列 `mid_confidence`（0-1）。

样本过滤（保证 outcome 语义干净）：
- 仅 `operation IN (buy, sell)`（hold/close 无方向性胜负）；`close` 是平仓不是开仓信号；
- 仅 `executed == "true"`（未执行的决策无实盘意义）；
- `realized_pnl IS NOT NULL` 且 `!= 0`（0 是开仓单未结算）；
- confidence 有效（0.05~0.95，0 是降级/禁开标记，剔除）。

全程 flag 门控：总开关 `AI_DECISION_CALIBRATOR_ENABLED`（默认开），秒回滚。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# p_win 合理区间：同 S1-1，曲线外推也不允许离谱值。
_PWIN_FLOOR = 0.30
_PWIN_CAP = 0.80

# confidence 枢轴（LLM 中性置信度）与冷启动斜率（0-1 尺度，每 0.1 档调 3%）。
_PIVOT_CONF = 0.5
_COLD_SLOPE_PER_01 = 0.03


@dataclass
class CalibrationResult:
    """一次 p_win 估计的结果 + 溯源信息（与 confidence_calibrator 同构）。"""
    p_win: float = 0.45
    source: str = "cold_linear"   # calibrated / cold_linear / fallback
    n_samples: int = 0
    base_rate: float = 0.45
    note: str = ""


@dataclass
class _CalibrationModel:
    """按全局（LLM 主驾决策）拟合出的 conf→胜率模型。"""
    xs: List[float] = field(default_factory=list)   # 置信度桶中心（升序，0-1）
    ys: List[float] = field(default_factory=list)   # PAVA 保序后的胜率
    n_samples: int = 0
    base_rate: float = 0.45
    fitted_at: float = 0.0
    is_calibrated: bool = False


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _extract_confidence(decision_snapshot: Optional[str], mid_conf) -> Optional[float]:
    """从 decision_snapshot JSON 提取 LLM confidence，兼容 0-1 与 0-100 两种格式。

    优先级：decision_snapshot.confidence > mid_confidence 列。解析失败返回 None。
    """
    if decision_snapshot:
        try:
            raw = json.loads(decision_snapshot)
        except (TypeError, ValueError):
            raw = None
        if isinstance(raw, dict):
            conf = raw.get("confidence")
            if conf is not None:
                try:
                    v = float(conf)
                except (TypeError, ValueError):
                    v = None
                if v is not None and v > 0:
                    # 早期规则引擎写回 confidence*100（0-100 百分数）→ 归一化
                    return min(1.0, v / 100.0) if v > 1.0 else v
    if mid_conf is not None:
        try:
            return float(mid_conf)
        except (TypeError, ValueError):
            return None
    return None


class AiDecisionConfidenceCalibrator:
    """把 LLM 决策置信度（0-1）校准成实际胜率 p_win（可实例化，模块级单例）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._model: Optional[_CalibrationModel] = None
        self._model_ts: float = 0.0

    # ── 配置读取 ──
    @staticmethod
    def _get(name: str, default):
        from backend.config import settings as _s
        return getattr(_s, name, default)

    def _cfg(self, suffix: str, default):
        return self._get(f"AI_DECISION_CALIBRATOR_{suffix}", default)

    def _enabled(self) -> bool:
        return bool(self._cfg("ENABLED", True))

    # ── 核心：估计 p_win ──
    def estimate_p_win(self, confidence: float) -> CalibrationResult:
        """把 LLM 置信度映射成校准胜率 p_win。"""
        c = _clip(float(confidence or 0.0), 0.0, 1.0)
        if not self._enabled():
            return self._cold_linear(c, base_rate=0.45, note="calibrator_disabled")

        model = self._get_model()
        if model is None or not model.is_calibrated:
            base = model.base_rate if model else 0.45
            n = model.n_samples if model else 0
            res = self._cold_linear(c, base_rate=base, note="insufficient_samples")
            res.n_samples = n
            res.base_rate = base
            return res

        p = self._interp(model, c)
        p = _clip(p, _PWIN_FLOOR, _PWIN_CAP)
        return CalibrationResult(
            p_win=round(p, 4),
            source="calibrated",
            n_samples=model.n_samples,
            base_rate=round(model.base_rate, 4),
            note=f"isotonic n={model.n_samples}",
        )

    def _cold_linear(self, confidence: float, base_rate: float, note: str) -> CalibrationResult:
        """冷启动线性映射：以历史基础胜率为锚，置信度每偏离 0.5 一档(0.1)微调 3%。"""
        base = _clip(base_rate, 0.35, 0.60)
        p = base + _COLD_SLOPE_PER_01 * ((confidence - _PIVOT_CONF) / 0.1)
        p = _clip(p, _PWIN_FLOOR, _PWIN_CAP)
        return CalibrationResult(
            p_win=round(p, 4), source="cold_linear",
            base_rate=round(base, 4), note=note,
        )

    @staticmethod
    def _interp(model: _CalibrationModel, confidence: float) -> float:
        xs, ys = model.xs, model.ys
        if not xs:
            return model.base_rate
        if confidence <= xs[0]:
            return ys[0]
        if confidence >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if confidence <= xs[i]:
                x0, x1 = xs[i - 1], xs[i]
                y0, y1 = ys[i - 1], ys[i]
                if x1 - x0 < 1e-9:
                    return y1
                t = (confidence - x0) / (x1 - x0)
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
        """从 ai_decision_logs 拉取 (confidence, won) 样本，分桶 + PAVA 拟合。"""
        from backend.services.calibration.confidence_calibrator import _pava_expand

        min_samples = int(self._cfg("MIN_SAMPLES", 30) or 30)
        lookback_days = int(self._cfg("LOOKBACK_DAYS", 45) or 45)
        min_bucket = int(self._cfg("MIN_BUCKET", 5) or 5)

        pairs = self._load_samples(lookback_days)
        model = _CalibrationModel(fitted_at=time.time())
        if not pairs:
            return model

        wins = sum(1 for _, won in pairs if won)
        model.n_samples = len(pairs)
        model.base_rate = _clip(wins / len(pairs), 0.05, 0.95)

        if len(pairs) < min_samples:
            logger.info(
                f"[AiDecisionCalibrator] 样本 {len(pairs)}<{min_samples}，回退线性映射 "
                f"(base_rate={model.base_rate:.3f})"
            )
            return model

        # confidence 0-1 → 0.1 一档分桶（桶 0~9），桶中心 = (b + 0.5) / 10
        buckets: Dict[int, List[int]] = {}
        for conf, won in pairs:
            b = int(_clip(conf, 0.0, 0.999) * 10)
            buckets.setdefault(b, []).append(1 if won else 0)

        centers: List[float] = []
        rates: List[float] = []
        weights: List[float] = []
        for b in sorted(buckets.keys()):
            outcomes = buckets[b]
            # 桶内样本不足则跳过（同 S1-1 2026-07-20 修复：小样本胜率方差大，
            # 直接进保序回归会把整条曲线压死）。ai_decision_logs 样本通常比
            # SignalTradeFeedback 稀疏，默认 5 笔/桶即可参与。
            if len(outcomes) < min_bucket:
                continue
            centers.append((b + 0.5) / 10.0)
            rates.append(sum(outcomes) / len(outcomes))
            weights.append(float(len(outcomes)))

        if len(centers) < 2:
            logger.info(
                f"[AiDecisionCalibrator] 样本 {len(pairs)} 但有效桶 <2，回退线性映射 "
                f"(base_rate={model.base_rate:.3f})"
            )
            return model

        iso = _pava_expand(rates, weights)
        model.xs = centers
        model.ys = [_clip(v, _PWIN_FLOOR, _PWIN_CAP) for v in iso]
        model.is_calibrated = True
        logger.info(
            f"[AiDecisionCalibrator] 校准完成 n={model.n_samples} base={model.base_rate:.3f} "
            f"buckets={list(zip([round(c, 2) for c in centers], [round(v, 3) for v in model.ys]))}"
        )
        return model

    def _load_samples(self, lookback_days: int) -> List[Tuple[float, bool]]:
        """拉取 (confidence, won) 样本对：buy/sell 已执行且已回填盈亏的决策。"""
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import AIDecisionLog
        except Exception as e:
            logger.debug(f"[AiDecisionCalibrator] 无法加载模型依赖: {e}")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        db = AnalyticsSessionLocal()
        try:
            rows = (
                db.query(
                    AIDecisionLog.decision_snapshot,
                    AIDecisionLog.mid_confidence,
                    AIDecisionLog.realized_pnl,
                )
                .filter(
                    AIDecisionLog.operation.in_(["buy", "sell"]),
                    AIDecisionLog.executed == "true",
                    AIDecisionLog.realized_pnl.isnot(None),
                    AIDecisionLog.realized_pnl != 0,
                    AIDecisionLog.decision_time >= cutoff,
                )
                .all()
            )
        except Exception as e:
            logger.debug(f"[AiDecisionCalibrator] 样本查询失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return []
        finally:
            db.close()

        pairs: List[Tuple[float, bool]] = []
        for snap, mid_conf, pnl in rows:
            conf = _extract_confidence(snap, mid_conf)
            if conf is None:
                continue
            # 剔除降级/禁开标记（0）与极端边缘值，保留可校准区间
            if conf < 0.05 or conf > 0.95:
                continue
            try:
                won = float(pnl or 0.0) > 0.0
            except (TypeError, ValueError):
                continue
            pairs.append((conf, won))
        return pairs

    def get_stats(self) -> Dict[str, object]:
        """给前端/可观测性用的快照（S2-11 决策链路视图消费）。"""
        model = self._get_model()
        base: Dict[str, object] = {
            "calibrated": False,
            "n_samples": 0,
            "base_rate": 0.45,
            "source": "ai_decision_logs",
            "note": "no_samples",
        }
        if model is None:
            return base
        # 各桶样本明细（含被 MIN_BUCKET 跳过的小桶，供质量评估）
        raw_buckets: Dict[str, Dict[str, object]] = {}
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import AIDecisionLog

            cutoff = datetime.now(timezone.utc) - timedelta(
                days=int(self._cfg("LOOKBACK_DAYS", 45) or 45))
            db = AnalyticsSessionLocal()
            try:
                rows = (
                    db.query(
                        AIDecisionLog.decision_snapshot,
                        AIDecisionLog.mid_confidence,
                        AIDecisionLog.realized_pnl,
                    )
                    .filter(
                        AIDecisionLog.operation.in_(["buy", "sell"]),
                        AIDecisionLog.executed == "true",
                        AIDecisionLog.realized_pnl.isnot(None),
                        AIDecisionLog.realized_pnl != 0,
                        AIDecisionLog.decision_time >= cutoff,
                    )
                    .all()
                )
            except Exception:
                rows = []
            finally:
                db.close()
            tmp: Dict[int, List[float]] = {}
            for snap, mid_conf, pnl in rows:
                conf = _extract_confidence(snap, mid_conf)
                if conf is None or conf < 0.05 or conf > 0.95:
                    continue
                try:
                    won = float(pnl or 0.0) > 0.0
                except (TypeError, ValueError):
                    continue
                b = int(_clip(conf, 0.0, 0.999) * 10)
                tmp.setdefault(b, []).append(1.0 if won else 0.0)
            for b in sorted(tmp.keys()):
                outs = tmp[b]
                raw_buckets[f"{(b + 0.5) / 10.0:.2f}"] = {
                    "n": len(outs),
                    "win_rate": round(sum(outs) / len(outs), 4),
                }
        except Exception as e:
            logger.debug(f"[AiDecisionCalibrator] get_stats 桶明细失败: {e}")

        return {
            "calibrated": model.is_calibrated,
            "n_samples": model.n_samples,
            "base_rate": round(model.base_rate, 4),
            "source": "ai_decision_logs",
            "note": "isotonic" if model.is_calibrated else "insufficient_samples",
            "curve": [
                {"confidence": round(x, 3), "p_win": round(y, 4)}
                for x, y in zip(model.xs, model.ys)
            ],
            "buckets": raw_buckets,
            "fitted_at": model.fitted_at,
        }


# 模块级单例
ai_decision_calibrator = AiDecisionConfidenceCalibrator()
