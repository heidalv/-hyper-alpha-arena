# -*- coding: utf-8 -*-
"""实盘权益 helper：用户流快照优先 → REST 币安余额兜底（供 scalp/midlong 实盘会话用）。"""
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# 文件在 backend/services/full_auto/ 下，仓库根需上溯 4 级
# [2026-08-28 修复] 原 3 级上溯指向 backend/ → data 快照路径错误 → equity 恒 0
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def get_live_equity(account, trading_acct_id) -> float:
    """实盘账户真实权益（USDT 口径）。失败返回 0.0（调用方跳过开仓，安全）。

    [2026-08-28 实盘零成交修复] 失败不再静默：两个数据源都拿不到时打 WARNING，
    给出具体原因（快照缺失/过期/账户不匹配 vs REST 异常），避免「权益恒 0、
    整轮跳过开仓」却没有一条告警的盲区。
    """
    _reasons = []
    # 1) 用户流快照（<20s 新鲜，零币安调用）
    try:
        p = os.path.join(_REPO, "data", "live_user_stream_snapshot.json")
        if os.path.exists(p):
            _age = time.time() - os.path.getmtime(p)
            if _age <= 20.0:
                with open(p, encoding="utf-8") as f:
                    snap = json.load(f)
                if int(snap.get("account_id") or 0) == int(trading_acct_id):
                    usdt = (snap.get("balances") or {}).get("USDT") or {}
                    eq = float(usdt.get("cw") or 0) or float(usdt.get("wb") or 0)
                    if eq > 0:
                        return eq
                    _reasons.append("snapshot USDT 余额为 0")
                else:
                    _reasons.append(
                        f"snapshot account_id={snap.get('account_id')} != {trading_acct_id}"
                    )
            else:
                _reasons.append(f"snapshot 过期 {_age:.0f}s>20s")
        else:
            _reasons.append("snapshot 文件不存在")
    except Exception as _snap_err:
        _reasons.append(f"snapshot 读取异常: {_snap_err}")
    # 2) REST 币安余额兜底
    try:
        import asyncio

        from backend.services.exchange.exchange_manager import get_exchange_manager

        # [2026-08-28 实盘零成交修复] 新建客户端：缓存客户端跨 asyncio.run 复用
        # 会抛 "Event loop is closed"（权益兜底跑在 scalp 热路径上，必须可靠）。
        client = get_exchange_manager().create_fresh_client(
            "binance",
            user_id=account.user_id or 1,
            account_id=trading_acct_id,
            market_type=getattr(account, "binance_market_type", None) or "usdt_m",
        )
        if client is not None:
            # [2026-09-11 修复] 旧实现 get_balance 与 close 分属两个 asyncio.run：
            # aiohttp 会话绑定在第一个 loop，第二个 loop 里 close 清理不到 →
            # 每次权益兜底泄漏一个 session（backend.error.log 每 30-45s 一轮
            # "Unclosed client session"）。改为单 loop 内 finally close。
            async def _run():
                try:
                    return await client.get_balance()
                finally:
                    try:
                        _raw = getattr(client, "_exchange", None)
                        if _raw is not None and hasattr(_raw, "close"):
                            await _raw.close()
                    except Exception:
                        pass

            try:
                bal = asyncio.run(_run())
            except Exception:
                bal = None
            eq = float(getattr(bal, "total_equity", 0) or 0)
            if eq > 0:
                return eq
            _reasons.append("REST total_equity=0")
        else:
            _reasons.append("binance 客户端创建失败")
    except Exception as _rest_err:
        _reasons.append(f"REST 余额异常: {_rest_err}")
    logger.warning(
        "[LiveEquity] 实盘账户 %s 权益不可用（本轮跳过开仓）: %s",
        trading_acct_id, "; ".join(_reasons) or "未知原因",
    )
    return 0.0
