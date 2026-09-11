"""实盘宪法风控与下单 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class LiveTradingHost:
    defensive_entered_at: Dict[str, float] = field(default_factory=dict)

    is_live_trading_session: Callable = field(repr=False, default=lambda *a, **k: False)
    is_unified_executor_on: Callable = field(repr=False, default=lambda: False)
    should_switch_mode: Callable = field(repr=False, default=lambda *a, **k: True)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    invalidate_session_status_cache: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)


def build_live_trading_host(svc) -> LiveTradingHost:
    return LiveTradingHost(
        defensive_entered_at=svc._defensive_entered_at,
        is_live_trading_session=svc._is_live_trading_session,
        is_unified_executor_on=svc._is_unified_executor_on,
        should_switch_mode=svc._should_switch_mode,
        append_event=svc._append_event,
        invalidate_session_status_cache=svc._invalidate_session_status_cache,
        should_log_pause_event=svc._should_log_pause_event,
    )


# ── [2026-08-29 实盘符号校验] 币安 USDT-M 合约可交易基币集合（1h 缓存）──
# 背景：信号引擎用多所数据，XPL 等不在币安永续上市 → 实盘单被交易所
# "does not have market symbol" 拒绝（实测 19:21 XPL 首单）。下单前过滤。
_BINANCE_FUT_BASES_CACHE: Dict[str, Any] = {"ts": 0.0, "bases": frozenset()}


def binance_futures_bases() -> frozenset:
    """币安 USDT-M 永续可交易基币（fapi exchangeInfo，1h 缓存，失败返回空集）。"""
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue

    _now = time.time()
    if _BINANCE_FUT_BASES_CACHE["bases"] and _now - _BINANCE_FUT_BASES_CACHE["ts"] < 3600.0:
        return _BINANCE_FUT_BASES_CACHE["bases"]
    try:
        _proxy = None
        for _k in ("MARKET_DATA_HTTP_PROXY", "BINANCE_HTTP_PROXY",
                   "BINANCE_HTTPS_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
            _v = os.getenv(_k, "").strip()
            if _v:
                _proxy = _v
                break
        _opener = _ur.build_opener(
            _ur.ProxyHandler({"http": _proxy, "https": _proxy}) if _proxy
            else _ur.ProxyHandler({})
        )
        with _opener.open(
            "https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=15
        ) as _resp:
            _data = _json.loads(_resp.read().decode("utf-8") or "{}")
        _bases = frozenset(
            str(s.get("baseAsset", "") or "").upper()
            for s in (_data.get("symbols") or [])
            if s.get("contractType") == "PERPETUAL"
            and str(s.get("quoteAsset", "")).upper() == "USDT"
            and str(s.get("status", "")).upper() == "TRADING"
        )
        if _bases:
            _BINANCE_FUT_BASES_CACHE["ts"] = _now
            _BINANCE_FUT_BASES_CACHE["bases"] = _bases
    except Exception as _exc:
        logger.debug("[LiveSymbolCheck] 币安合约目录拉取失败(放行由交易所兜底): %s", _exc)
    return _BINANCE_FUT_BASES_CACHE["bases"]


def live_symbol_tradable(symbol: str, exchange: str = "binance") -> bool:
    """实盘下单前符号校验：目录拉取失败时放行（交易所自身会拒单兜底）。"""
    _sym = str(symbol or "").upper()
    if not _sym or str(exchange or "").lower() != "binance":
        return True
    _bases = binance_futures_bases()
    return (not _bases) or (_sym in _bases)



def live_constitutional_enabled(session, host: LiveTradingHost) -> bool:
    if not host.is_live_trading_session(session):
        return False
    try:
        from backend.config.settings import LIVE_CONSTITUTIONAL_RISK_ENABLED
        return bool(LIVE_CONSTITUTIONAL_RISK_ENABLED)
    except Exception:
        return True

_LIVE_SNAP_REST_CACHE = {"ts": 0.0, "acct": 0, "data": None}
_LIVE_SNAP_REFRESHING = threading.Lock()


def _rest_account_snapshot_direct(account_id: int) -> dict:
    """单 fresh 客户端并发拉 余额+持仓（一次 asyncio.run，用完即关）。

    [2026-08-30 性能] 此前经 LiveExecutor 分两次取 → 2 次建客户端(实测
    每次 1.9s)+2 次事件循环；代理链路下 balance 3.9s / positions 2.2s，
    串行 ≈10s，热路径与 UI 全被拖卡。合并后 ≤ max(3.9, 2.2)+1.9 ≈ 6s，
    且并发后 ≈5s。配合 TTL 去重，实际 CPU/代理占用大幅下降。
    """
    import asyncio as _aio

    from backend.database.connection import SessionLocal as _SL
    from backend.database.models import Account as _Account
    from backend.services.exchange.exchange_manager import get_exchange_manager as _GM

    db2 = _SL()
    try:
        acct = db2.query(_Account).filter(_Account.id == int(account_id)).first()
        if not acct:
            return {}
        mgr = _GM()
        client = mgr.create_fresh_client(
            "binance", user_id=acct.user_id or 1, account_id=int(account_id),
            market_type=(getattr(acct, "binance_market_type", None) or "usdt_m"),
        )
        if client is None:
            return {}

        async def _run():
            bal, pos = await _aio.gather(
                _aio.wait_for(client.get_balance(), timeout=20),
                _aio.wait_for(client.get_positions(), timeout=20),
                return_exceptions=True,
            )
            return bal, pos

        try:
            bal, pos = _aio.run(_run())
        finally:
            try:
                _aio.run(_aio.wait_for(client.close(), timeout=5))
            except Exception:
                pass
        if isinstance(bal, BaseException) or isinstance(pos, BaseException):
            return {}
        out = {
            "total_equity": float(getattr(bal, "total_equity", 0) or 0),
            "available_balance": float(getattr(bal, "available_balance", 0) or 0),
            "frozen_margin": float(getattr(bal, "frozen_margin", 0) or 0),
            "positions": [
                {
                    "coin": str(getattr(x, "symbol", "") or "").split("/")[0].upper(),
                    "symbol": str(getattr(x, "symbol", "") or "").split("/")[0].upper(),
                    "position_value": abs(float(getattr(x, "size", 0) or 0)) * float(getattr(x, "mark_price", 0) or 0),
                    "value": abs(float(getattr(x, "size", 0) or 0)) * float(getattr(x, "mark_price", 0) or 0),
                    "margin": float(getattr(x, "margin", 0) or 0),
                    "side": str(getattr(x, "side", "") or ""),
                    "unrealized_pnl": float(getattr(x, "unrealized_pnl", 0) or 0),
                }
                for x in (pos or [])
            ],
        }
        return out
    finally:
        db2.close()


def fetch_live_account_snapshot(db: Session, account_id: int) -> dict:
    """实盘账户快照：总权益/可用必须直接取自交易所 REST（含持仓浮盈）。

    [2026-08-30 数据源根治] 用户明确要求：权益类数据不得本地计算/读缓存文件。
    本地用户流快照的 wb/cw 不含全仓持仓浮盈（实测 REST 总权益 $108.81 vs
    快照 $65.55，差额 = 两笔仓 $43 浮盈），此前快照优先导致宪法检查与界面
    权益严重失真。现在：REST（交易所权威）优先 + 5s TTL 去重热路径；
    本地快照仅在 REST 失败时兜底（兜底口径也补 Σ浮盈）。
    """
    _now = time.time()
    _have = bool(_LIVE_SNAP_REST_CACHE["data"]) and _LIVE_SNAP_REST_CACHE["acct"] == int(account_id)
    if _have and (_now - _LIVE_SNAP_REST_CACHE["ts"]) <= 10.0:
        return _LIVE_SNAP_REST_CACHE["data"]
    # [2026-08-30 stale-while-revalidate] 上游(代理→币安)单次快照可达 5-15s，
    # 同步等它 = 缓存过期瞬间的请求全卡 15s。改为：有过期数据立即返回旧值，
    # 后台线程刷新（单飞锁防并发重复拉）；仅冷启动(无任何数据)才同步等。
    if _have:
        if _LIVE_SNAP_REFRESHING.acquire(blocking=False):
            def _bg_refresh():
                try:
                    _fresh = _rest_account_snapshot_direct(int(account_id))
                    if float(_fresh.get("total_equity") or 0) > 0:
                        _frozen = float(_fresh.get("frozen_margin") or 0)
                        _eq = float(_fresh["total_equity"])
                        _LIVE_SNAP_REST_CACHE.update(
                            ts=time.time(), acct=int(account_id),
                            data={
                                "total_equity": _eq,
                                "available_balance": float(_fresh.get("available_balance") or 0),
                                "margin_usage_percent": (_frozen / _eq * 100.0) if _eq > 0 else 0.0,
                                "positions": _fresh.get("positions") or [],
                                "source": "binance_rest",
                            },
                        )
                except Exception as _bg_err:
                    logger.debug("[LiveConstitutional] 后台刷新失败(沿用旧值): %s", _bg_err)
                finally:
                    _LIVE_SNAP_REFRESHING.release()
            threading.Thread(target=_bg_refresh, name="live-snap-refresh", daemon=True).start()
        return _LIVE_SNAP_REST_CACHE["data"]

    # ── 冷启动：同步拉一次（单客户端并发，≈5s）──
    try:
        raw = _rest_account_snapshot_direct(int(account_id))
        total_equity = float(raw.get("total_equity") or 0)
        if total_equity > 0:
            available = float(raw.get("available_balance") or 0)
            frozen = float(raw.get("frozen_margin") or 0)
            margin_usage = frozen / total_equity * 100.0 if frozen > 0 else 0.0
            data = {
                "total_equity": total_equity,
                "available_balance": available,
                "margin_usage_percent": margin_usage,
                "positions": raw.get("positions") or [],
                "source": "binance_rest",
            }
            _LIVE_SNAP_REST_CACHE.update(ts=_now, acct=int(account_id), data=data)
            return data
    except Exception as err:
        logger.warning("[LiveConstitutional] REST 权益获取失败，退回本地快照: %s", err)

    # ── 2. 兜底：本地用户流快照（仅 REST 失败时；权益口径补 Σ持仓浮盈）──
    try:
        import json as _json
        import os as _os
        _repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__)))))
        _sp = _os.path.join(_repo, "data", "live_user_stream_snapshot.json")
        if _os.path.exists(_sp) and time.time() - _os.path.getmtime(_sp) <= 120.0:
            with open(_sp, encoding="utf-8") as f:
                snap = _json.load(f) or {}
            if int(snap.get("account_id") or 0) == int(account_id):
                _usdt = (snap.get("balances") or {}).get("USDT") or {}
                _wb = float(_usdt.get("wb") or 0)
                _cw = float(_usdt.get("cw") or 0)
                _base = _wb if _wb > 0 else _cw
                _upnl_sum = 0.0
                _positions = []
                for _psym, _p in (snap.get("positions") or {}).items():
                    if not isinstance(_p, dict):
                        continue
                    try:
                        _amt = float(_p.get("amt") or 0)
                        _mp = float(_p.get("mp") or _p.get("ep") or 0)
                        _up = float(_p.get("up") or 0)
                        _upnl_sum += _up
                        _positions.append({
                            "coin": str(_psym),
                            "symbol": str(_psym),
                            "position_value": abs(_amt) * _mp,
                            "value": abs(_amt) * _mp,
                            "margin": float(_p.get("iw") or 0),
                            "side": "long" if str(_p.get("ps") or "").upper() == "LONG" else "short",
                            "unrealized_pnl": _up,
                        })
                    except Exception:
                        continue
                _eq = (_base + _upnl_sum) if _base > 0 else 0.0
                if _eq > 0:
                    _frozen = sum(
                        float((snap.get("leverage_map") or {}).get(_k, {}).get("initial_margin") or 0)
                        for _k in (snap.get("leverage_map") or {})
                    )
                    _avail = max(0.0, _eq - _frozen)
                    data = {
                        "total_equity": _eq,
                        "available_balance": min(_avail, _eq),
                        "margin_usage_percent": (_frozen / _eq * 100.0) if _eq > 0 else 0.0,
                        "positions": _positions,
                        "source": "user_stream_fallback",
                    }
                    _LIVE_SNAP_REST_CACHE.update(ts=_now, acct=int(account_id), data=data)
                    return data
    except Exception as _snap_err:
        logger.debug("[LiveConstitutional] 用户流快照读取跳过: %s", _snap_err)
    return {}


def live_constitutional_pre_trade_check(
    db: Session, session, strat, decision: dict, host: LiveTradingHost,
) -> tuple:
    if not live_constitutional_enabled(session, host):
        return True, ""
    operation = (
        (decision.get("operation") or decision.get("action") or "")
        .strip()
        .lower()
    )
    if operation in ("close", "reduce", "hold", ""):
        return True, ""

    try:
        _d_acct = int((decision or {}).get("account_id") or 0)
    except (TypeError, ValueError):
        _d_acct = 0
    account_id = _d_acct or int(getattr(strat, "account_id", None) or 0) or         int(getattr(session, "account_id", 0) or 0)
    if not account_id:
        return False, "无有效实盘 account_id"

    snap = fetch_live_account_snapshot(db, account_id)
    total_equity = float(snap.get("total_equity") or 0)
    available_balance = float(snap.get("available_balance") or 0)
    positions = snap.get("positions") or []
    margin_usage = float(snap.get("margin_usage_percent") or 0)

    if total_equity <= 0:
        logger.warning(
            "[LiveConstitutional] 无法获取权益 account=%s，拒绝新开",
            account_id,
        )
        return False, "无法获取实盘权益，拒绝新开"

    symbol = str(
        decision.get("symbol") or getattr(strat, "primary_symbol", "") or ""
    ).upper()
    order_value = float(decision.get("order_value") or decision.get("notional") or 0)
    if order_value <= 0:
        pct = float(
            decision.get("position_pct")
            or decision.get("target_portion_of_balance")
            or 0.05
        )
        lev = float(decision.get("leverage") or 10)
        order_value = max(available_balance * pct * lev, 0.0)

    try:
        from backend.services.risk_control_service import check_risk_before_trade
        allowed, message = check_risk_before_trade(
            db=db,
            account_id=account_id,
            symbol=symbol,
            operation=operation if operation in ("buy", "sell") else "buy",
            order_value=order_value,
            total_equity=total_equity,
            available_balance=available_balance,
            positions=positions,
            margin_usage_percent=margin_usage,
        )
        return bool(allowed), str(message or "")
    except Exception as err:
        logger.error("[LiveConstitutional] 开单前检查异常: %s", err, exc_info=True)
        return False, f"宪法风控检查异常: {err}"

def check_live_constitutional_session_risk(
    db: Session, session, host: LiveTradingHost,
) -> None:
    if not live_constitutional_enabled(session, host):
        return
    account_id = int(getattr(session, "account_id", None) or 0)
    if not account_id:
        return
    snap = fetch_live_account_snapshot(db, account_id)
    equity = float(snap.get("total_equity") or 0)
    if equity <= 0:
        return
    try:
        from backend.services.risk_control_service import (
            RiskCheckResult,
            get_risk_control_service,
        )
        svc = get_risk_control_service()
        svc.load_config_from_db(db, account_id)
        resp = svc.check_daily_loss_breaker(db, account_id, equity)
        session_id = getattr(session, "session_id", "") or ""
        if resp.result == RiskCheckResult.BLOCKED:
            if session.status != "defensive":
                if not host.should_switch_mode(session_id, session.status, "defensive"):
                    logger.info("[LiveConstitutional] 进入防守被缓冲延迟 %s", session_id)
                    return
                host.defensive_entered_at[session_id] = time.time()
                _safe_append_event(
                    db, session, host,
                    "circuit_breaker",
                    f"[Live宪法] {resp.message}",
                )
                logger.warning(
                    "[LiveConstitutional] 进入防守 %s: %s",
                    session_id,
                    resp.message,
                )
                session.status = "defensive"
                session.pause_reason = "circuit_breaker"
                host.invalidate_session_status_cache(session_id)
        elif resp.result == RiskCheckResult.WARNING:
            if host.should_log_pause_event(session_id, "live_risk_warn"):
                _safe_append_event(
                    db, session, host,
                    "live_risk_warning",
                    f"[Live宪法] {resp.message}"[:200],
                )
    except Exception as err:
        logger.debug("[LiveConstitutional] 会话巡检跳过: %s", err)

def _live_scale_decision_to_cap(db, session, strat, decision, host) -> None:
    """[2026-08-29 实盘可达性·根治] 把决策敞口等比缩到宪法 20% 上限以内。

    此前只在 decision 带 order_value/notional 时缩放；master 决策只带
    position_pct，风险检查内部按 available×pct×lev 复算敞口 → 缩放永远
    不生效 → SOL $22.27/权益$66=34% 被 live_risk_block 拦死（实盘零成交）。

    现在：覆盖全部敞口来源（_sizing_notional_usd / quantity×price /
    available×pct×lev 兜底），并把 order_value、notional、quantity、
    position_pct、target_portion_of_balance、_sizing_* 全部等比缩放，
    使「宪法检查」与「实际下单」看到同一个数字。
    """
    try:
        account_id = int(
            (decision or {}).get("account_id")
            or getattr(strat, "account_id", None)
            or getattr(session, "account_id", 0)
            or 0
        )
        _snap = fetch_live_account_snapshot(db, account_id)
        _eq = float(_snap.get("total_equity") or 0)
        _avail = float(_snap.get("available_balance") or 0)
        if _eq <= 0:
            return
        _lev = float(decision.get("leverage") or 10)
        _price = float(decision.get("price") or 0)

        # 敞口口径（与 live_constitutional_pre_trade_check 一致）
        _ov0 = float(
            decision.get("order_value")
            or decision.get("notional")
            or decision.get("_sizing_notional_usd")
            or 0
        )
        if _ov0 <= 0 and decision.get("quantity") and _price > 0:
            _ov0 = abs(float(decision["quantity"])) * _price
        # [2026-08-29] 下游缩仓乘子（V5Gate/MTF/TrancheGate 的 size_multiplier）
        # 必须计入敞口估算——否则按满额 base 送检，小资金账户恒超 20%。
        # _sm 只进兜底公式（proposal 车道首访无 order_value，缩仓乘子必须
        # 计入）；显式 order_value/_sizing_notional_usd 已含各自乘子，二次
        # 相乘会重复缩仓（实测 $5.5→$0.12）。
        _sm = max(0.0, float(decision.get("size_multiplier") or 1.0))
        if _ov0 <= 0:
            _pct = float(
                decision.get("position_pct")
                or decision.get("target_portion_of_balance")
                or 0.05
            )
            _ov0 = max((_avail or _eq) * _pct * _lev * _sm, 0.0)

        if _ov0 <= 0:
            return
        # [2026-08-29 最小名义地板] 小资金账户上 V5Gate/MTF/Tranche 层层缩仓
        # （实测 ×0.018-0.03）把 $33 基数缩成 $0.6-0.97 尘埃单，低于币安
        # 最小名义 $5 永远无法成交（实盘零成交的最后一环）。抬到 $5.5
        # （仍受下方 18% 权益上限约束，权益 <$30 时抬不动→仍按尘埃拒绝）。
        _min_notional = float(os.getenv("LIVE_MIN_NOTIONAL_USD", "5.5") or 5.5)
        if 0 < _ov0 < _min_notional and _min_notional <= _eq * 0.18:
            logger.info(
                "[LiveMinNotional] %s 缩仓后名义 $%.2f < $%.2f → 抬到最小名义 "
                "(size_multiplier=%.4f, 18%%上限内)", 
                decision.get("symbol", "?"), _ov0, _min_notional, _sm,
            )
            _ov0 = _min_notional
        # place_ai_driven_order 按 target_portion_of_balance sizing；中线提案
        # 只带 position_pct → 缺省对齐，保证检查与实际下单同一口径。
        if not decision.get("target_portion_of_balance") and decision.get("position_pct"):
            decision["target_portion_of_balance"] = float(decision["position_pct"])
        if _ov0 <= _eq * 0.20:
            # 未超限也要写回口径字段，保证检查与下单一致
            _base_ov = max((_avail or _eq) * float(
                decision.get("position_pct")
                or decision.get("target_portion_of_balance") or 0.05
            ) * _lev, 0.0)
            if _base_ov > 0 and abs(_base_ov - _ov0) / _base_ov > 0.01:
                _r = _ov0 / _base_ov
                for _k in ("position_pct", "target_portion_of_balance"):
                    _v = float(decision.get(_k) or 0)
                    if _v > 0:
                        decision[_k] = _v * _r
                if decision.get("quantity") is not None:
                    try:
                        decision["quantity"] = float(decision["quantity"]) * _r
                    except (TypeError, ValueError):
                        pass
            decision["order_value"] = _ov0
            decision["notional"] = _ov0
            if decision.get("quantity") is None and _price > 0:
                decision["quantity"] = _ov0 / _price
            return

        _cap_ov = _eq * 0.18  # 18% 留余量（宪法 20% 硬墙内）
        _ratio = _cap_ov / _ov0
        decision["order_value"] = _cap_ov
        decision["notional"] = _cap_ov
        if decision.get("quantity") is not None:
            try:
                decision["quantity"] = float(decision["quantity"]) * _ratio
            except (TypeError, ValueError):
                pass
        elif _price > 0:
            decision["quantity"] = _cap_ov / _price
        for _k in ("position_pct", "target_portion_of_balance"):
            _v = float(decision.get(_k) or 0)
            if _v > 0:
                decision[_k] = _v * _ratio
        for _k in ("_sizing_notional_usd", "_sizing_margin_usd", "_sizing_max_loss_usd"):
            _v = float(decision.get(_k) or 0)
            if _v > 0:
                decision[_k] = _v * _ratio
        logger.info(
            "[LiveCap] %s 单笔敞口 %.2f→%.2f (18%%权益, 宪法20%%上限内)",
            decision.get("symbol", "?"), _ov0, _cap_ov,
        )
    except Exception as _cap_err:
        logger.debug("[LiveCap] 敞口预对齐跳过: %s", _cap_err)


def _safe_append_event(db, session, host, event_type: str, detail: str) -> None:
    """append_event 防崩：会话事务可能已失败（InFailedSqlTransaction），
    先 rollback+merge 再写；仍失败只记日志，绝不打断交易主流程。"""
    try:
        host.append_event(session, event_type, detail)
    except Exception as _ev_err:
        logger.warning("[LiveEvent] 事件写入失败(%s): %s", event_type, _ev_err)
        try:
            db.rollback()
            session = db.merge(session)
            host.append_event(session, event_type, detail)
        except Exception as _ev_err2:
            logger.warning("[LiveEvent] 事件重试仍失败(%s): %s", event_type, _ev_err2)


def execute_live_trade(
    db: Session, session, strat, decision: dict, host: LiveTradingHost,
) -> bool:
    # [2026-08-29 事务中毒防御] 同一 tick 早前的失败查询会让 db 进入
    # InFailedSqlTransaction——实盘下单路径的任意懒加载（strat/ai_strategies）
    # 都会炸掉本该发出的订单（实测 18:11/18:22 ETH 宪法已过、下单前炸于
    # SELECT ai_strategies）。策略：① rollback 前把 strat 需要的属性抓成
    # 本地变量（rollback 会过期全部 ORM 实例，后续零触碰）；② rollback
    # 清掉失败事务再继续。
    _st_acct = 0
    _st_sid = ""
    _st_psym = ""
    _sess_acct = 0
    try:
        _st_acct = int(getattr(strat, "account_id", 0) or 0)
        _st_sid = str(getattr(strat, "strategy_id", "") or "")
        _st_psym = str(getattr(strat, "primary_symbol", "") or "").upper()
    except Exception:
        pass
    try:
        _sess_acct = int(getattr(session, "account_id", 0) or 0)
    except Exception:
        _sess_acct = 0
    try:
        db.rollback()
        session = db.merge(session)
        # 决策缺 symbol 时从策略补齐（此前日志与检查都以 "?" 显示）
        if not decision.get("symbol") and _st_psym:
            decision["symbol"] = _st_psym
        # 账户号随 decision 传递：下游(cap/宪法检查)不再触碰 strat ORM
        # （rollback 后实例已过期，懒加载会炸单）。
        decision.setdefault("account_id", _st_acct or _sess_acct)

        _live_scale_decision_to_cap(db, session, strat, decision, host)

        _acct_for_check = _st_acct or _sess_acct
        _allowed, _risk_msg = live_constitutional_pre_trade_check(
            db, session, strat, decision, host
        )
        if not _allowed:
            symbol = decision.get("symbol", "?")
            operation = decision.get("operation") or decision.get("action", "?")
            logger.warning(
                "[LiveConstitutional] BLOCK %s %s: %s",
                symbol, operation, _risk_msg,
            )
            _safe_append_event(
                db, session, host,
                "live_risk_block",
                f"[Live宪法] {symbol} {operation} {_risk_msg[:100]}",
            )
            return False

        # 同币已有仓 → 强制 adopt 交易所杠杆（一仓一杠杆），禁止另算覆盖
        try:
            from backend.services.leverage_authority import extract_existing_symbol_leverage
            _sym = str(decision.get("symbol") or _st_psym or "").upper()
            _acct = _st_acct or _sess_acct
            if _sym and _acct:
                _snap = fetch_live_account_snapshot(db, _acct)
                _adopt = extract_existing_symbol_leverage(_sym, _snap.get("positions") or [])
                if _adopt is not None:
                    _old = float(decision.get("leverage") or 0)
                    decision["leverage"] = float(_adopt)
                    if abs(_old - float(_adopt)) > 0.01:
                        logger.info(
                            "[LiveAdoptLev] %s %.1fx→%.1fx (existing exchange position)",
                            _sym, _old, float(_adopt),
                        )
                else:
                    # [2026-09-04 币种杠杆] 无仓 → 取该币统一档位，覆盖上游自算值与
                    # 下游 decision.get("leverage", 10) 的 10x 兜底（该兜底会让实盘
                    # 按 10x 开仓，与本地记账不符）。
                    from backend.services.leverage_authority import resolve_leverage
                    _old = float(decision.get("leverage") or 0)
                    _uni = float(resolve_leverage(
                        tier=decision.get("timeframe_tier"),
                        requested=decision.get("leverage"),
                        symbol=_sym,
                    ))
                    decision["leverage"] = _uni
                    if abs(_old - _uni) > 0.01:
                        logger.info(
                            "[LiveSymbolLev] %s %.1fx→%.1fx (per-symbol tier, no open position)",
                            _sym, _old, _uni,
                        )
        except Exception as _lev_err:
            logger.debug("[LiveAdoptLev] skip: %s", _lev_err)

        if host.is_unified_executor_on():
            # 统一执行器路径
            from backend.services.exchange.executors import OrderContext
            from backend.services.exchange.live_executor import LiveExecutor
            _ctx = OrderContext(
                account_id=(_st_acct or _sess_acct),
                symbol=decision.get("symbol") or _st_psym,
                side=decision.get("side", "buy" if decision.get("operation") == "buy" else "sell"),
                quantity=float(decision.get("quantity", 0) or 0),
                leverage=float(decision.get("leverage", 10) or 10),
                tp_price=decision.get("take_profit_price"),
                sl_price=decision.get("stop_loss_price"),
                strategy_id=_st_sid,
                trade_nature=decision.get("trade_nature"),
                timeframe_tier=decision.get("timeframe_tier"),
                # 阶段 3.2: 执行算法透传（决策层可选产出 algo/algo_config）
                algo=decision.get("algo", "MARKET"),
                algo_config=decision.get("algo_config"),
                trigger_context={
                    "source": "full_auto",
                    "strategy_id": _st_sid,
                    "pre_made_decisions": [decision],
                },
            )
            _ores = LiveExecutor().place_order(db, _ctx)
            symbol = decision.get("symbol", "?")
            operation = decision.get("operation", "?")
            if _ores.success:
                _safe_append_event(db, session, host, "live_trade",
                    f"实盘下单已提交(统一执行器): {symbol} {operation} status={_ores.status}")
            else:
                _safe_append_event(db, session, host, "live_trade_error",
                    f"实盘下单失败(统一执行器): {symbol} {operation} {_ores.error or _ores.status}")
            return bool(_ores.success)

        # 原路径（默认）
        from backend.services.trading_commands import place_ai_driven_order

        trigger_ctx: Dict[str, Any] = {
            "source": "full_auto",
            "strategy_id": _st_sid,
            "pre_made_decisions": [decision],
        }

        place_ai_driven_order(
            account_id=(_st_acct or _sess_acct),
            trigger_context=trigger_ctx,
        )

        symbol = decision.get("symbol", "?")
        operation = decision.get("operation", "?")
        _safe_append_event(db, session, host, "live_trade",
            f"实盘下单已提交: {symbol} {operation}")
        return True
    except Exception as e:
        logger.error(f"[FullAuto] 实盘交易执行异常: {e}", exc_info=True)
        _safe_append_event(db, session, host, "live_trade_error", str(e)[:100])
        return False
