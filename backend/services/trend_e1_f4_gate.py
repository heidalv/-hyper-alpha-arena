# -*- coding: utf-8 -*-
"""E1 → Aster 小额实盘 F4 门禁检查单（v3 p3-promotion / v2 F4）。

验收项（全部通过才允许 TREND_E1_LIVE_ASTER=true 生效）：
  1. 模拟跑满 ≥ 4 周（或 TREND_E1_F4_MIN_DAYS）
  2. trend_drift = 0 且 leverage_violations = 0，权重偏差 ≤ TREND_E1_F4_MAX_DRIFT
  3. 净费 / 权益 ≤ 阈值（默认 2bp/日均近似用周费用帽）
  4. 停机/熔断单测最近一次绿（读落盘或 env 声明）
  5. promotion_freeze 未激活
  6. LIVE_KILL_SWITCH 未激活

本模块**只产出 go/no-go**；真正对 Aster 下单仍要 TREND_E1_LIVE_ASTER=true
且账户在 TREND_E1_ACCOUNT_IDS——默认全关。

[§51 修复 2026-09-10] 三点口径修正（此前文档与代码不一致，属静默失效）：
  1. 账户键名是 `TREND_E1_ACCOUNT_IDS`（本文档早先误写 `TREND_E1_LIVE_ACCOUNT_IDS`，
     该键在 env_registry / .env / 代码里**都不存在**，照着旧文档配置会静默落空）；
  2. `assert_live_allowed()` 是**执行前**门禁的统一入口，由 `trend_e1_engine.scheduled_job()`
     在 `run_daily()` **之前**调用（修复前它在执行之后才评估、且该函数零调用点）；
  3. 当前构建**没有 E1 实盘下单路径**（`paper_engine.place_order` 仅落模拟成交），
     见 `trend_e1_engine.E1_LIVE_ROUTING_IMPLEMENTED`。因此 `TREND_E1_LIVE_ASTER=true`
     不会带来实盘，只能表达意图——这一点由 `_pre_exec_live_gate()` 显式告警（不再静默无效）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "trend_drift"
GATE_PATH = Path(__file__).resolve().parents[1] / "data" / "trend_e1" / "f4_gate_latest.json"


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def live_aster_requested() -> bool:
    return _env_true("TREND_E1_LIVE_ASTER", False)


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _check_drift() -> Dict[str, Any]:
    """E1 漂移验收（compute_trend_drift 落盘的 trend_drift/latest.json）。

    [2026-09-04 修正] 该文件里 trend_drift / weight_drift / leverage_violations 都是
    **仓位计数**而非比例，早先版本拿 trend_drift 直接和 TREND_E1_F4_MAX_DRIFT(0.02)
    比较属于量纲错误，永远判不出真实情况。正确读法：
      trend_drift          方向不符的仓位数（missing+extra+wrong_side）——E1 验收要求 = 0
      leverage_violations  杠杆超限的仓位数——小额实盘前必须为 0
      matched[].deviation  单仓权重偏差（实际-目标）比例——这才是和 MAX_DRIFT 比的量
    """
    path = DATA_DIR / "latest.json"
    if not path.exists():
        return {"ok": False, "reason": "trend_drift/latest.json 不存在"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": f"drift 读失败: {exc}"}

    # 兼容两种形状：扁平（latest.json）/ 嵌套（e1_last_run 的 accounts.<id>.drift）
    node = data.get("drift") if isinstance(data.get("drift"), dict) else data
    trend_n = _as_int(node.get("trend_drift"))
    if trend_n is None:
        return {"ok": False, "reason": "trend_drift 字段缺失",
                "raw_keys": list(node.keys())[:20]}
    weight_n = _as_int(node.get("weight_drift")) or 0
    lev_n = _as_int(node.get("leverage_violations")) or 0

    # 权重偏差比例取所有匹配仓位的最大绝对值
    max_dev: Optional[float] = None
    for m in (data.get("matched") or []):
        try:
            dev = abs(float(m.get("deviation")))
        except (TypeError, ValueError):
            continue
        max_dev = dev if max_dev is None else max(max_dev, dev)

    # latest.json 只留最后一个账户；多账户时从 e1_last_run 聚合取最差，避免漏检
    accounts_checked = 1
    try:
        run = json.loads((DATA_DIR / "e1_last_run.json").read_text(encoding="utf-8"))
        accounts = run.get("accounts") or {}
        if accounts:
            accounts_checked = len(accounts)
            for _acct, payload in accounts.items():
                d = (payload or {}).get("drift") or {}
                trend_n = max(trend_n, _as_int(d.get("trend_drift")) or 0)
                weight_n = max(weight_n, _as_int(d.get("weight_drift")) or 0)
                lev_n = max(lev_n, _as_int(d.get("leverage_violations")) or 0)
    except Exception:
        pass

    max_ok = _env_float("TREND_E1_F4_MAX_DRIFT", 0.02)
    fails: List[str] = []
    if trend_n != 0:
        fails.append(f"trend_drift={trend_n}（方向不符仓位数，要求 0）")
    if lev_n != 0:
        fails.append(f"leverage_violations={lev_n}（要求 0）")
    if max_dev is not None and max_dev > max_ok:
        fails.append(f"max|权重偏差|={max_dev:.4f} > {max_ok}")
    return {
        "ok": not fails,
        "trend_drift": trend_n,
        "weight_drift": weight_n,
        "leverage_violations": lev_n,
        "max_weight_deviation": round(max_dev, 4) if max_dev is not None else None,
        "max_ok": max_ok,
        "accounts_checked": accounts_checked,
        "as_of_bar": node.get("as_of_bar") or data.get("as_of_bar"),
        "reason": None if not fails else "；".join(fails),
    }


def _check_run_days() -> Dict[str, Any]:
    """用 e1_runs.jsonl 或 e1_last_run 估模拟天数。"""
    min_days = int(_env_float("TREND_E1_F4_MIN_DAYS", 28))
    runs_path = DATA_DIR / "e1_runs.jsonl"
    days = 0.0
    if runs_path.exists():
        try:
            lines = [ln for ln in runs_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if len(lines) >= 2:
                first = json.loads(lines[0])
                last = json.loads(lines[-1])
                t0 = float(first.get("ts_ms") or first.get("ts") or 0)
                t1 = float(last.get("ts_ms") or last.get("ts") or 0)
                if t0 > 1e12:
                    t0, t1 = t0 / 1000.0, t1 / 1000.0
                if t1 > t0 > 0:
                    days = (t1 - t0) / 86400.0
            elif lines:
                days = 1.0
        except Exception:
            days = 0.0
    # 也可用 history.jsonl
    if days <= 0:
        hist = DATA_DIR / "history.jsonl"
        if hist.exists():
            try:
                lines = [ln for ln in hist.read_text(encoding="utf-8").splitlines() if ln.strip()]
                days = float(len(lines))  # 粗估：每日一条
            except Exception:
                pass
    ok = days >= min_days
    return {"ok": ok, "days": round(days, 1), "min_days": min_days,
            "reason": None if ok else f"模拟天数 {days:.1f} < {min_days}"}


def _check_fee_budget() -> Dict[str, Any]:
    """费用帽：读 edge_ledger 周费用/权益；失败则记 skip（不假装通过）。"""
    max_fee_pct = _env_float("TREND_E1_F4_MAX_FEE_PCT_WEEK", 0.01)  # 1%/周
    try:
        from backend.services.analysis import ledgers
        # 若有费用汇总接口则用；否则跳过为 inconclusive
        if hasattr(ledgers, "fee_summary"):
            s = ledgers.fee_summary(days=7)
            fee = float(s.get("fee_usd") or 0)
            eq = float(s.get("equity_usd") or 1)
            pct = fee / eq if eq > 0 else 1.0
            ok = pct <= max_fee_pct
            return {"ok": ok, "fee_pct_week": round(pct, 6), "max": max_fee_pct,
                    "reason": None if ok else f"周费用占比 {pct:.4%} > {max_fee_pct:.2%}"}
    except Exception as exc:
        return {"ok": False, "inconclusive": True, "reason": f"费用账本不可用: {exc}"}
    return {"ok": False, "inconclusive": True, "reason": "无 fee_summary；请补 edge_ledger 或设 TREND_E1_F4_SKIP_FEE=true"}


def _check_freeze_and_kill() -> List[Dict[str, Any]]:
    checks = []
    try:
        from backend.services.allocation.capital_allocator import promotion_frozen
        fr = promotion_frozen()
        checks.append({
            "name": "promotion_freeze",
            "ok": not fr.get("frozen"),
            "reason": None if not fr.get("frozen") else "黑天鹅晋升冻结中",
            "detail": fr,
        })
    except Exception as exc:
        checks.append({"name": "promotion_freeze", "ok": False, "reason": str(exc)[:120]})
    try:
        from backend.services.risk.kill_switch import kill_switch_status
        ks = kill_switch_status()
        engaged = bool(getattr(ks, "engaged", False))
        checks.append({
            "name": "kill_switch",
            "ok": not engaged,
            "reason": None if not engaged else f"KILL_SWITCH: {getattr(ks, 'reason', '')}",
        })
    except Exception as exc:
        checks.append({"name": "kill_switch", "ok": False, "reason": str(exc)[:120]})
    return checks


def evaluate_f4_gate(*, persist: bool = True) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    d = _check_drift()
    checks.append({"name": "trend_drift", **d})
    days = _check_run_days()
    checks.append({"name": "run_days", **days})
    if _env_true("TREND_E1_F4_SKIP_FEE", False):
        checks.append({"name": "fee_budget", "ok": True, "skipped": True, "reason": "TREND_E1_F4_SKIP_FEE=true"})
    else:
        fee = _check_fee_budget()
        # inconclusive 不算通过
        if fee.get("inconclusive") and not fee.get("ok"):
            fee = {**fee, "ok": False}
        checks.append({"name": "fee_budget", **fee})
    checks.extend(_check_freeze_and_kill())

    # 停机单测：环境声明或落盘
    if _env_true("TREND_E1_F4_HALT_TESTS_OK", False):
        checks.append({"name": "halt_tests", "ok": True, "reason": "env 声明已通过"})
    else:
        halt_path = Path(__file__).resolve().parents[1] / "data" / "risk" / "halt_tests_ok.json"
        if halt_path.exists():
            checks.append({"name": "halt_tests", "ok": True, "reason": "halt_tests_ok.json 存在"})
        else:
            checks.append({
                "name": "halt_tests", "ok": False,
                "reason": "缺少 halt_tests_ok.json；跑停机单测后 touch 或设 TREND_E1_F4_HALT_TESTS_OK=true",
            })

    all_ok = all(bool(c.get("ok")) for c in checks)
    out = {
        "ts_ms": int(time.time() * 1000),
        "passed": all_ok,
        "live_aster_requested": live_aster_requested(),
        "live_allowed": all_ok and live_aster_requested(),
        "checks": checks,
        "note": "passed 只是资格；真下单还要 TREND_E1_LIVE_ASTER=true 与 live 账户列表",
    }
    if persist:
        try:
            GATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            GATE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                                 encoding="utf-8")
        except Exception as exc:
            logger.warning("[f4_gate] 落盘失败: %s", exc)
    return out


def assert_live_allowed() -> Dict[str, Any]:
    """live 意图的唯一 go/no-go 入口：未过门 → 拒（返回 allowed=False）。

    调用方契约（`trend_e1_engine._pre_exec_live_gate()`）：
      - 必须在**执行之前**调用；
      - 本函数抛异常时必须按「未过门」处理（fail-closed），不得静默放行；
      - `allowed=False` 时调用方只能保持 paper，不得据此开启任何实盘通道。
    """
    gate = evaluate_f4_gate(persist=True)
    if not gate.get("live_allowed"):
        return {"allowed": False, "gate": gate}
    return {"allowed": True, "gate": gate}
