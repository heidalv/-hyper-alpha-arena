# -*- coding: utf-8 -*-
"""实盘权益 helper：用户流快照优先 → REST 币安余额兜底（供 scalp/midlong 实盘会话用）。"""
import json
import os
import time

# 文件在 backend/services/full_auto/ 下，仓库根需上溯 4 级
# [2026-08-28 修复] 原 3 级上溯指向 backend/ → data 快照路径错误 → equity 恒 0
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def get_live_equity(account, trading_acct_id) -> float:
    """实盘账户真实权益（USDT 口径）。失败返回 0.0（调用方跳过开仓，安全）。"""
    # 1) 用户流快照（<20s 新鲜，零币安调用）
    try:
        p = os.path.join(_REPO, "data", "live_user_stream_snapshot.json")
        if os.path.exists(p) and time.time() - os.path.getmtime(p) <= 20.0:
            with open(p, encoding="utf-8") as f:
                snap = json.load(f)
            if int(snap.get("account_id") or 0) == int(trading_acct_id):
                usdt = (snap.get("balances") or {}).get("USDT") or {}
                eq = float(usdt.get("cw") or 0) or float(usdt.get("wb") or 0)
                if eq > 0:
                    return eq
    except Exception:
        pass
    # 2) REST 币安余额兜底
    try:
        import asyncio

        from backend.services.exchange.exchange_manager import get_exchange_manager

        client = get_exchange_manager().get_or_create_global_client(
            "binance",
            user_id=account.user_id or 1,
            account_id=trading_acct_id,
            market_type=getattr(account, "binance_market_type", None) or "usdt_m",
        )
        if client is not None:
            bal = asyncio.run(client.get_balance())
            eq = float(getattr(bal, "total_equity", 0) or 0)
            if eq > 0:
                return eq
    except Exception:
        pass
    return 0.0
