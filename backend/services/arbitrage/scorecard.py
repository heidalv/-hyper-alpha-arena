# -*- coding: utf-8 -*-
"""套利 Scorecard（v3 方向 5，p2-arb-infra）。

方案要求的 KPI：**收益 / 占用 / 年化 / 回撤 / 对账误差**，再加 fill/maker/funding
捕获率。原料分散在 `arbitrage_positions`、`rebate_performance_logs`、
`live_income_ledger`、carry_sim 落盘——本模块只做**只读聚合**，不造数。

任何数据源取不到 → 该项为 None 并在 notes 说明，绝不用 0 冒充「没亏」。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "arb"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


def _db():
    from backend.database.connection import SessionLocal
    return SessionLocal()


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _max_drawdown(pnls: List[float]) -> Optional[float]:
    """按时间序累计净收益的最大回撤（绝对值，正数 = 回撤幅度）。样本 < 2 → None。"""
    if len(pnls) < 2:
        return None
    peak, mdd = 0.0, 0.0
    cum = 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return round(mdd, 4)


def _load_v3_positions(days: int) -> Dict[str, Any]:
    from sqlalchemy import text

    since = _now_ms() - days * 86400000
    # entry_time 是 TIMESTAMP；用 epoch 比较
    db = _db()
    notes: List[str] = []
    try:
        rows = db.execute(text(
            "SELECT strategy, status, mode, size_usd, pnl, accumulated_funding, "
            "long_size, short_size, delta, "
            "entry_time, close_time, exchange_long, exchange_short "
            "FROM arbitrage_positions "
            "WHERE EXTRACT(EPOCH FROM COALESCE(entry_time, created_at)) * 1000 >= :lo "
            "ORDER BY entry_time ASC NULLS LAST"
        ), {"lo": since}).mappings().all()
    except Exception as exc:
        notes.append(f"arbitrage_positions 读取失败: {exc}")
        return {"n": 0, "notes": notes, "rows": []}
    finally:
        db.close()

    out_rows = [dict(r) for r in rows]
    closed = [r for r in out_rows if str(r.get("status")) == "closed"]
    active = [r for r in out_rows if str(r.get("status")) == "active"]
    pnls = [_safe_float(r.get("pnl")) or 0.0 for r in closed]
    fundings = [_safe_float(r.get("accumulated_funding")) or 0.0 for r in closed]
    notionals = [_safe_float(r.get("size_usd")) or 0.0 for r in out_rows]
    total_pnl = sum(pnls)
    total_funding = sum(fundings)
    occupied = sum(_safe_float(r.get("size_usd")) or 0.0 for r in active)
    avg_notional = (sum(notionals) / len(notionals)) if notionals else None
    # 年化：窗口内净收益 / 平均占用 / (days/365)；无占用则 None
    ann = None
    if avg_notional and avg_notional > 0 and days > 0:
        ann = round((total_pnl / avg_notional) * (365.0 / days), 4)
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for r in out_rows:
        s = str(r.get("strategy") or "?")
        b = by_strategy.setdefault(s, {"n": 0, "n_closed": 0, "pnl": 0.0, "funding": 0.0})
        b["n"] += 1
        if str(r.get("status")) == "closed":
            b["n_closed"] += 1
            b["pnl"] += _safe_float(r.get("pnl")) or 0.0
            b["funding"] += _safe_float(r.get("accumulated_funding")) or 0.0
    return {
        "n": len(out_rows), "n_active": len(active), "n_closed": len(closed),
        "pnl": round(total_pnl, 4), "funding_captured": round(total_funding, 4),
        "occupied_usd": round(occupied, 2),
        "avg_notional_usd": round(avg_notional, 2) if avg_notional else None,
        "annualized": ann,
        "max_drawdown": _max_drawdown(pnls),
        "by_strategy": by_strategy,
        "paper_n": sum(1 for r in out_rows if str(r.get("mode") or "paper") == "paper"),
        "live_n": sum(1 for r in out_rows if str(r.get("mode")) == "live"),
        "notes": notes, "rows": out_rows,
    }


def _load_rebate_logs(days: int) -> Dict[str, Any]:
    from sqlalchemy import text

    since = _now_ms() - days * 86400000
    db = _db()
    notes: List[str] = []
    try:
        # rebate_performance_logs 列名因版本可能不同，尽量宽容
        rows = db.execute(text(
            "SELECT * FROM rebate_performance_logs "
            "WHERE EXTRACT(EPOCH FROM COALESCE(created_at, ts, updated_at)) * 1000 >= :lo "
            "ORDER BY 1 DESC LIMIT 2000"
        ), {"lo": since}).mappings().all()
    except Exception as exc:
        notes.append(f"rebate_performance_logs 读取失败: {exc}")
        return {"n": 0, "notes": notes}
    finally:
        db.close()

    pnls, rebates, points = [], [], []
    for r in rows:
        d = dict(r)
        p = _safe_float(d.get("total_pnl") if "total_pnl" in d else d.get("pnl"))
        if p is not None:
            pnls.append(p)
        rb = _safe_float(d.get("total_rebate") if "total_rebate" in d else d.get("rebate"))
        if rb is not None:
            rebates.append(rb)
        pt = _safe_float(d.get("total_points") if "total_points" in d else d.get("points"))
        if pt is not None:
            points.append(pt)
    return {
        "n": len(rows),
        "pnl": round(sum(pnls), 4) if pnls else None,
        "rebate": round(sum(rebates), 4) if rebates else None,
        "points": round(sum(points), 4) if points else None,
        "notes": notes,
    }


def _load_carry_sim() -> Dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "data" / "cashflow" / "carry_sim" / "latest.json"
    if not path.exists():
        return {"available": False, "notes": ["carry_sim latest.json 不存在"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"available": False, "notes": [f"carry_sim 读取失败: {exc}"]}
    execs = data.get("executions") or []
    return {
        "available": True,
        "ok": data.get("ok"),
        "venues": data.get("venues"),
        "n_combos": len(data.get("combos") or []),
        "n_executions": len(execs),
        "multi_venue_coverage": data.get("multi_venue_coverage"),
        "notes": data.get("notes") or [],
    }


def _load_maker_ratio() -> Dict[str, Any]:
    """实盘 maker 占比：有 live_income_ledger 才有；失败如实记。"""
    try:
        from backend.services.rebate_arb.live_income_ledger import build_live_income_ledger
        # 不强制指定账户——若函数需要 client，跳过
        # 多数部署下需要 client；这里只尝试无参或读缓存
        if not callable(build_live_income_ledger):
            return {"available": False, "notes": ["live_income_ledger 不可调用"]}
        # 签名通常需要 client；没有现成 client 时不强行拉，避免副作用
        return {"available": False, "notes": ["maker_ratio 需指定 live 账户客户端，scorecard 不主动建连"]}
    except Exception as exc:
        return {"available": False, "notes": [f"maker_ratio: {exc}"]}


def _recon_error_proxy(v3: Dict[str, Any]) -> Optional[float]:
    """对账误差代理：活跃仓的 |delta| / size 均值。无仓 → None。"""
    rows = [r for r in (v3.get("rows") or []) if str(r.get("status")) == "active"]
    if not rows:
        return None
    errs = []
    for r in rows:
        size = abs(_safe_float(r.get("long_size")) or 0) + abs(_safe_float(r.get("short_size")) or 0)
        delta = abs(_safe_float(r.get("delta")) or 0)
        if size > 0:
            errs.append(delta / size)
    if not errs:
        return None
    return round(sum(errs) / len(errs), 6)


def compute_scorecard(*, days: int = 30) -> Dict[str, Any]:
    days = max(1, min(int(days), 365))
    notes: List[str] = []
    v3 = _load_v3_positions(days)
    notes.extend(v3.get("notes") or [])
    rebate = _load_rebate_logs(days)
    notes.extend(rebate.get("notes") or [])
    carry = _load_carry_sim()
    notes.extend(carry.get("notes") or [])
    maker = _load_maker_ratio()
    notes.extend(maker.get("notes") or [])

    total_pnl = (v3.get("pnl") or 0.0) + (rebate.get("pnl") or 0.0)
    has_pnl = v3.get("n_closed", 0) > 0 or (rebate.get("pnl") is not None)

    gate = _promotion_gate(v3, rebate, carry)
    out = {
        "ts_ms": _now_ms(),
        "days": days,
        "kpi": {
            "pnl": round(total_pnl, 4) if has_pnl else None,
            "occupied_usd": v3.get("occupied_usd"),
            "annualized": v3.get("annualized"),
            "max_drawdown": v3.get("max_drawdown"),
            "recon_error": _recon_error_proxy(v3),
            "funding_captured": v3.get("funding_captured"),
            "rebate": rebate.get("rebate"),
            "points": rebate.get("points"),
            "maker_ratio": maker.get("maker_ratio"),
        },
        "v3": {k: v for k, v in v3.items() if k != "rows"},
        "rebate": rebate,
        "carry_sim": carry,
        "promotion_gate": gate,
        "notes": notes,
    }
    _persist(out)
    return out


def _promotion_gate(v3: Dict[str, Any], rebate: Dict[str, Any],
                    carry: Dict[str, Any]) -> Dict[str, Any]:
    """小资金晋升门：样本与收益下限。不过门 → carry live 拒开。"""
    min_n = _env_int("ARB_SCORECARD_MIN_N", 10)
    min_ann = float(os.getenv("ARB_SCORECARD_MIN_ANN", "0.05") or 0.05)
    n = int(v3.get("n_closed") or 0) + int(rebate.get("n") or 0)
    ann = v3.get("annualized")
    mdd = v3.get("max_drawdown")
    reasons = []
    if n < min_n:
        reasons.append(f"已平仓样本 {n} < {min_n}")
    if ann is None:
        reasons.append("年化无法计算（无占用或无平仓）")
    elif ann < min_ann:
        reasons.append(f"年化 {ann} < 门槛 {min_ann}")
    if mdd is not None and v3.get("pnl") is not None and v3["pnl"] > 0 and mdd > abs(v3["pnl"]):
        reasons.append(f"回撤 {mdd} 超过窗口净收益 {v3['pnl']}")
    if not carry.get("multi_venue_coverage"):
        reasons.append("资金费多场所覆盖不足")
    passed = not reasons
    return {
        "passed": passed,
        "min_n": min_n, "min_ann": min_ann,
        "n": n, "annualized": ann,
        "reasons": reasons,
        "note": "过门只是小资金资格，不等于自动开 Live",
    }


def _persist(payload: Dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "scorecard_latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("[arb.scorecard] 落盘失败: %s", exc)


def latest_scorecard() -> Optional[Dict[str, Any]]:
    path = DATA_DIR / "scorecard_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def scheduled_scorecard() -> Dict[str, Any]:
    from backend.core.tenant import set_system_identity
    set_system_identity()
    days = _env_int("ARB_SCORECARD_DAYS", 30)
    out = compute_scorecard(days=days)
    logger.info("[arb.scorecard] days=%s pnl=%s ann=%s gate=%s",
                days, (out.get("kpi") or {}).get("pnl"),
                (out.get("kpi") or {}).get("annualized"),
                (out.get("promotion_gate") or {}).get("passed"))
    return {"ok": True, "gate": out.get("promotion_gate"), "kpi": out.get("kpi")}
