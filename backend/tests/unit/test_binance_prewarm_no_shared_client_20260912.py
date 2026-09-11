# -*- coding: utf-8 -*-
"""[2026-09-12 F38u] 币安实盘余额预热线程泄漏模式锁。

现场：_binance_prewarm_thread 每 30s 用 get_or_create_global_client（共享缓存
客户端）+ asyncio.run（每次新 loop）拉余额；_ensure_loop 检测跨 loop 复用后
重建 ccxt 实例，旧实例（带活跃 aiohttp 会话）弃置不 close → 控制台每 ~40s
一条 "Unclosed client session"。

契约（与 F38o/F38q/F38r 同款根治模式）：
- 余额拉取必须用 create_fresh_client（每次新建），禁止共享缓存客户端；
- close 必须与请求在同一 event loop（finally 内 await close）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.api import account_routes  # noqa: E402


def _src() -> str:
    return inspect.getsource(account_routes._binance_live_balance)


def test_balance_fetch_uses_fresh_client_not_shared():
    src = _src()
    assert "create_fresh_client(" in src, "余额拉取必须每次新建客户端（共享客户端跨 loop 重建即泄漏）"
    # 注释里可提及旧实现，但调用点禁止回归（"." + "(" 只匹配真实调用）
    assert ".get_or_create_global_client(" not in src, "共享缓存客户端是泄漏源，禁止回归"


def test_client_closed_in_same_loop():
    src = _src()
    # finally 块内 await close（与请求同 loop）；若 close 在 asyncio.run 之外新 loop 执行则清理不到
    assert "await _ai.wait_for(client.close(), timeout=5)" in src
    assert "finally:" in src
