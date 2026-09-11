"""live_trade_facts — 实盘平仓写入统一交易事实表（trade_facts, source='live'）。

[2026-08-29 全面修复 P2.3] 背景：trade_facts INSERT 硬编码 source='paper'、
live 平仓无 PnL 回填、live_sub_positions 无 pnl/close_price 列——实盘交易
完全不进学习/归因闭环（诊断报告红旗#1）。

设计（先落账后回填）：
  - LPM 平仓时立即写 trade_facts（source='live'）：entry_price 取子仓加权
    均价，exit_price 取回调成交价（拿不到=0），pnl 仅在成交价已知时计算；
  - 成交价未知 → outcome=''（pending），后续由对账/回填任务补 pnl；
  - pnl 已知时同步喂 midlong_circuit_gate（实盘连亏熔断同样生效）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _nature_to_tier(trade_nature: str) -> str:
    n = (trade_nature or "").lower()
    return {"scalp": "short", "swing": "mid", "trend_follow": "long",
            "position": "long", "intraday": "short"}.get(n, n or "mid")


def record_live_close_trade_fact(
    *,
    account_id: int,
    symbol: str,
    subs: List[Any],
    closed_size: float,
    fill_price: float,
    close_reason: str,
    trade_nature: str = "",
    full_close: bool = True,
) -> None:
    """LPM 平仓后写 trade_facts（独立会话，失败不影响主链路）。"""
    try:
        if not subs or closed_size <= 0:
            return
        total = sum(float(p.size or 0) for p in subs)
        if total <= 0:
            return
        # 加权开仓均价（按平仓前 size；部分平仓时近似整仓均价）
        avg_entry = sum(
            float(p.entry_price or 0) * float(p.size or 0) for p in subs
        ) / total
        side = "long" if subs[0].side == "long" else "short"
        tier = _nature_to_tier(trade_nature or getattr(subs[0], "trade_nature", "") or "")
        fill = float(fill_price or 0)
        _fill_src = "callback"
        if fill <= 0:
            # [2026-08-29 P2.3 后半] 成交价兜底：当前平仓回调拿不到成交价（恒0），
            # 用市场最新价近似 exit（市价单滑点小，误差远小于"pnl 永久缺失"）。
            try:
                from backend.services.market_price_service import get_price
                _px = get_price(str(symbol).upper())
                if _px and float(_px) > 0:
                    fill = float(_px)
                    _fill_src = "market_price_approx"
            except Exception:
                pass
        pnl: Optional[float] = None
        if fill > 0 and avg_entry > 0:
            direction = 1.0 if side == "long" else -1.0
            pnl = (fill - avg_entry) / avg_entry * closed_size * avg_entry * direction
        outcome = "" if pnl is None else ("win" if pnl > 0 else ("loss" if pnl < 0 else "scratch"))

        from sqlalchemy import text as _sa_text
        from backend.database.connection import SessionLocal as _ArenaLocal
        import json as _json
        with _ArenaLocal() as _db:
            _db.execute(_sa_text(
                "INSERT INTO trade_facts "
                "(source, account_id, position_id, symbol, tier, side, entry_price, "
                " exit_price, fees, pnl, outcome, close_reason, factor_exposures, strategy_id) "
                "VALUES ('live', :a, :p, :s, :t, :d, :e, :x, :f, :pnl, :o, :r, "
                " CAST(:fx AS JSONB), :sid)"
            ), {
                "a": int(account_id), "p": f"live:{subs[0].id}", "s": str(symbol).upper(),
                "t": tier, "d": side, "e": float(avg_entry or 0), "x": fill,
                "f": 0.0, "pnl": pnl, "o": outcome, "r": str(close_reason or "")[:60],
                "fx": _json.dumps({
                    "live_meta": {
                        "closed_size": float(closed_size),
                        "full_close": bool(full_close),
                        "fill_known": fill > 0,
                        "fill_src": _fill_src,
                        "trade_nature": str(trade_nature or ""),
                        "sub_ids": [int(p.id) for p in subs[:8]],
                        "note": "成交价由市价单回调/市场最新价近似；精确成交价与费用由交易所回填",
                    }
                }, ensure_ascii=False),
                "sid": str(getattr(subs[0], "strategy_id", "") or ""),
            })
            _db.commit()

        if pnl is not None:
            try:
                from backend.services.full_auto.midlong_circuit_gate import (
                    record_midlong_outcome,
                )
                if tier in ("mid", "long"):
                    record_midlong_outcome(account_id, str(symbol).upper(), pnl)
            except Exception:
                pass

            # [2026-09-02 因子闭环修复 D10] 把已实现盈亏回填到开仓时写的因子快照，
            # 闭合"实盘因子 → 实盘结果"链路。接在这里而不是 LPM 里，是因为
            # close_sub_position 与 close_all_symbol 两条平仓路径都汇聚到本函数，
            # 且 pnl（含成交价缺失时的市场价兜底）在此已经算好，一处接线全覆盖。
            # pnl_pct 用 ROI 小数，与 paper 侧 _notify_learning_on_close 的
            # pnl/(entry_price*full_size) 同口径 —— 换成百分数会让实盘样本比模拟盘
            # 大 100 倍，IC 估计直接失真。
            try:
                from backend.services.live_learning_hooks import (
                    backfill_live_close_pnl,
                )
                _roi = (
                    float(pnl) / (avg_entry * closed_size)
                    if avg_entry > 0 and closed_size > 0 else 0.0
                )
                backfill_live_close_pnl(
                    sub_ids=[p.id for p in subs], pnl=float(pnl), pnl_pct=_roi,
                )
            except Exception as _bf_err:
                logger.debug("[LiveTradeFacts] 因子快照盈亏回填跳过: %s", _bf_err)
        logger.info(
            "[LiveTradeFacts] 实盘平仓落账 %s %s %s qty=%.6f entry=%.4f fill=%.4f pnl=%s reason=%s",
            symbol, tier, side, closed_size, avg_entry, fill,
            f"{pnl:.4f}" if pnl is not None else "pending",
            close_reason,
        )
    except Exception as exc:
        logger.debug("[LiveTradeFacts] 落账失败(不影响主链路): %s", exc)
