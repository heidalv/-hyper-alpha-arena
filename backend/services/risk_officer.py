# -*- coding: utf-8 -*-
"""风控官（Risk Officer）—— 架构第 3 环，**有否决权**（用户指令 2026-09-20）。

架构：「五分析师 → 牛熊研究员对抗辩论 → **风控官（有否决权）** → 交易员」。

## 为什么需要它（实测缺陷）
`risk_constitution.constitutional_veto` 在设计上是"最后红线"（env 不可覆盖），但**调用点漏传关键实参**：
`mlto/brain.py:1864` 只传了 `account_id/symbol/side/sl_pct`，
**`margin_usd`/`equity_usd` 从未传过** ⇒ 20% 单笔保证金硬顶、日亏损硬停、单币/总敞口上限
**全部空转**，只剩止损幅度校验在跑。也就是说"宪法级风控"过去只兑现了 1/4。

## 本模块做什么
把风控官做成**一个显式角色**（而不是散落的 if）：
  1. **宪法红线**（`risk_constitution`，env 不可覆盖）：传全参调用 —— 止损幅度 / 单笔保证金 /
     日亏损硬停 / 单币与总敞口；
  2. **名义暴露硬顶**：`计划名义 / 净值 > RISK_OFFICER_MAX_NOTIONAL_PCT` ⇒ 否决
     （这一条不依赖杠杆口径，防"杠杆算错导致保证金看着很小"）；
  3. **辩论风险姿态**：辩论**主周期** = reject 且 风险共识 < `RISK_OFFICER_DEBATE_RISK_FLOOR`
     ⇒ 否决（这是"辩论只降 conviction、风控官才拍板"的分工落地）；
  4. **每一次判定都落库** `alpha_analytics.risk_officer_decisions`（输入+逐项检查+结论），
     否决不再只存在于日志里一行 warning。

## 模式差异（**显式**，不静默）
- paper（模拟盘）：`日亏损硬停` 默认**豁免**（`RISK_OFFICER_PAPER_DAILY_LOSS=false`）——
  与 `PB_PAPER_SKIP` 同一立场：纸面亏损是训练数据；且用户 09-11 明确"允许小仓位"。
  豁免会写进 `checks` 的 `skipped_paper`，**可见而非静默**。
- live：全项执行。

## 开关（全部可回滚）
| 开关 | 默认 | 作用 |
|---|---|---|
| `RISK_OFFICER_ENABLED` | true | 总闸；false=整段跳过（记录一条 skipped） |
| `RISK_OFFICER_MAX_NOTIONAL_PCT` | 2.0 | 计划名义/净值硬顶 |
| `RISK_OFFICER_VETO_ON_DEBATE_REJECT` | true | 是否允许辩论姿态触发否决 |
| `RISK_OFFICER_DEBATE_RISK_FLOOR` | 0.5 | 辩论风险共识低于此值才算"风险过高" |
| `RISK_OFFICER_PAPER_DAILY_LOSS` | false | paper 是否执行日亏损硬停 |
| `RISK_OFFICER_DEBATE_MAX_AGE_H` | 3.0 | 辩论姿态的有效期（过期不采用） |
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PRODUCER = "backend/services/risk_officer.py"
TABLE = "risk_officer_decisions"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMP      NOT NULL DEFAULT now(),
    account_id    INTEGER,
    symbol        VARCHAR(24)    NOT NULL,
    side          VARCHAR(8)     NOT NULL,
    tier          VARCHAR(8),
    mode          VARCHAR(8),
    allow         BOOLEAN        NOT NULL,
    reason        VARCHAR(200),
    inputs_json   TEXT,
    checks_json   TEXT,
    debate_json   TEXT,
    producer      VARCHAR(160)
)
"""


def _flag(name: str, default: str) -> bool:
    return (os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:  # noqa: BLE001
        return float(default)


def enabled() -> bool:
    return _flag("RISK_OFFICER_ENABLED", "true")


def ensure_table() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.connection import analytics_engine

        with analytics_engine.begin() as c:
            c.execute(text(_DDL))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RiskOfficer] 建表失败: %s", exc)
        return False


def evaluate_open(
    *,
    account_id: Optional[int],
    symbol: str,
    side: str,
    tier: str = "mid",
    mode: str = "paper",
    equity_usd: float = 0.0,
    planned_notional_usd: float = 0.0,
    leverage: float = 1.0,
    sl_pct: float = 0.0,
    session_id: str = "",
    thesis_id: str = "",
    persist: bool = True,
) -> Dict[str, Any]:
    """开仓前的**权威风控判定**。返回 {allow, reason, checks, inputs, debate}。"""
    sym_u = str(symbol or "").upper()
    side_l = str(side or "").lower()
    mode_l = str(mode or "paper").lower()
    equity = max(0.0, float(equity_usd or 0.0))
    notional = max(0.0, float(planned_notional_usd or 0.0))
    lev = max(1.0, float(leverage or 1.0))
    margin = notional / lev

    checks: List[Dict[str, Any]] = []
    reasons: List[str] = []

    if not enabled():
        checks.append({"name": "enabled", "ok": True, "skipped": "RISK_OFFICER_ENABLED=false"})

    # ── 1) 宪法红线（传全参 —— 这正是过去漏掉的地方）──
    if enabled():
        try:
            from backend.services.risk_constitution import (
                MAX_DAILY_LOSS_PCT_HARD,
                constitutional_veto,
            )

            veto = constitutional_veto(
                account_id=account_id, symbol=sym_u, side=side_l,
                margin_usd=margin, equity_usd=equity, sl_pct=float(sl_pct or 0),
            )
            if veto and str(veto).startswith("daily_loss_hard") and mode_l == "paper" \
                    and not _flag("RISK_OFFICER_PAPER_DAILY_LOSS", "false"):
                # paper 豁免（显式可见，不静默）
                checks.append({"name": "constitution", "ok": True, "value": veto,
                               "skipped_paper": f"日亏损硬停(阈值 {MAX_DAILY_LOSS_PCT_HARD})在模拟盘豁免"
                                                "（RISK_OFFICER_PAPER_DAILY_LOSS=false）"})
            elif veto:
                checks.append({"name": "constitution", "ok": False, "value": veto})
                reasons.append(f"constitution:{veto}")
            else:
                checks.append({"name": "constitution", "ok": True,
                               "value": {"margin_usd": round(margin, 2), "equity_usd": round(equity, 2),
                                         "sl_pct": float(sl_pct or 0)}})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": "constitution", "ok": True, "error": f"{type(exc).__name__}: {str(exc)[:80]}"})

    # ── 2) 名义暴露硬顶（不依赖杠杆口径）──
    if enabled() and equity > 0 and notional > 0:
        pct = notional / equity
        cap = _num("RISK_OFFICER_MAX_NOTIONAL_PCT", 2.0)
        if pct > cap:
            checks.append({"name": "notional_pct", "ok": False,
                           "value": round(pct, 3), "cap": cap})
            reasons.append(f"notional_pct:{pct:.2f}>{cap}")
        else:
            checks.append({"name": "notional_pct", "ok": True, "value": round(pct, 3), "cap": cap})

    # ── 3) 辩论风险姿态（主周期 reject + 风险共识偏低 ⇒ 否决）──
    debate: Optional[Dict[str, Any]] = None
    if enabled() and _flag("RISK_OFFICER_VETO_ON_DEBATE_REJECT", "true"):
        try:
            from backend.services.mlto.brain_debate import debate_context

            debate = debate_context(sym_u, tier,
                                    hours=_num("RISK_OFFICER_DEBATE_MAX_AGE_H", 3.0))
            if debate:
                pv = str(debate.get("primary_verdict") or "")
                rmin = debate.get("risk_min")
                floor = _num("RISK_OFFICER_DEBATE_RISK_FLOOR", 0.5)
                risky = (pv == "reject") and (rmin is not None and float(rmin) < floor)
                checks.append({"name": "debate_posture", "ok": not risky,
                               "value": {"primary_horizon": debate.get("primary_horizon"),
                                         "primary_verdict": pv, "risk_min": rmin,
                                         "horizon_verdicts": debate.get("horizon_verdicts")},
                               "floor": floor})
                if risky:
                    reasons.append(f"debate_reject:{debate.get('primary_horizon')}:risk{rmin}")
            else:
                checks.append({"name": "debate_posture", "ok": True, "skipped": "近期无该标的辩论记录"})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": "debate_posture", "ok": True, "error": f"{type(exc).__name__}: {str(exc)[:80]}"})

    allow = not reasons
    reason = ";".join(reasons)[:200]
    out: Dict[str, Any] = {
        "allow": allow, "reason": reason, "checks": checks,
        "inputs": {"account_id": account_id, "symbol": sym_u, "side": side_l, "tier": tier,
                   "mode": mode_l, "equity_usd": equity, "planned_notional_usd": notional,
                   "leverage": lev, "margin_usd": round(margin, 2), "sl_pct": float(sl_pct or 0),
                   "session_id": session_id, "thesis_id": thesis_id},
        "debate": debate,
    }
    if persist:
        _persist(out)
    if not allow:
        logger.warning("[RiskOfficer] VETO %s %s %s tier=%s: %s（名义 $%.0f/权益 $%.0f 杠杆 %.1fx）",
                       sym_u, side_l, mode_l, tier, reason, notional, equity, lev)
    else:
        logger.debug("[RiskOfficer] ALLOW %s %s notional=%.0f equity=%.0f", sym_u, side_l, notional, equity)
    return out


def _persist(out: Dict[str, Any]) -> None:
    try:
        from sqlalchemy import text

        from backend.database.connection import analytics_engine

        i = out["inputs"]
        if not ensure_table():
            return
        with analytics_engine.begin() as c:
            c.execute(text(
                f"INSERT INTO {TABLE} (account_id, symbol, side, tier, mode, allow, reason, "
                f"inputs_json, checks_json, debate_json, producer) VALUES "
                f"(:acct, :sym, :side, :tier, :mode, :allow, :reason, :inputs, :checks, :debate, :producer)"),
                {"acct": i.get("account_id"), "sym": i.get("symbol"), "side": i.get("side"),
                 "tier": i.get("tier"), "mode": i.get("mode"), "allow": bool(out["allow"]),
                 "reason": out.get("reason") or "", 
                 "inputs": json.dumps(i, ensure_ascii=False, default=str)[:4000],
                 "checks": json.dumps(out.get("checks"), ensure_ascii=False, default=str)[:6000],
                 "debate": json.dumps(out.get("debate"), ensure_ascii=False, default=str)[:2000],
                 "producer": PRODUCER})
    except Exception as exc:  # noqa: BLE001
        logger.debug("[RiskOfficer] 落库失败（不阻塞主链）: %s", exc)


def status(*, hours: float = 24.0) -> Dict[str, Any]:
    """给画布/审计用：近 N 小时判定分布 + 否决原因 Top。"""
    base = {
        "enabled": enabled(),
        "max_notional_pct": _num("RISK_OFFICER_MAX_NOTIONAL_PCT", 2.0),
        "veto_on_debate_reject": _flag("RISK_OFFICER_VETO_ON_DEBATE_REJECT", "true"),
        "debate_risk_floor": _num("RISK_OFFICER_DEBATE_RISK_FLOOR", 0.5),
        "paper_daily_loss": _flag("RISK_OFFICER_PAPER_DAILY_LOSS", "false"),
        "producer": PRODUCER,
    }
    try:
        from sqlalchemy import text

        from backend.database.connection import analytics_engine

        with analytics_engine.connect() as c:
            rows = c.execute(text(
                f"select allow, count(*), max(ts) from {TABLE} "
                f"where ts >= now() - make_interval(secs => :secs) group by allow"),
                {"secs": float(hours) * 3600}).fetchall()
            base["decisions"] = {("allow" if r[0] else "veto"): {"n": int(r[1]), "last": str(r[2])[:19]}
                                 for r in rows}
            top = c.execute(text(
                f"select reason, count(*) from {TABLE} where allow = false and reason <> '' "
                f"and ts >= now() - make_interval(secs => :secs) group by reason order by 2 desc limit 8"),
                {"secs": float(hours) * 3600}).fetchall()
            base["veto_reasons_top"] = [{"reason": str(r[0])[:120], "n": int(r[1])} for r in top]
    except Exception as exc:  # noqa: BLE001
        base["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return base


def daily_report(*, hours: float = 24.0) -> Dict[str, Any]:
    """风控官日报（供画布/报表）：判定数、否决率、被否决的标的分布。"""
    st = status(hours=hours)
    dec = st.get("decisions") or {}
    n_allow = int((dec.get("allow") or {}).get("n") or 0)
    n_veto = int((dec.get("veto") or {}).get("n") or 0)
    total = n_allow + n_veto
    return {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_hours": hours,
        "total": total,
        "allow": n_allow,
        "veto": n_veto,
        "veto_rate": round(n_veto / total, 3) if total else None,
        "top_reasons": st.get("veto_reasons_top") or [],
        "config": {k: v for k, v in st.items() if k not in ("decisions", "veto_reasons_top", "error")},
    }
