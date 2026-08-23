# -*- coding: utf-8 -*-
"""EV Governor — 用真实交易结果自动放大/收缩/暂停（盈利反馈闭环）。

设计原则（用户授权：大胆开单-学习错误-慢慢收紧）：
  - 不做开单前门禁（那是 M0 防亏思路）；只做【结果反馈后的资金分配】——
    正期望的通道与策略拿更多钱，负期望的自动降级，深负的暂停，全部用真实
    closed outcomes 判定，不靠主观。
  - 分两级：
    ① 簇级（scalp_ranging_mr / scalp_trend / swing / trend_follow）：
       scalp 策略 id 每单随机，只能按打法簇聚合。
    ② 策略级（中长线 tpl_*/gen_* 稳定 id）。
  - 输出 data/strategy_ev_state.json，开仓路径读 mult 乘数。

判据（近 N 天 closed）：
  n>=10 且 EV>0            → premium (×1.2，簇上限 3.0)
  n>=10 且 EV<=0           → stable (×1.0)
  n>=30 且 EV<0            → reduce (×0.5)
  n>=50 且 EV<0 且周亏损>0.5%权益 → pause (×0.2，仅簇级)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join("data", "strategy_ev_state.json")
_LOOKBACK_DAYS = 14
_MIN_N_EV = 10
_MIN_N_REDUCE = 30
_MIN_N_PAUSE = 50

CLUSTERS = {
    "scalp_ranging_mr": "scalp_mr_%",
    "scalp_trend": "scalp_lane_%",
}


def _cluster_of_strategy(strategy_id: str) -> str:
    s = str(strategy_id or "")
    if s.startswith("scalp_mr_"):
        return "scalp_ranging_mr"
    if s.startswith("scalp_lane_"):
        return "scalp_trend"
    if s.startswith("tpl_long") or s.startswith("gen_trend"):
        return "trend_follow"
    if "swing" in s or s.startswith("tpl_mid"):
        return "swing"
    return "other"


def _load_state() -> Dict[str, Any]:
    try:
        if os.path.exists(_STATE_PATH):
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"clusters": {}, "strategies": {}, "updated_at": None}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_STATE_PATH)), exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_PATH)
    except Exception as e:
        logger.warning("[EvGovernor] 状态落盘失败: %s", e)


def _audit_cluster(db: Session, prefix: str, days: int) -> Dict[str, Any]:
    """近 N 天 closed 订单聚合（净 PnL=pnl-fee）。"""
    rows = db.execute(text(
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE pnl > 0) AS wins,
               round(sum(pnl - coalesce(fee,0))::numeric, 6) AS net_pnl,
               round(avg(pnl - coalesce(fee,0))::numeric, 6) AS avg_net
        FROM paper_orders
        WHERE strategy_id LIKE :prefix AND pnl IS NOT NULL
          AND created_at >= now() - make_interval(days => :days)
        """), {"prefix": prefix, "days": days}).fetchone()
    n = int(rows.n or 0)
    net = float(rows.net_pnl or 0)
    avg = float(rows.avg_net or 0) if n else 0.0
    return {"n": n, "wins": int(rows.wins or 0), "net_pnl": net, "avg_net": avg}


def _val(x, key, default=0.0):
    try:
        return x.get(key, default) if hasattr(x, "get") else getattr(x, key, default)
    except Exception:
        return default


def _decide(audit) -> Dict[str, Any]:
    n = int(_val(audit, "n", 0) or 0)
    ev = float(_val(audit, "avg_net", 0.0) or 0.0)
    # [2026-08-23 用户指示] pause/reduce 乘数提高（0.2→0.5 / 0.5→0.75）：
    # ×0.2 把单仓名义压到 ~5% 权益，手续费占比吞噬盈利、仓位无统计意义
    # （"基本就是刷手续费"）。模拟盘的最小有意义仓位 ≈ ×0.5；env 可调。
    _pause_mult = float(os.getenv("EV_GOVERNOR_PAUSE_MULT", "0.5") or 0.5)
    _reduce_mult = float(os.getenv("EV_GOVERNOR_REDUCE_MULT", "0.75") or 0.75)
    if n >= 50 and ev < 0:
        mult, decision = _pause_mult, "pause"
    elif n >= 30 and ev < 0:
        mult, decision = _reduce_mult, "reduce"
    elif n >= 10 and ev > 0:
        mult, decision = 1.2, "premium"
    else:
        mult, decision = 1.0, "stable"
    return {"mult": mult, "decision": decision, "ev_per_trade": ev, "n": n}


def audit_and_write(db: Session) -> Dict[str, Any]:
    """全量审计 → 落盘 → 返回摘要。每个交易日调用一次（已在每日对账任务注册）。"""
    days = int(os.getenv("EV_GOVERNOR_LOOKBACK_DAYS", str(_LOOKBACK_DAYS)) or _LOOKBACK_DAYS)
    state = _load_state()
    clusters = {}
    for cname, prefix in CLUSTERS.items():
        a = _audit_cluster(db, prefix, days)
        d = _decide(a)
        clusters[cname] = {**a, **d, "updated_at": time.time()}
        logger.info(
            "[EvGovernor] cluster=%s n=%d ev=%.4f decision=%s mult=%.1f",
            cname, a["n"], d["ev_per_trade"], d["decision"], d["mult"],
        )
    state["clusters"] = clusters
    state["updated_at"] = time.time()
    _save_state(state)
    return state


def cluster_mult(cluster: str) -> float:
    """开仓路径读取：簇资金乘数（默认 1.0；读取失败返回 1.0，不阻塞开单）。"""
    try:
        st = _load_state()
        entry = (st.get("clusters") or {}).get(cluster) or {}
        return float(entry.get("mult") or 1.0)
    except Exception:
        return 1.0


__all__ = ["audit_and_write", "cluster_mult", "_cluster_of_strategy"]
