# -*- coding: utf-8 -*-
"""SignalReview Agent — 信号复盘（v3 方向 3，p1-agents-a）。

确定性核心（只读 `signal_ledger`，无 LLM、无副作用）：
  1. 每个信号源的 命中率（Wilson 95% 区间）/ 平均超额 bp（含 95% 区间）/ 平均 Brier；
  2. **IC**：signed_strength = direction × strength 与实际 excess_bp 的 Spearman 秩相关；
     另给 confidence 与 hit 的相关（校准 IC），两者一起看才知道"方向准"还是"置信度准"；
  3. **半衰期**：按 horizon 分桶的平均超额，找超额衰减到峰值一半的 horizon → half_life_hours；
  4. **regime 分层**：source × regime 的命中率与超额（regime 取信号生成时快照的 signal_ledger.regime）；
  5. 异常判定：超额 95% 上界 < 0（显著为负）→ 建议停用；显著为正 → 建议加权；样本不足 → 只观察。

预测（可证伪，落 agent_predictions，kind=`source_edge`）：
  对每个样本足够的信号源，预测「未来 window_h 小时内该源新评分信号的平均超额 bp 的符号」。
  到期由 `outcomes.score_source_edge` 用**同一张账本的真实结果**评分：符号一致 → 1.0，否则 0.0，
  窗口内新样本不足 → 返回 None（保持 open，最终 48h 后 void，不造分）。

建议（observe 模式只记录不执行）：
  adjust_source_weight / disable_source；advise 模式额外把它写成 proposed 实验卡（假设/改动/阈值/回滚）。

LLM 解释层：默认关闭（`AGENT_LLM_ENABLED=false`）。开启后走 ModelGateway.dual_call(task="signal_review")，
只负责"解释异常 + 提假设"，不改任何数字；共识不达标就丢弃（不造数）。
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.agents.base import (
    Advice,
    ObservationAgent,
    Prediction,
    env_float,
    env_int,
    env_true,
    now_ms,
)

logger = logging.getLogger(__name__)

AGENT_ID = "signal_review"
KIND_SOURCE_EDGE = "source_edge"

# horizon 分桶（小时上界）；用于半衰期曲线
HORIZON_BUCKETS_H: Tuple[float, ...] = (1, 4, 12, 24, 48, 72, 168, 720)


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman 秩相关（含并列名次的平均秩）。n < 10 或任一侧无变化 → None。"""
    n = len(xs)
    if n < 10 or n != len(ys):
        return None

    def ranks(vals: Sequence[float]) -> Optional[List[float]]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out if len(set(out)) > 1 else None

    rx, ry = ranks(xs), ranks(ys)
    if rx is None or ry is None:
        return None
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return (num / den) if den > 1e-12 else None


def load_scored_signals(days: int, *, limit: int = 20000) -> List[Dict[str, Any]]:
    """近 N 天 status='scored' 的信号明细（只取评分需要的列）。"""
    from sqlalchemy import text

    from backend.services.analysis.ledgers import _db, ensure_schema

    ensure_schema()
    since = now_ms() - int(days) * 86400 * 1000
    db = _db()
    try:
        rows = db.execute(
            text(
                """
                SELECT source, symbol, direction, strength, confidence, horizon_ms, regime,
                       ret_bp, excess_bp, hit, brier, created_ms, scored_ms
                FROM signal_ledger
                WHERE status = 'scored' AND created_ms >= :since
                ORDER BY created_ms DESC LIMIT :lim
                """
            ),
            {"since": since, "lim": int(limit)},
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[SignalReview] 读取 signal_ledger 失败: %s", exc)
        return []
    finally:
        db.close()


def source_edge_since(source: str, since_ms: int, until_ms: int) -> Dict[str, Any]:
    """某信号源在 [since, until] 内**评分完成**的信号统计（评分器与预测都用它，口径唯一）。"""
    from sqlalchemy import text

    from backend.services.analysis.ledgers import _db, ensure_schema

    ensure_schema()
    db = _db()
    try:
        row = db.execute(
            text(
                """
                SELECT COUNT(*) AS n, AVG(excess_bp) AS avg_excess, AVG(ret_bp) AS avg_ret,
                       AVG(CAST(hit AS DOUBLE PRECISION)) AS hit_rate
                FROM signal_ledger
                WHERE source = :src AND status = 'scored' AND scored_ms >= :lo AND scored_ms <= :hi
                """
            ),
            {"src": source, "lo": int(since_ms), "hi": int(until_ms)},
        ).mappings().first()
        return dict(row or {})
    except Exception as exc:
        logger.warning("[SignalReview] source_edge_since 失败 %s: %s", source, exc)
        return {}
    finally:
        db.close()


def _bucket_of(horizon_ms: Any) -> Optional[float]:
    try:
        h = float(horizon_ms) / 3600000.0
    except Exception:
        return None
    for b in HORIZON_BUCKETS_H:
        if h <= b:
            return b
    return HORIZON_BUCKETS_H[-1]


def _half_life_hours(curve: List[Dict[str, Any]], min_n: int) -> Optional[float]:
    """超额-horizon 曲线上，超额降到峰值一半的 horizon（线性插值）。峰值 ≤ 0 或样本不足 → None。"""
    pts = [(float(c["bucket_h"]), float(c["avg_excess_bp"])) for c in curve if int(c.get("n") or 0) >= min_n]
    if len(pts) < 2:
        return None
    peak_h, peak_v = max(pts, key=lambda p: p[1])
    if peak_v <= 0:
        return None
    half = peak_v / 2.0
    after = [p for p in pts if p[0] > peak_h]
    prev = (peak_h, peak_v)
    for h, v in sorted(after):
        if v <= half:
            if abs(prev[1] - v) < 1e-9:
                return h
            frac = (prev[1] - half) / (prev[1] - v)
            return round(prev[0] + frac * (h - prev[0]), 2)
        prev = (h, v)
    return None


class SignalReviewAgent(ObservationAgent):
    agent_id = AGENT_ID
    description = "信号复盘：各信号源命中率 / IC / 半衰期 / regime 分层，异常源提权重或停用假设"
    kinds = (KIND_SOURCE_EDGE,)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.days = env_int("AGENT_SIGNAL_REVIEW_DAYS", 30)
        self.min_n = env_int("AGENT_SIGNAL_REVIEW_MIN_N", 20)          # 出结论所需最少样本
        self.min_n_predict = env_int("AGENT_SIGNAL_REVIEW_MIN_N_PRED", 30)  # 落预测所需最少样本
        self.window_h = env_float("AGENT_SIGNAL_REVIEW_WINDOW_H", 168.0)    # 预测窗口（默认 7d）
        self.bucket_min_n = env_int("AGENT_SIGNAL_REVIEW_BUCKET_MIN_N", 10)

    # ---------------- 确定性核心 ----------------
    def analyze(self, errors: List[str]) -> Dict[str, Any]:
        from backend.research.event_study import mean_se_interval, wilson_interval

        rows = load_scored_signals(self.days)
        if not rows:
            errors.append("signal_ledger 近窗无已评分信号")
        by_source: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_source.setdefault(str(r.get("source") or "unknown"), []).append(r)

        sources: List[Dict[str, Any]] = []
        for src, items in sorted(by_source.items(), key=lambda kv: -len(kv[1])):
            hits = [int(x["hit"]) for x in items if x.get("hit") is not None]
            excess = [float(x["excess_bp"]) for x in items if x.get("excess_bp") is not None]
            rets = [float(x["ret_bp"]) for x in items if x.get("ret_bp") is not None]
            briers = [float(x["brier"]) for x in items if x.get("brier") is not None]
            n = len(items)
            hit_lo, hit_hi = wilson_interval(sum(hits), len(hits)) if hits else (0.0, 1.0)
            ex_mean, ex_se, ex_lo, ex_hi = mean_se_interval(excess) if excess else (0.0, 0.0, 0.0, 0.0)

            # IC：signed strength vs 实际超额；strength 缺失时退回 direction 单独看
            sx, sy = [], []
            cx, cy = [], []
            for x in items:
                if x.get("excess_bp") is None:
                    continue
                d = int(x.get("direction") or 0)
                s = x.get("strength")
                if s is not None:
                    sx.append(d * float(s))
                    sy.append(float(x["excess_bp"]))
                if x.get("confidence") is not None and x.get("hit") is not None:
                    cx.append(float(x["confidence"]))
                    cy.append(float(x["hit"]))
            ic = _spearman(sx, sy)
            ic_calib = _spearman(cx, cy)

            # 半衰期曲线
            buckets: Dict[float, List[float]] = {}
            for x in items:
                b = _bucket_of(x.get("horizon_ms"))
                if b is None or x.get("excess_bp") is None:
                    continue
                buckets.setdefault(b, []).append(float(x["excess_bp"]))
            curve = [
                {"bucket_h": b, "n": len(v), "avg_excess_bp": round(sum(v) / len(v), 2)}
                for b, v in sorted(buckets.items())
            ]
            half_life = _half_life_hours(curve, self.bucket_min_n)

            # regime 分层
            by_regime: List[Dict[str, Any]] = []
            reg_map: Dict[str, List[Dict[str, Any]]] = {}
            for x in items:
                reg_map.setdefault(str(x.get("regime") or "unknown"), []).append(x)
            for reg, sub in sorted(reg_map.items(), key=lambda kv: -len(kv[1])):
                sub_hits = [int(y["hit"]) for y in sub if y.get("hit") is not None]
                sub_ex = [float(y["excess_bp"]) for y in sub if y.get("excess_bp") is not None]
                by_regime.append({
                    "regime": reg,
                    "n": len(sub),
                    "hit_rate": round(sum(sub_hits) / len(sub_hits), 4) if sub_hits else None,
                    "avg_excess_bp": round(sum(sub_ex) / len(sub_ex), 2) if sub_ex else None,
                })

            verdict, verdict_reason = self._verdict(n, ex_lo, ex_hi, ex_mean)
            sources.append({
                "source": src,
                "n": n,
                "hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
                "hit_ci": [round(hit_lo, 4), round(hit_hi, 4)],
                "avg_ret_bp": round(sum(rets) / len(rets), 2) if rets else None,
                "avg_excess_bp": round(ex_mean, 2),
                "excess_ci_bp": [round(ex_lo, 2), round(ex_hi, 2)],
                "avg_brier": round(sum(briers) / len(briers), 4) if briers else None,
                "ic_strength": round(ic, 4) if ic is not None else None,
                "ic_confidence": round(ic_calib, 4) if ic_calib is not None else None,
                "half_life_hours": half_life,
                "horizon_curve": curve,
                "by_regime": by_regime,
                "verdict": verdict,
                "verdict_reason": verdict_reason,
            })

        return {
            "days": self.days,
            "scored_signals": len(rows),
            "sources": sources,
            "min_n": self.min_n,
            "anomalies": [s for s in sources if s["verdict"] in ("disable", "scale_down", "scale_up")],
            "llm": self._llm_layer(sources, errors),
        }

    def _verdict(self, n: int, ex_lo: float, ex_hi: float, ex_mean: float) -> Tuple[str, str]:
        """只有 95% 区间整体落在一侧才敢下结论，其余一律 observe。"""
        if n < self.min_n:
            return "observe", f"样本 {n} < {self.min_n}"
        if ex_hi < 0:
            return "disable", f"超额 95% 上界 {ex_hi:.1f}bp < 0，显著为负"
        if ex_lo > 0:
            return "scale_up", f"超额 95% 下界 {ex_lo:.1f}bp > 0，显著为正"
        if ex_mean < -5:
            return "scale_down", f"均值超额 {ex_mean:.1f}bp 为负但不显著"
        return "keep", "区间跨 0，证据不足"

    # ---------------- 预测 ----------------
    def predict(self, findings: Dict[str, Any]) -> List[Prediction]:
        out: List[Prediction] = []
        horizon_ms = int(self.window_h * 3600 * 1000)
        for s in findings.get("sources") or []:
            if int(s.get("n") or 0) < self.min_n_predict:
                continue
            mean_ex = float(s.get("avg_excess_bp") or 0.0)
            direction = 1 if mean_ex > 0 else (-1 if mean_ex < 0 else 0)
            if direction == 0:
                continue
            # 置信度 = |均值| 相对区间半宽的信噪比映射到 0.5–0.9（不外推、不造分）
            lo, hi = (s.get("excess_ci_bp") or [0.0, 0.0])[:2]
            half_width = max(1e-6, (float(hi) - float(lo)) / 2.0)
            snr = abs(mean_ex) / half_width
            conf = max(0.5, min(0.9, 0.5 + 0.2 * snr))
            out.append(Prediction(
                kind=KIND_SOURCE_EDGE,
                subject=str(s["source"])[:48],
                prediction={
                    "source": s["source"],
                    "direction": direction,
                    "expected_excess_bp": mean_ex,
                    "baseline_n": int(s.get("n") or 0),
                    "baseline_hit_rate": s.get("hit_rate"),
                    "window_h": self.window_h,
                    "min_n": max(5, self.min_n_predict // 3),
                },
                horizon_ms=horizon_ms,
                confidence=round(conf, 3),
            ))
        return out

    # ---------------- 建议 ----------------
    def advise(self, findings: Dict[str, Any]) -> List[Advice]:
        out: List[Advice] = []
        for s in findings.get("anomalies") or []:
            v = s["verdict"]
            if v == "disable":
                out.append(Advice(
                    action="disable_source", target=str(s["source"]), severity=4,
                    params={"n": s["n"], "avg_excess_bp": s["avg_excess_bp"], "excess_ci_bp": s["excess_ci_bp"]},
                    reason=s["verdict_reason"],
                ))
            elif v in ("scale_up", "scale_down"):
                factor = 1.25 if v == "scale_up" else 0.6
                out.append(Advice(
                    action="adjust_source_weight", target=str(s["source"]), severity=2,
                    params={"factor": factor, "n": s["n"], "avg_excess_bp": s["avg_excess_bp"],
                            "half_life_hours": s.get("half_life_hours"), "ic_strength": s.get("ic_strength")},
                    reason=s["verdict_reason"],
                ))
        return out

    # ---------------- LLM 解释层（默认关闭） ----------------
    def _llm_layer(self, sources: List[Dict[str, Any]], errors: List[str]) -> Optional[Dict[str, Any]]:
        if not env_true("AGENT_LLM_ENABLED", False):
            return None
        try:
            from backend.services.analysis.model_gateway import get_model_gateway

            top = sorted(sources, key=lambda s: -int(s.get("n") or 0))[:12]
            system = (
                "你是量化信号复盘分析员。只依据给出的账本统计作答，禁止编造任何未给出的数字；"
                "对每个异常信号源解释可能原因并给出可证伪的假设。输出单个 JSON 对象，不要 Markdown。"
            )
            import json as _json

            user = "【信号源统计（近%d天，真实账本）】\n%s" % (self.days, _json.dumps(top, ensure_ascii=False, default=str))
            cres = get_model_gateway().dual_call(
                "signal_review", system, user, max_output_tokens=1600, timeout_s=180.0,
            )
            return {"accepted": cres.accepted, "consensus_score": cres.consensus_score,
                    "run_id": cres.run_id, "final": cres.final if cres.accepted else None}
        except Exception as exc:
            errors.append(f"llm_layer: {exc}")
            return None


def build() -> SignalReviewAgent:
    return SignalReviewAgent()


# ─────────────────────────── 到期评分器 ───────────────────────────
def score_source_edge(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """kind=source_edge 的 outcome 评估器（注册到 ledgers）。

    用预测窗口内**该源真实评分完成**的信号平均超额 bp 判定方向对错。
    窗口内新样本 < min_n → 返回 None（保持 open；48h 后由 score_due 标 void）。
    """
    pred = row.get("prediction") or {}
    source = str(pred.get("source") or row.get("subject") or "").strip()
    if not source:
        return {"score": 0.0, "outcome": {"error": "预测缺少 source"}}
    min_n = int(pred.get("min_n") or 5)
    stats = source_edge_since(source, int(row["created_ms"]), int(row["expires_ms"]))
    n = int(stats.get("n") or 0)
    if n < min_n:
        return None
    avg_excess = stats.get("avg_excess")
    if avg_excess is None:
        return None
    actual_dir = 1 if float(avg_excess) > 0 else (-1 if float(avg_excess) < 0 else 0)
    predicted_dir = int(pred.get("direction") or 0)
    score = 1.0 if (actual_dir != 0 and actual_dir == predicted_dir) else 0.0
    return {
        "score": score,
        "outcome": {
            "source": source,
            "window_n": n,
            "actual_avg_excess_bp": round(float(avg_excess), 2),
            "actual_hit_rate": stats.get("hit_rate"),
            "predicted_direction": predicted_dir,
            "predicted_excess_bp": pred.get("expected_excess_bp"),
        },
    }
