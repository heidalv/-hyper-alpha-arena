# -*- coding: utf-8 -*-
"""短线影子模式（v3 F1a）。

决策依据（2026-09-03 拍板）
--------------------------
短线车道 08-26→09-02 净 -$1,193，其中手续费 $1,420：在当前几何（单笔目标 ≈ 4bp、
往返成本 ≈ 8bp）下这条车道"设计上就是负期望"，多跑一天多亏一天。但它也是全系统
样本最多的学习闭环（5.5 万条 triple-barrier 标签）。所以不是"删掉"，而是转影子：

- 所有闸门、信号日志、pwin 校准、triple-barrier 结算 **照常运行**；
- 走到"下真单"那一步时不再调用 paper/live 下单，改写一条 action='shadow_fill' 的
  信号日志（冻结本来会成交的入场价 / TP / SL / 杠杆 / 名义），零手续费；
- 晋升门（恢复真单）由 edge_ledger.shadow_lane_stats 判定：影子样本 N ≥ 300 且
  净收益 95% 置信下界 > 0；未过门时 SCALP_SHADOW_MODE 不得手工关闭。

开关：SCALP_SHADOW_MODE（默认 true）。任何读取异常都按"影子"处理（fail-closed：
出错时宁可不下单，也不能因为一个配置读取错误回到亏损路径）。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_stats: Dict[str, Any] = {"shadow_fills": 0, "last_ts": None, "errors": 0}


def scalp_shadow_enabled(trade_mode: Optional[str] = None) -> bool:
    """影子模式开关，按 paper / live 分治。

    [2026-09-04 用户指令：模拟盘不设影子层] 原实现签名收了 ``trade_mode``
    却从未使用（"paper/live 一律受控"）。但本模块的立论是"单笔目标 ≈ 4bp
    而往返成本 ≈ 8bp，多跑一天多亏一天"，其中占大头的手续费（08-26→09-02
    净 -$1,193 中手续费 $1,420）**只在实盘真实发生**；模拟盘的手续费是虚拟
    记账，用影子去省一笔并不存在的费用，代价是永远拿不到真实成交数据——
    滑点、实际成交价、真实 PnL、退出行为，信号日志里一条都没有。
    而恢复真单的晋升门要求"影子样本 N ≥ 300 且净收益 95% 下界 > 0"，这些
    统计量恰恰需要上述真实成交数据 → 模拟盘被影子挡住 = 晋升门永远走不到。

    live 维持原语义（默认开，未过晋升门不得手工关闭）；paper 默认直接下
    模拟单。`SCALP_SHADOW_MODE_PAPER=true` 可一键回到旧行为。
    fail-closed 不变：任何读取异常都按"影子"处理。
    """
    try:
        mode = (trade_mode or "").strip().lower()
        if mode == "paper":
            raw = (os.getenv("SCALP_SHADOW_MODE_PAPER", "false") or "false").strip().lower()
        else:
            raw = (os.getenv("SCALP_SHADOW_MODE", "true") or "true").strip().lower()
        return raw in ("1", "true", "yes", "on")
    except Exception:
        return True


def record_shadow_fill(
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    tp_price: Optional[float],
    sl_price: Optional[float],
    leverage: Optional[float],
    notional_usd: Optional[float],
    factor_score: float,
    threshold: Optional[float],
    session_id: Optional[str],
    account_id: Optional[int],
    trade_mode: Optional[str],
    features: Optional[Dict[str, Any]] = None,
) -> bool:
    """把"本来会成交"的一单写进 scalp_signal_log（action='shadow_fill'）。

    triple-barrier 结算对 action 无感，因此这些行会与真实信号一样被打标签；
    edge_ledger 只统计 action='shadow_fill' 行来评估"假如成交"的净期望。
    """
    try:
        from backend.services.scalp_signal_logger import log_signal

        entry = float(entry_price or 0)
        if entry <= 0:
            return False
        tp_pct = None
        sl_pct = None
        if tp_price and float(tp_price) > 0:
            tp_pct = abs(float(tp_price) - entry) / entry
        if sl_price and float(sl_price) > 0:
            sl_pct = abs(entry - float(sl_price)) / entry
        feats = dict(features or {})
        feats.update({
            "shadow": True,
            "trade_mode": str(trade_mode or "paper"),
            "would_leverage": float(leverage or 0),
            "would_notional_usd": float(notional_usd or 0),
            "would_tp": float(tp_price or 0),
            "would_sl": float(sl_price or 0),
        })
        log_signal(
            symbol=symbol,
            direction=direction,
            action="shadow_fill",
            factor_score=float(factor_score or 0),
            threshold=(float(threshold) if threshold is not None else None),
            entry_price=entry,
            features=feats,
            session_id=session_id,
            account_id=account_id,
            signal_ts=int(time.time()),
            tp_pct=tp_pct,
            sl_pct=sl_pct,
        )
        _stats["shadow_fills"] += 1
        _stats["last_ts"] = time.time()
        return True
    except Exception as exc:
        _stats["errors"] += 1
        logger.warning("[ScalpShadow] 影子成交落库失败 %s: %s", symbol, exc)
        return False


def shadow_mode_stats() -> Dict[str, Any]:
    return {"enabled": scalp_shadow_enabled(), **_stats}
