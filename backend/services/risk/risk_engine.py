# -*- coding: utf-8 -*-
"""RiskEngine 单入口（v3 方向 7）。

所有开仓/加仓订单在真正提交前必经 `pre_trade()`：
  paper  → paper_trading_engine.place_order（TradeGate 之后、旧引擎层校验之前）
  live   → trading_commands 三个开仓分支（HL buy/sell、CCXT buy/sell）
  arb    → arbitrage/live_executor 三个实盘执行入口

检查顺序（先便宜、先全局，后昂贵）：
  1. LIVE_KILL_SWITCH（env/文件/API 三入口）—— 实盘开仓全拒；scope=all 时 paper 同拒
  2. TradingState：HALTED / REDUCING → 拒开仓；ACTIVE 放行
  3. 连通性熔断：该 venue 连续失败 ≥ N → 拒开仓
  4. 事件避险窗口（no_open_windows，Phase 2 事件策略写入）
  5. 日开仓配额（单一来源 runtime_tuning：scalp / trend / total / live）
  6. 组合回撤缩放：只给出 position_scale（1.0/0.5），不拒单；0.0 的情况已由 REDUCING 覆盖

平仓/减仓永远放行（降风险动作不被风控拦截）——调用方通过 `is_open=False` 声明。

`tick()` 由调度器每 60s 调用：评估闪崩（BTC 1h/4h）、组合回撤（默认账本账户）、过期状态回收，
写 TradingState 并发 P0/P1 告警。所有评估失败都记录但不改变状态（fail-safe：不会因为数据缺失
而误停机，也不会因为异常而误放行——放行判定全部基于已落盘的状态）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.services.risk.trading_state import TradingState, get_state_store
from backend.services.risk.kill_switch import kill_switch_status
from backend.services.risk.circuit_breakers import (
    get_connectivity_breaker, evaluate_flash_crash, flash_crash_thresholds, drawdown_tier,
)
from backend.services.risk import daily_quota

logger = logging.getLogger(__name__)

LIVE_VENUES = {"binance", "asterdex", "hyperliquid", "okx", "bybit", "gate", "bitget"}


def _env_true(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class PreTradeRequest:
    account_id: int
    symbol: str
    side: str                       # buy/sell/long/short
    is_open: bool                   # True=开仓/加仓；False=平仓/减仓（永远放行）
    venue: str = "paper"            # paper | binance | asterdex | hyperliquid | ...
    tier: Optional[str] = None      # short/mid/long
    trade_nature: Optional[str] = None  # scalp/swing/trend_follow
    notional: float = 0.0
    equity: float = 0.0
    leverage: float = 1.0
    session_id: Optional[str] = None
    source: str = ""                # 调用方标签（scalp_loop / master / live_ccxt ...）

    @property
    def is_live(self) -> bool:
        return str(self.venue or "").lower() in LIVE_VENUES


@dataclass
class RiskVerdict:
    allowed: bool
    reason_code: str = ""
    reason: str = ""
    state: str = TradingState.ACTIVE.value
    position_scale: float = 1.0
    max_leverage: Optional[float] = None
    checks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed, "reason_code": self.reason_code, "reason": self.reason,
            "state": self.state, "position_scale": self.position_scale,
            "max_leverage": self.max_leverage, "checks": self.checks,
        }

    def as_block_result(self, layer: str = "risk_engine") -> Dict[str, Any]:
        """与 paper_engine.place_order 既有拦截返回结构对齐。"""
        return {
            "success": False, "blocked": True,
            "blocked_layer": layer, "blocked_by": self.reason_code,
            "reason": self.reason, "reason_code": self.reason_code,
            "risk_engine": self.to_dict(),
        }


class RiskEngine:
    """单例。`enabled` 由 RISK_ENGINE_V3_ENABLED 控制（默认开）；关闭时 pre_trade 只记录不拦截。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.counters: Dict[str, int] = {"allowed": 0, "blocked": 0, "errors": 0, "ticks": 0}
        self.last_block: Optional[Dict[str, Any]] = None
        self.last_tick: Dict[str, Any] = {}
        self._peaks: Dict[int, float] = {}   # account_id → 本进程观察到的权益峰值（回撤分级用）

    @property
    def enabled(self) -> bool:
        return _env_true("RISK_ENGINE_V3_ENABLED", True)

    # ───────────────────────────── pre_trade ─────────────────────────────
    def pre_trade(self, db, req: PreTradeRequest) -> RiskVerdict:
        store = get_state_store()
        try:
            snap = store.snapshot()
        except Exception as exc:  # 状态不可读 → 视为 ACTIVE 但记录错误
            logger.error("[RiskEngine] 读取状态失败: %s", exc)
            snap = None
        state = TradingState.parse(snap.state) if snap else TradingState.ACTIVE
        verdict = RiskVerdict(True, state=state.value,
                              position_scale=float(snap.position_scale) if snap else 1.0,
                              max_leverage=(snap.max_leverage if snap else None))

        # 平仓/减仓永远放行
        if not req.is_open:
            verdict.checks.append({"check": "reduce_bypass", "ok": True})
            return self._finish(verdict, req)

        # 1. kill switch
        try:
            ks = kill_switch_status()
            blocked = ks.blocks(live=req.is_live)
            verdict.checks.append({"check": "kill_switch", "ok": not blocked, "engaged": ks.engaged, "scope": ks.scope})
            if blocked:
                return self._deny(verdict, req, "kill_switch", f"LIVE_KILL_SWITCH 生效({ks.source}): {ks.reason}")
        except Exception as exc:
            self._err("kill_switch", exc)

        # 2. trading state
        if state != TradingState.ACTIVE:
            verdict.checks.append({"check": "trading_state", "ok": False, "state": state.value})
            return self._deny(verdict, req, f"trading_state_{state.value}",
                              f"TradingState={state.value} ({snap.source}: {snap.reason})")
        verdict.checks.append({"check": "trading_state", "ok": True, "state": state.value})

        # 3. 连通性
        try:
            if req.is_live and get_connectivity_breaker().is_tripped(req.venue):
                verdict.checks.append({"check": "connectivity", "ok": False, "venue": req.venue})
                return self._deny(verdict, req, "connectivity_tripped", f"venue={req.venue} 连续失败熔断，禁止新开")
            verdict.checks.append({"check": "connectivity", "ok": True})
        except Exception as exc:
            self._err("connectivity", exc)

        # 4. 事件避险窗口
        try:
            w = store.in_no_open_window(req.symbol)
            if w:
                verdict.checks.append({"check": "no_open_window", "ok": False, "window": w})
                return self._deny(verdict, req, "risk_window", f"避险窗口: {w.get('reason')}")
            verdict.checks.append({"check": "no_open_window", "ok": True})
        except Exception as exc:
            self._err("no_open_window", exc)

        # 4b. 事件总线避险窗口（market_events：下架 / 监控标签 / 清算级联 / 黑天鹅级全市场级联）
        try:
            from backend.services.risk import event_windows
            hit = event_windows.check(req.symbol, req.side)
            if hit:
                verdict.checks.append({"check": "event_window", "ok": False, **hit})
                return self._deny(verdict, req, "event_window",
                                  f"事件避险窗口[{hit.get('rule')}]: {hit.get('title')}")
            verdict.checks.append({"check": "event_window", "ok": True})
        except Exception as exc:
            self._err("event_window", exc)

        # 5. 日开仓配额（单一来源）
        # [2026-09-03 v3 方向1] E1 趋势引擎日任务（source=trend_e1:*）：规则驱动、日线级、最多 8 币/日，
        # 不占 LLM/短线的日开仓配额（配额是治"高频乱开"的，不是治趋势 sleeve 的）；其余检查照常。
        _e1_source = str(req.source or "").lower().startswith("trend_e1")
        if _e1_source:
            verdict.checks.append({"check": "daily_quota", "ok": True, "exempt": "trend_e1"})
        elif db is not None and _env_true("RISK_DAILY_QUOTA_ENABLED", True):
            try:
                q = daily_quota.check(db, req.account_id, tier=req.tier, trade_nature=req.trade_nature, live=req.is_live)
                verdict.checks.append({"check": "daily_quota", "ok": q.allowed, **q.to_dict()})
                if not q.allowed:
                    return self._deny(verdict, req, "daily_quota", q.reason)
            except Exception as exc:
                self._err("daily_quota", exc)
                try:
                    db.rollback()
                except Exception:
                    pass

        # 6. 回撤缩放（信息，不拒单）
        if req.equity and req.equity > 0:
            try:
                peak = self._observe_peak(req.account_id, float(req.equity))
                dd = drawdown_tier(float(req.equity), peak)
                verdict.position_scale = min(verdict.position_scale, dd.scale) if dd.scale > 0 else verdict.position_scale
                verdict.checks.append({"check": "drawdown_scale", "ok": True, **dd.to_dict()})
            except Exception as exc:
                self._err("drawdown_scale", exc)

        return self._finish(verdict, req)

    # ───────────────────────────── tick ─────────────────────────────
    def tick(self) -> Dict[str, Any]:
        """周期巡检（60s）。返回本次评估摘要，供 job_registry 记录。"""
        out: Dict[str, Any] = {"ts": time.time()}
        store = get_state_store()
        snap = store.snapshot()   # 触发 TTL 回收
        out["state_before"] = snap.state

        # kill switch 状态（文件入口可能被外部写入，同步到状态机）
        try:
            ks = kill_switch_status()
            out["kill_switch"] = ks.to_dict()
            if ks.engaged and TradingState.parse(snap.state) != TradingState.HALTED:
                store.set_state(TradingState.HALTED, reason=f"kill_switch({ks.source}): {ks.reason}", source="kill_switch")
        except Exception as exc:
            self._err("tick.kill_switch", exc)

        # 闪崩
        try:
            fc = evaluate_flash_crash()
            out["flash_crash"] = fc.to_dict()
            if fc.triggered:
                ttl_h = flash_crash_thresholds()["ttl_hours"]
                cur = store.snapshot()
                if TradingState.parse(cur.state) == TradingState.ACTIVE or cur.source != "flash_crash":
                    store.set_state(TradingState.REDUCING, reason=fc.reason, source="flash_crash",
                                    ttl_seconds=ttl_h * 3600.0, escalate_only=True)
                    self._alert(f"📉 闪崩熔断触发 → REDUCING {ttl_h:.0f}h\n{fc.reason}\nBTC={fc.last_price}", "critical")
        except Exception as exc:
            self._err("tick.flash_crash", exc)

        # 组合回撤（默认账本账户）
        try:
            out["drawdown"] = self._tick_drawdown(store)
        except Exception as exc:
            self._err("tick.drawdown", exc)

        out["connectivity"] = get_connectivity_breaker().status()
        out["state_after"] = store.snapshot().state
        with self._lock:
            self.counters["ticks"] += 1
            self.last_tick = out
        return out

    def _tick_drawdown(self, store) -> Dict[str, Any]:
        """用 paper_balances 权益 vs 本进程/会话峰值做组合回撤分级。"""
        from backend.database.connection import SessionLocal
        from backend.core.tenant import set_system_identity
        from sqlalchemy import text
        set_system_identity()
        results: Dict[str, Any] = {}
        db = SessionLocal()
        try:
            accounts = self._ledger_accounts(db)
            worst_scale = 1.0
            worst_dd = None
            for aid in accounts:
                row = db.execute(text("SELECT total_equity FROM paper_balances WHERE account_id = :a"), {"a": aid}).fetchone()
                if not row or row[0] is None:
                    continue
                equity = float(row[0])
                peak = self._session_peak(db, aid) or self._observe_peak(aid, equity)
                peak = max(peak, self._observe_peak(aid, equity))
                dd = drawdown_tier(equity, peak)
                results[str(aid)] = {"equity": round(equity, 2), "peak": round(peak, 2), **dd.to_dict()}
                if dd.scale < worst_scale:
                    worst_scale = dd.scale
                    worst_dd = dd
            if worst_dd is not None and worst_dd.reducing:
                store.set_state(TradingState.REDUCING, reason=f"组合回撤 {worst_dd.drawdown_pct:.1f}% ≥ {worst_dd.reducing_pct:.0f}%",
                                source="drawdown", ttl_seconds=24 * 3600.0, escalate_only=True, position_scale=0.0)
                self._alert(f"🩸 组合回撤 {worst_dd.drawdown_pct:.1f}% ≥ {worst_dd.reducing_pct:.0f}% → REDUCING（只平不开）", "critical")
            else:
                cur = store.snapshot()
                if abs(float(cur.position_scale) - worst_scale) > 1e-9 and TradingState.parse(cur.state) == TradingState.ACTIVE:
                    store.set_position_scale(worst_scale, reason=f"drawdown tier {worst_dd.label if worst_dd else 'normal'}")
                    if worst_scale < 1.0:
                        self._alert(f"⚠️ 组合回撤进入减半档（scale={worst_scale}）", "warning")
        finally:
            db.close()
        return results

    @staticmethod
    def _ledger_accounts(db) -> List[int]:
        """回撤巡检覆盖的账户：EDGE_LEDGER_ACCOUNTS；缺省=全部有 paper_balances 的账户。"""
        try:
            from backend.services.ledger.edge_ledger import default_ledger_accounts
            accts = default_ledger_accounts()
            if accts:
                return [int(a) for a in accts]
        except Exception:
            pass
        try:
            from sqlalchemy import text
            return [int(r[0]) for r in db.execute(text("SELECT account_id FROM paper_balances")).fetchall()]
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            return []

    @staticmethod
    def _session_peak(db, account_id: int) -> Optional[float]:
        """优先用 full_auto_sessions.peak_balance（重置脚本会同步清零）。"""
        try:
            from sqlalchemy import text
            row = db.execute(
                text("SELECT max(peak_balance) FROM full_auto_sessions WHERE account_id = :a AND status IN ('running','defensive','paused')"),
                {"a": int(account_id)},
            ).fetchone()
            if row and row[0] is not None and float(row[0]) > 0:
                return float(row[0])
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        return None

    def _observe_peak(self, account_id: int, equity: float) -> float:
        with self._lock:
            p = self._peaks.get(int(account_id), 0.0)
            if equity > p:
                p = equity
                self._peaks[int(account_id)] = p
            return p

    def reset_peak(self, account_id: int) -> None:
        with self._lock:
            self._peaks.pop(int(account_id), None)

    # ───────────────────────────── status ─────────────────────────────
    def status(self, db=None, account_id: Optional[int] = None) -> Dict[str, Any]:
        store = get_state_store()
        snap = store.snapshot()
        out: Dict[str, Any] = {
            "enabled": self.enabled,
            "trading_state": snap.to_dict(),
            "kill_switch": kill_switch_status().to_dict(),
            "connectivity": get_connectivity_breaker().status(),
            "flash_crash_thresholds": flash_crash_thresholds(),
            "counters": dict(self.counters),
            "last_block": self.last_block,
            "last_tick": self.last_tick,
            "quota_caps": {},
        }
        try:
            out["quota_caps"] = daily_quota.all_caps()
            if db is not None and account_id:
                out["quota"] = daily_quota.status(db, int(account_id))
        except Exception as exc:
            out["quota_error"] = str(exc)
        try:
            from backend.services.risk import event_windows
            out["event_windows"] = event_windows.status()
        except Exception as exc:
            out["event_windows"] = {"error": str(exc)[:160]}
        return out

    # ───────────────────────────── 内部 ─────────────────────────────
    def _deny(self, verdict: RiskVerdict, req: PreTradeRequest, code: str, reason: str) -> RiskVerdict:
        verdict.allowed = False
        verdict.reason_code = code
        verdict.reason = reason
        if not self.enabled:
            # 关闭状态：只记录，不拦截
            logger.warning("[RiskEngine][disabled] 本应拦截 %s %s %s: %s", req.venue, req.symbol, req.side, reason)
            verdict.allowed = True
            verdict.reason_code = f"observed:{code}"
            return self._finish(verdict, req)
        with self._lock:
            self.counters["blocked"] += 1
            self.last_block = {
                "ts": time.time(), "account_id": req.account_id, "symbol": req.symbol, "side": req.side,
                "venue": req.venue, "tier": req.tier, "nature": req.trade_nature, "code": code, "reason": reason,
                "source": req.source,
            }
        logger.warning("[RiskEngine] BLOCK %s acct=%s %s %s tier=%s/%s: %s",
                       req.venue, req.account_id, req.symbol, req.side, req.tier, req.trade_nature, reason)
        return verdict

    def _finish(self, verdict: RiskVerdict, req: PreTradeRequest) -> RiskVerdict:
        if verdict.allowed:
            with self._lock:
                self.counters["allowed"] += 1
        return verdict

    def _err(self, where: str, exc: Exception) -> None:
        with self._lock:
            self.counters["errors"] += 1
        logger.warning("[RiskEngine] %s 检查异常（该项跳过）: %s", where, exc)

    @staticmethod
    def _alert(text: str, level: str) -> None:
        """统一告警出口：critical→P0（全通道），warning→P1，其余→P2。"""
        try:
            from backend.services.ops.alerts import send_alert
            lvl = {"critical": "P0", "warning": "P1"}.get(str(level).lower(), "P2")
            send_alert(lvl, "RiskEngine", text, dedupe_key=f"risk_engine:{text[:40]}", source="risk_engine")
        except Exception as exc:  # pragma: no cover
            logger.debug("[RiskEngine] 告警失败: %s", exc)


_engine: Optional[RiskEngine] = None
_engine_lock = threading.Lock()


def get_risk_engine_v3() -> RiskEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = RiskEngine()
    return _engine


def pre_trade(db, req: PreTradeRequest) -> RiskVerdict:
    """模块级快捷入口。"""
    return get_risk_engine_v3().pre_trade(db, req)


def run_tick_job() -> Dict[str, Any]:
    """调度器入口（v3_jobs_ext 注册，60s）。"""
    return get_risk_engine_v3().tick()
