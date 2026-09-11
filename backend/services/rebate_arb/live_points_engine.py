# -*- coding: utf-8 -*-
"""LivePointsEngine — Asterdex 实盘成交顺路吃积分的逐笔计量引擎。

设计见 docs/ASTERDEX_LIVE_POINTS_DESIGN.md。
- 只计量「本来就要成交」的实盘单，绝不为刷分而交易（wash 由官方取消资格）；
- 积分公式单一来源：rule_registry.STAGE6_POINT_MODEL；
- 不触碰 rule_sync_gate / S8 自动开仓策略（rebate 域暂停语义不变）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 持仓积分的内存登记（open 时记，close 时结算小时数）；进程重启后未结算的
# open 事件以账本 close 缺失容忍（只影响持仓积分估算，不影响官方真实积分）。
_open_registry: Dict[str, Dict[str, Any]] = {}
_registry_lock = threading.Lock()


def _model() -> Dict[str, Any]:
    from backend.services.rebate_arb.rule_registry import STAGE6_POINT_MODEL
    return STAGE6_POINT_MODEL


def _point_value_usd() -> float:
    """单位积分估值（USD）。

    [2026-09-03] Stage 6 已结束且无后继赛季（rule_registry.STAGE6_POINT_MODEL
    "active": False）→ 积分不再有任何兑换价值，估值必须归零，避免前端继续
    展示"投机性收益"误导用户。赛季重新开启时把 active 置回 True 即可恢复。
    """
    m = _model()
    if not bool(m.get("active", True)):
        return 0.0
    pv = m.get("point_valuation") or {}
    return float(pv.get("usd_per_point_estimate", 0.01)) * float(pv.get("speculative_discount", 0.5))


# 默认对哪些周期的「开仓」做 maker 优先：中长线容忍 30s 级成交延迟；
# 短线(short/scalp) 的入场时效敏感，错过入场的机会成本远大于 0.04% 手续费。
_DEFAULT_MAKER_FIRST_TIERS = ("mid", "long")


class LivePointsEngine:
    """Asterdex 实盘积分计量（单例）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._policy_cache: Dict[str, Any] = {"ts": 0.0, "policy": None}

    # ── 积分开关策略（交易所配置里的专门开关） ──────────────

    def get_policy(self, account_id: Optional[int] = None) -> Dict[str, Any]:
        """读取 asterdex 凭证上的积分开关与执行策略（60s 缓存）。

        返回:
          {"enabled": bool, "maker_first": bool, "maker_timeout_s": float,
           "asset_points_enabled": bool, "reason": str}
        开关关闭时 record_fill/record_close 静默跳过，绝不计量。
        """
        now = time.time()
        if now - float(self._policy_cache.get("ts") or 0) < 60.0:
            return dict(self._policy_cache.get("policy") or self._default_policy())
        policy = self._default_policy()
        try:
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal
            from backend.database.models import ExchangeCredential
            from sqlalchemy import text as _sa_text

            with system_identity(), SessionLocal() as db:
                cred = db.query(ExchangeCredential).filter(
                    ExchangeCredential.exchange == "asterdex",
                    ExchangeCredential.enabled == True,  # noqa: E712
                ).first()
                if cred is None:
                    policy["reason"] = "no_asterdex_credential"
                else:
                    enabled = bool(getattr(cred, "points_enabled", False))
                    cfg = getattr(cred, "points_config", None) or {}
                    # [2026-09-03] maker 优先只对哪些周期的开仓生效（默认中长线）；
                    # 交易所配置页可通过 points_config.maker_first_tiers 覆盖。
                    _tiers_raw = cfg.get("maker_first_tiers")
                    if isinstance(_tiers_raw, (list, tuple)) and _tiers_raw:
                        _tiers = tuple(str(t).strip().lower() for t in _tiers_raw if str(t).strip())
                    else:
                        _tiers = _DEFAULT_MAKER_FIRST_TIERS
                    policy = {
                        "enabled": enabled,
                        "maker_first": bool(cfg.get("maker_first", True)) if enabled else False,
                        "maker_timeout_s": float(cfg.get("maker_timeout_s", 30.0) or 30.0),
                        "maker_first_tiers": list(_tiers),
                        "asset_points_enabled": bool(cfg.get("asset_points_enabled", False)) if enabled else False,
                        "account_id": cred.account_id,
                        "reason": "ok" if enabled else "points_disabled_in_exchange_config",
                    }
        except Exception as e:
            policy["reason"] = f"policy_read_failed: {str(e)[:80]}"
        self._policy_cache = {"ts": now, "policy": dict(policy)}
        return dict(policy)

    @staticmethod
    def _default_policy() -> Dict[str, Any]:
        return {
            "enabled": False,
            "maker_first": False,
            "maker_timeout_s": 30.0,
            "maker_first_tiers": list(_DEFAULT_MAKER_FIRST_TIERS),
            "asset_points_enabled": False,
            "account_id": None,
            "reason": "points_disabled_in_exchange_config",
        }

    def invalidate_policy_cache(self) -> None:
        """交易所配置保存后立刻失效缓存（否则最长 60s 才生效）。"""
        self._policy_cache = {"ts": 0.0, "policy": None}

    def points_enabled(self) -> bool:
        return bool(self.get_policy().get("enabled"))

    # ── 正常实盘的 maker 优先门控 ───────────────────────────

    def maker_first_for(
        self,
        exchange: str,
        *,
        timeframe_tier: Optional[str],
        is_open: bool,
        policy: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """正常实盘（AI 策略）下单是否走 maker 优先，返回 (bool, timeout_s)。

        [2026-09-03 费率优化] Aster USDT 永续 Maker 0% / Taker 0.04%。
        只有同时满足以下条件才用挂单优先（其余一律原样市价，不改变任何交易意图）：
          1. 交易所是 asterdex（币安等不在此优化范围）
          2. 是开仓/加仓（reduce_only 的平仓、止损绝不等挂单——时效优先）
          3. 周期属于 policy.maker_first_tiers（默认 mid/long；short 时效敏感不做）
          4. 交易所配置页开关 enabled 且 maker_first 为真
        超时/未成交由 place_order_maker_first 自动撤单回退市价，故最坏情况
        只是多等 timeout_s 秒，不会漏单。
        """
        if str(exchange or "").lower() != "asterdex" or not is_open:
            return False, 0.0
        pol = policy if policy is not None else self.get_policy()
        if not (pol.get("enabled") and pol.get("maker_first")):
            return False, 0.0
        tier = str(timeframe_tier or "mid").strip().lower()
        tiers = [str(t).lower() for t in (pol.get("maker_first_tiers") or _DEFAULT_MAKER_FIRST_TIERS)]
        if tier not in tiers:
            return False, 0.0
        timeout = float(pol.get("maker_timeout_s") or 30.0)
        return True, max(5.0, min(timeout, 300.0))

    # ── 估算函数 ───────────────────────────────────────

    def est_trade_points(self, notional_usd: float, maker: bool) -> tuple:
        """交易积分估算：(fee_usd, trade_points)。

        fee_schedule: USDT 永续 maker 0 / taker 0.0004。
        trading: fee_usd × 100 + maker 名义($1k) × 1.0。
        """
        m = _model()
        fees = m.get("fee_schedule") or {}
        usdt = fees.get("usdt_perp") or {"maker": 0.0, "taker": 0.0004}
        fee_rate = float(usdt.get("maker", 0.0)) if maker else float(usdt.get("taker", 0.0004))
        fee_usd = notional_usd * fee_rate
        trading = m.get("trading") or {}
        pts = fee_usd * float(trading.get("points_per_usd_fee", 100.0))
        if maker:
            pts += (notional_usd / 1000.0) * float(trading.get("maker_points_per_1k_usd", 1.0))
        return round(fee_usd, 6), round(pts, 4)

    def est_hold_points(self, notional_usd: float, hold_hours: float) -> float:
        m = _model()
        pos = m.get("position") or {}
        pts = (notional_usd / 1000.0) * float(pos.get("points_per_1k_usd_hour", 0.5)) * max(hold_hours, 0.0)
        return round(pts, 4)

    # ── 记录 ───────────────────────────────────────────

    def record_fill(
        self,
        *,
        source: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        maker: bool,
        order_id: str = "",
        session_id: str = "",
        reason: str = "",
    ) -> Optional[int]:
        """开仓/加仓成交 → 写 open 事件（交易积分），并登记持仓时长计量。

        [2026-09] 受交易所配置的积分开关控制：points_enabled=false 时静默跳过。
        """
        if not self.points_enabled():
            return None
        notional = float(qty or 0) * float(price or 0)
        if notional <= 0:
            return None
        fee_usd, trade_points = self.est_trade_points(notional, maker)
        m = _model()
        est_usd = round((trade_points) * _point_value_usd(), 6)
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import AsterdexLivePointsEvent

            db = AnalyticsSessionLocal()
            try:
                ev = AsterdexLivePointsEvent(
                    session_id=str(session_id) if session_id else None,
                    source=str(source or "funding_arb"),
                    event_type="open",
                    order_id=str(order_id or "")[:80],
                    symbol=str(symbol or "").upper()[:20],
                    side=str(side or "").lower()[:10],
                    qty=float(qty),
                    price=float(price),
                    notional_usd=round(notional, 2),
                    maker=bool(maker),
                    fee_usd=fee_usd,
                    trade_points=trade_points,
                    hold_points=0.0,
                    hold_hours=0.0,
                    est_usd=est_usd,
                    model_version=str(m.get("version", "")),
                    reason=str(reason or "")[:40],
                )
                db.add(ev)
                db.commit()
                ev_id = int(ev.id)
                with _registry_lock:
                    _open_registry[str(order_id or ev_id)] = {
                        "ts": time.time(),
                        "notional": notional,
                        "symbol": str(symbol or "").upper(),
                        "source": source,
                        "session_id": str(session_id or ""),
                    }
                logger.info(
                    "[LivePoints] open %s %s $%.0f maker=%s → fee=$%.4f points=%.2f",
                    source, symbol, notional, maker, fee_usd, trade_points,
                )
                return ev_id
            finally:
                db.close()
        except Exception as e:
            logger.warning("[LivePoints] record_fill failed: %s", e)
            return None

    def record_close(
        self,
        *,
        source: str,
        symbol: str,
        order_id: str = "",
        session_id: str = "",
        hold_hours: Optional[float] = None,
        notional_usd: Optional[float] = None,
        reason: str = "",
    ) -> Optional[int]:
        """平仓 → 写 close 事件（持仓积分）。open 时登记的持仓时长优先。

        [2026-09] 受交易所配置的积分开关控制：points_enabled=false 时静默跳过。
        """
        if not self.points_enabled():
            return None
        reg_key = str(order_id or "")
        with _registry_lock:
            reg = _open_registry.pop(reg_key, None) if reg_key else None
            if reg is None and order_id:
                # 尝试用 symbol+source 兜底匹配最近一条 open
                candidates = [v for k, v in _open_registry.items()
                              if v.get("symbol") == str(symbol or "").upper()
                              and v.get("source") == source]
                if candidates:
                    reg = candidates[-1]
        if reg is not None:
            if notional_usd is None:
                notional_usd = float(reg.get("notional") or 0)
            if hold_hours is None:
                hold_hours = max(time.time() - float(reg.get("ts") or time.time()), 0) / 3600.0
            if not session_id:
                session_id = reg.get("session_id") or ""
        else:
            # [2026-09] 进程重启后内存登记丢失 → 从账本找最近 open 事件兜底算时长
            try:
                from backend.database.connection import AnalyticsSessionLocal
                from sqlalchemy import text as _sa_text

                db = AnalyticsSessionLocal()
                try:
                    row = db.execute(_sa_text(
                        "SELECT ts, notional_usd FROM asterdex_live_points_events "
                        "WHERE source = :src AND symbol = :sym AND event_type = 'open' "
                        "ORDER BY id DESC LIMIT 1"
                    ), {"src": str(source or "funding_arb"),
                        "sym": str(symbol or "").upper()}).fetchone()
                finally:
                    db.close()
                if row is not None:
                    if notional_usd is None:
                        notional_usd = float(row[1] or 0)
                    if hold_hours is None:
                        try:
                            hold_hours = max((time.time() - row[0].timestamp()) / 3600.0, 0.0)
                        except Exception:
                            hold_hours = 0.0
            except Exception:
                pass
        notional = float(notional_usd or 0)
        hours = float(hold_hours or 0)
        if notional <= 0:
            return None
        hold_points = self.est_hold_points(notional, hours)
        est_usd = round(hold_points * _point_value_usd(), 6)
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import AsterdexLivePointsEvent

            db = AnalyticsSessionLocal()
            try:
                ev = AsterdexLivePointsEvent(
                    session_id=str(session_id) if session_id else None,
                    source=str(source or "funding_arb"),
                    event_type="close",
                    order_id=str(order_id or "")[:80],
                    symbol=str(symbol or "").upper()[:20],
                    side="",
                    qty=None,
                    price=None,
                    notional_usd=round(notional, 2),
                    maker=None,
                    fee_usd=0.0,
                    trade_points=0.0,
                    hold_points=hold_points,
                    hold_hours=round(hours, 4),
                    est_usd=est_usd,
                    model_version=str(_model().get("version", "")),
                    reason=str(reason or "")[:40],
                )
                db.add(ev)
                db.commit()
                logger.info(
                    "[LivePoints] close %s %s $%.0f %.1fh → hold_points=%.2f",
                    source, symbol, notional, hours, hold_points,
                )
                return int(ev.id)
            finally:
                db.close()
        except Exception as e:
            logger.warning("[LivePoints] record_close failed: %s", e)
            return None

    # ── 汇总 ───────────────────────────────────────────

    def get_summary(self, days: int = 7) -> Dict[str, Any]:
        from backend.database.connection import AnalyticsSessionLocal
        from sqlalchemy import text as _sa_text

        db = AnalyticsSessionLocal()
        try:
            rows = db.execute(_sa_text(
                "SELECT event_type, COUNT(*), COALESCE(SUM(notional_usd),0), "
                "COALESCE(SUM(trade_points),0), COALESCE(SUM(hold_points),0), "
                "COALESCE(SUM(fee_usd),0), COALESCE(SUM(est_usd),0) "
                "FROM asterdex_live_points_events "
                "WHERE ts > NOW() - INTERVAL ':d days' "
                "GROUP BY event_type"
            ), {"d": days}).fetchall()
            total = {"events": 0, "notional_usd": 0.0, "trade_points": 0.0,
                     "hold_points": 0.0, "fee_usd": 0.0, "est_usd": 0.0}
            by_type = {}
            for r in rows:
                et = r[0] or "unknown"
                by_type[et] = {
                    "count": int(r[1]), "notional_usd": float(r[2]),
                    "trade_points": float(r[3]), "hold_points": float(r[4]),
                    "fee_usd": float(r[5]), "est_usd": float(r[6]),
                }
                total["events"] += int(r[1])
                total["notional_usd"] += float(r[2])
                total["trade_points"] += float(r[3])
                total["hold_points"] += float(r[4])
                total["fee_usd"] += float(r[5])
                total["est_usd"] += float(r[6])
            # maker 成交占比 + 官方快照对账
            maker_n, all_n = 0, 0
            mrows = db.execute(_sa_text(
                "SELECT maker, COUNT(*) FROM asterdex_live_points_events "
                "WHERE event_type='open' AND maker IS NOT NULL GROUP BY maker"
            )).fetchall()
            for r in mrows:
                if r[0]:
                    maker_n = int(r[1])
                all_n += int(r[1])
            total["maker_ratio"] = round(maker_n / all_n, 4) if all_n else None
            total["maker_fills"] = maker_n
            total["by_type"] = by_type
            return total
        except Exception as e:
            logger.warning("[LivePoints] summary failed: %s", e)
            return {"error": str(e)[:120]}
        finally:
            db.close()

    def reconcile(self) -> Dict[str, Any]:
        """账本估算 vs 官方积分快照（对账漂移）。

        [2026-09-03] Stage 6 已结束、官方 API 无积分端点（adapter 返回占位快照），
        故 available=False + 明确原因；赛季重开且官方提供接口后再恢复对账。
        """
        m = _model()
        if not bool(m.get("active", True)):
            return {
                "available": False,
                "reason": "stage_ended",
                "stage_status": str(m.get("stage_status") or "ended"),
                "note": str(m.get("stage_note") or "Stage 6 已结束，积分不再产生价值"),
            }
        try:
            from backend.services.exchange.exchange_manager import get_exchange_manager

            mgr = get_exchange_manager()
            client = mgr.get_client("asterdex") or mgr.get_or_create_global_client("asterdex")
            if client is None or not hasattr(client, "get_points_snapshot"):
                return {"available": False, "reason": "no_asterdex_client"}
            import asyncio
            snap = asyncio.run(client.get_points_snapshot())
            est_total = self.get_summary(days=30)
            est_points = (est_total.get("trade_points", 0) + est_total.get("hold_points", 0)) if "error" not in est_total else 0.0
            return {
                "available": True,
                "official_points": round(float(snap.points_balance or 0), 2),
                "multiplier": float(snap.points_multiplier or 1.0),
                "estimated_points_30d": round(float(est_points), 2),
                "season": str(snap.season or ""),
                "airdrop_eligible": bool(getattr(snap, "airdrop_eligible", False)),
                "note": "官方未公开精确权重，估算漂移属预期；以官方快照为准",
            }
        except Exception as e:
            return {"available": False, "reason": str(e)[:120]}


# 单例
live_points_engine = LivePointsEngine()
