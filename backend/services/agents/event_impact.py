# -*- coding: utf-8 -*-
"""EventImpact Agent（v3 方向 3，p2-agents-b）：事件冲击的持续跟踪与显著性复核。

`p1-event-study` 已经能对库内每种事件类型出一份冲击报告（最优持有期、方向化超额、
Wilson 命中区间、成本门）。但**一次性报告不等于结论**：样本会增长、显著性会漂移、
今天显著的类型下个月可能就不显著了。本 Agent 做三件报告本身做不到的事：

  1. **稳定性跟踪**：对比本轮与上轮报告，找出显著性翻转（sig → not sig 或反向）的类型；
  2. **可证伪预测**：对当前显著的类型落 `event_impact` 预测——未来该类型的超额仍为正；
     到期用**新发生的事件**回测评分，而不是拿老样本自证；
  3. **实验卡**：显著且稳定的类型 → 提"接入策略"的实验卡；曾显著但已翻转的 → 提"下架"卡。

不造数：`n_used < min_n` 的类型一律不下结论、不落预测；报告缺失时记 errors 并跳过。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.services.agents.base import (
    ACTION_PROPOSE_EXPERIMENT,
    Advice,
    ObservationAgent,
    Prediction,
    env_float,
    env_int,
    env_true,
    now_ms,
    read_latest,
)

logger = logging.getLogger(__name__)

AGENT_ID = "event_impact"
KIND_EVENT_IMPACT = "event_impact"


def load_reports(*, refresh: bool = False) -> Tuple[Dict[str, Any], Optional[int]]:
    """取事件研究报告 → ({event_type: report}, 报告生成时间)。

    默认读 `event_study` 定时任务落盘的 `latest.json`（结构 `{"ts_ms", "types": {...}}`）；
    `refresh=True` 时现算全量（分钟级，只在人工触发时用）。
    """
    from backend.research import event_study

    if refresh:
        reports = event_study.run_all()
        return {et: r.to_dict() for et, r in reports.items()}, None
    latest = event_study.latest_report()
    if not latest:
        return {}, None
    types = latest.get("types")
    if isinstance(types, dict):
        return types, latest.get("ts_ms")
    return {}, latest.get("ts_ms")


class EventImpactAgent(ObservationAgent):
    agent_id = AGENT_ID
    description = "事件冲击显著性跟踪：稳定性翻转检测 + 可证伪预测 + 接入/下架实验卡"
    kinds = (KIND_EVENT_IMPACT,)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.min_n = env_int("AGENT_EVENT_IMPACT_MIN_N", 30)
        self.window_h = env_float("AGENT_EVENT_IMPACT_WINDOW_H", 336.0)   # 14 天后复核
        # 现算全量事件研究很重（分钟级），默认读 event_study 定时任务落盘的最新报告
        self.refresh = env_true("AGENT_EVENT_IMPACT_REFRESH", False)

    def analyze(self, errors: List[str]) -> Dict[str, Any]:
        report_ms: Optional[int] = None
        try:
            reports, report_ms = load_reports(refresh=self.refresh)
        except Exception as exc:
            errors.append(f"load_reports: {exc}")
            reports = {}
        if not reports:
            errors.append("无事件研究报告（event_study 尚未产出或读取失败）")
            return {"n_types": 0, "types": [], "significant": [], "flips": []}
        # 报告太旧就不该拿来下结论——事件研究是每日任务，超过 3 天说明任务掉了
        if report_ms and now_ms() - int(report_ms) > 3 * 86400 * 1000:
            stale_d = round((now_ms() - int(report_ms)) / 86400000, 1)
            errors.append(f"事件研究报告已过期 {stale_d} 天，本轮只读不落预测")

        prev = (read_latest(self.agent_id) or {}).get("findings") or {}
        prev_sig = {t["event_type"]: t for t in (prev.get("types") or [])}

        types: List[Dict[str, Any]] = []
        flips: List[Dict[str, Any]] = []
        for et, r in sorted(reports.items()):
            n_used = int(r.get("n_used") or 0)
            sig = bool(r.get("significant"))
            item = {
                "event_type": et,
                "n_used": n_used,
                "n_raw": int(r.get("n_events_raw") or 0),
                "significant": sig,
                "optimal_hold_h": r.get("optimal_hold_h"),
                "mean_at_opt": r.get("mean_at_opt"),
                "mean_lo_at_opt": r.get("mean_lo_at_opt"),
                "hit_rate": r.get("hit_rate"),
                "wilson": [r.get("wilson_lo"), r.get("wilson_hi")],
                "significant_horizons": r.get("significant_horizons") or [],
                "conclusive": n_used >= self.min_n,
            }
            types.append(item)

            old = prev_sig.get(et)
            if old and old.get("conclusive") and item["conclusive"]:
                if bool(old.get("significant")) != sig:
                    flips.append({
                        "event_type": et,
                        "from": bool(old.get("significant")), "to": sig,
                        "n_used_before": old.get("n_used"), "n_used_now": n_used,
                        "mean_before": old.get("mean_at_opt"), "mean_now": item["mean_at_opt"],
                    })

        significant = [t for t in types if t["significant"] and t["conclusive"]]
        thin = [t["event_type"] for t in types if not t["conclusive"]]
        stale = bool(report_ms and now_ms() - int(report_ms) > 3 * 86400 * 1000)
        return {
            "n_types": len(types), "types": types,
            "significant": significant, "flips": flips,
            "thin_types": thin, "min_n": self.min_n,
            "source": "refresh" if self.refresh else "latest_report",
            "report_ms": report_ms, "stale": stale,
        }

    def predict(self, findings: Dict[str, Any]) -> List[Prediction]:
        if findings.get("stale"):
            return []       # 报告过期：不拿陈旧结论去赌未来
        out: List[Prediction] = []
        for t in findings.get("significant") or []:
            out.append(Prediction(
                kind=KIND_EVENT_IMPACT,
                subject=t["event_type"],
                prediction={
                    "event_type": t["event_type"],
                    "direction": "positive" if float(t.get("mean_at_opt") or 0) > 0 else "negative",
                    "horizon_h": t.get("optimal_hold_h"),
                    "observed_mean": t.get("mean_at_opt"),
                    "observed_n": t.get("n_used"),
                },
                horizon_ms=int(self.window_h * 3600 * 1000),
                confidence=min(0.7, 0.4 + 0.1 * len(t.get("significant_horizons") or [])),
            ))
        return out

    def advise(self, findings: Dict[str, Any]) -> List[Advice]:
        if findings.get("stale"):
            return []
        out: List[Advice] = []
        for t in (findings.get("significant") or [])[:3]:
            et = t["event_type"]
            hold = t.get("optimal_hold_h")
            out.append(Advice(
                action=ACTION_PROPOSE_EXPERIMENT,
                target=et,
                severity=2,
                reason=f"{et} 在 n={t['n_used']} 样本上显著："
                       f"{hold}h 平均超额 {t.get('mean_at_opt')}（95% 下界 {t.get('mean_lo_at_opt')}）",
                params={
                    "title": f"{et} 事件影子接入",
                    "hypothesis": f"{et} 的 {hold}h 方向化超额在历史样本上显著（95% 下界 "
                                  f"{t.get('mean_lo_at_opt')} > 成本门）；若以影子车道接入，"
                                  f"未来窗口内该事件源的信号超额下界应继续高于往返成本。",
                    "change": {"apply": False, "kind": "event_lane",
                               "proposal": f"以 {hold}h 持有期把 {et} 接入事件影子车道",
                               "event_type": et, "horizon_h": hold},
                    "expected_metrics": [
                        {"metric": "excess_lower_bp", "op": ">", "threshold": 14,
                         "scope": f"source:event_{et}", "min_n": self.min_n},
                        {"metric": "signal_n", "op": ">=", "threshold": self.min_n,
                         "scope": f"source:event_{et}"},
                    ],
                    "window_hours": int(self.window_h),
                    "rollback_condition": "若窗口内超额 95% 下界跌破 0，停止该事件类型的影子入账",
                },
            ))

        for f in (findings.get("flips") or [])[:3]:
            if f["from"] and not f["to"]:
                out.append(Advice(
                    action=ACTION_PROPOSE_EXPERIMENT,
                    target=f["event_type"],
                    severity=3,
                    reason=f"{f['event_type']} 显著性翻转：曾显著，样本增至 {f['n_used_now']} 后不再显著",
                    params={
                        "title": f"{f['event_type']} 显著性复核",
                        "hypothesis": f"{f['event_type']} 早期的显著性可能是小样本假象"
                                      f"（n {f['n_used_before']} → {f['n_used_now']} 后翻转）；"
                                      "若确认无 edge，应停止基于该事件的任何加仓逻辑。",
                        "change": {"apply": False, "kind": "event_lane",
                                   "proposal": f"暂停 {f['event_type']} 的策略消费，仅保留观察",
                                   "event_type": f["event_type"]},
                        "expected_metrics": [
                            {"metric": "excess_lower_bp", "op": ">", "threshold": 0,
                             "scope": f"source:event_{f['event_type']}", "min_n": self.min_n},
                        ],
                        "window_hours": int(self.window_h),
                        "rollback_condition": "若复核期内重新显著，恢复消费",
                    },
                ))
        return out


def score_event_impact(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """`event_impact` 到期评分：**只用预测之后新发生的事件**复核方向，避免拿老样本自证。"""
    pred = row.get("prediction") or {}
    if isinstance(pred, str):
        try:
            pred = json.loads(pred)
        except Exception:
            return None
    et = pred.get("event_type")
    want = pred.get("direction")
    if not et or want not in ("positive", "negative"):
        return None
    created = int(row.get("created_ms") or 0)
    expires = int(row.get("expires_ms") or 0)
    if not created or not expires:
        return None

    try:
        from backend.research.event_study import (
            HOUR,
            load_events,
            load_hourly_closes,
            mean_se_interval,
        )

        events = [e for e in load_events(et)
                  if created <= int(e.get("ts_ms") or 0) <= expires]
        if len(events) < 8:
            return None          # 窗口内新事件太少 → 保持 open，不造分
        hold = int(pred.get("horizon_h") or 4)
        syms = ["BTC"] + [e.get("symbol") or "BTC" for e in events]
        lo = min(int(e["ts_ms"]) // 1000 for e in events) - 2 * HOUR
        hi = max(int(e["ts_ms"]) // 1000 for e in events) + (hold + 2) * HOUR
        closes = load_hourly_closes(syms, lo, hi)

        def px(sym: str, ts_s: int) -> Optional[float]:
            return (closes.get(sym) or {}).get((ts_s // HOUR) * HOUR)

        from backend.research.event_study import _kline_base, event_sign

        xs: List[float] = []
        for e in events:
            sym = _kline_base(e.get("symbol"))
            t = int(e["ts_ms"]) // 1000
            p0, p1 = px(sym, t), px(sym, t + hold * HOUR)
            b0, b1 = px("BTC", t), px("BTC", t + hold * HOUR)
            if not p0 or not p1:
                continue
            sign = event_sign(et, e.get("direction"))
            if sign == 0:
                sign = 1
            ret = p1 / p0 - 1.0
            if sym != "BTC" and b0 and b1:
                ret -= (b1 / b0 - 1.0)
            xs.append(sign * ret)
        if len(xs) < 8:
            return None
        mean, se, ci_lo, ci_hi = mean_se_interval(xs)
    except Exception as exc:
        logger.warning("[%s] 评分取数失败: %s", AGENT_ID, exc)
        return None

    actual = "positive" if mean > 0 else "negative"
    return {"score": 1.0 if actual == want else 0.0,
            "outcome": {"n_new_events": len(xs), "mean_excess_bp": round(mean * 1e4, 2),
                        "ci_bp": [round(ci_lo * 1e4, 2), round(ci_hi * 1e4, 2)],
                        "actual": actual, "predicted": want, "horizon_h": pred.get("horizon_h")}}


def build() -> EventImpactAgent:
    return EventImpactAgent()
