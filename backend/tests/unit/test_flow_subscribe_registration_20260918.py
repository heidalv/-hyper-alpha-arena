# -*- coding: utf-8 -*-
"""轮75 P1-9 回归：Flow 采集的 symbol 注册在订阅之后 → 失败时不可达 → 成交永不落库。

## 事故

`market_flow_collector._subscribe_symbol()` 旧实现：

    try:
        self.trade_buffers[symbol] = TradeBuffer()
        trades_id = self.info.subscribe({"type": "trades", "coin": symbol}, ...)   # ← 这里抛
        ...
        self.subscribed_symbols.append(symbol)      # ← 永不执行
    except Exception as e:
        logger.error(f"Failed to subscribe {symbol}: {e}")

`hyperliquid/info.py` 的 `subscribe()` 对不在 Hyperliquid 永续列表里的 coin 会抛
`KeyError`（未加保护的 `self.name_to_coin[coin]`）。于是：

1. 第一个 subscribe 就抛 `KeyError('COTI')`；
2. 控制流跳到 except → `append` **不可达** → 该 symbol 从未进入订阅表；
3. `_flush_trades` 迭代 `subscribed_symbols` → 这些 symbol 的成交**永不落库**；
4. 下游 CVD 返回 None，`signal_detection_service` 直接 `return None`
   → 流量信号永不触发（静默，无 error 级痕迹）。

实测：COTI/MU 在任何交易所、30 天内 0 行 `market_trades_aggregated`。

## 修复

先注册（`subscribed_symbols` / `subscription_ids` / `trade_buffers`），
再逐通道订阅；单通道失败只 warning，不再被一个失败掩盖全部。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.market_flow_collector import MarketFlowCollector


class _StubInfo:
    """模拟 hyperliquid Info：对缺失 coin 抛 KeyError，对存在的返回 id。"""

    def __init__(self, known=("BTC", "ETH"), fail_all=False):
        self.known = set(known)
        self.fail_all = fail_all
        self.calls = []

    def subscribe(self, payload, cb):
        coin = payload.get("coin")
        self.calls.append(coin)
        if self.fail_all or coin not in self.known:
            raise KeyError(coin)
        return f"sub-{coin}-{payload.get('type')}"


def _collector(info):
    c = MarketFlowCollector()
    c.info = info
    c.subscribed_symbols = []
    c.subscription_ids = {}
    c.trade_buffers = {}
    return c


# ══════════════════════════════════════════════════════════════════════
# 1. 核心不变式：订阅失败也必须被登记
# ══════════════════════════════════════════════════════════════════════

def test_symbol_registered_even_when_subscribe_raises():
    """KeyError('COTI') 场景：symbol 仍须进入 subscribed_symbols。"""
    c = _collector(_StubInfo(known=("BTC",)))
    c._subscribe_symbol("COTI")
    assert "COTI" in c.subscribed_symbols, \
        '即使订阅抛 KeyError，也必须登记（否则 _flush_trades 永不处理它的成交）'
    assert "COTI" in c.subscription_ids
    assert "COTI" in c.trade_buffers


def test_all_streams_failing_still_registers():
    c = _collector(_StubInfo(fail_all=True))
    c._subscribe_symbol("MU")
    assert "MU" in c.subscribed_symbols
    assert c.subscription_ids.get("MU") == {}


def test_successful_subscribe_registers_all_ids():
    c = _collector(_StubInfo(known=("BTC",)))
    c._subscribe_symbol("BTC")
    assert "BTC" in c.subscribed_symbols
    assert set(c.subscription_ids["BTC"]) == {"trades", "l2Book", "activeAssetCtx"}


# ══════════════════════════════════════════════════════════════════════
# 2. 部分通道失败不掩盖其它通道
# ══════════════════════════════════════════════════════════════════════

def test_partial_failure_keeps_successful_streams():
    """只有 trades 通道失败，其余两通道仍应记录成功。"""

    class _PartialInfo:
        def subscribe(self, payload, cb):
            if payload.get("type") == "trades":
                raise KeyError(payload.get("coin"))
            return f"sub-{payload.get('type')}"

    c = _collector(_PartialInfo())
    c._subscribe_symbol("ETH")
    assert "ETH" in c.subscribed_symbols
    ids = c.subscription_ids["ETH"]
    assert "l2Book" in ids and "activeAssetCtx" in ids
    assert "trades" not in ids


def test_no_info_is_noop():
    c = _collector(None)
    c._subscribe_symbol("BTC")
    assert "BTC" not in c.subscribed_symbols, 'info 未就绪时不应假装已订阅'


# ══════════════════════════════════════════════════════════════════════
# 3. 幂等 + 与退订配合
# ══════════════════════════════════════════════════════════════════════

def test_subscribe_is_idempotent():
    c = _collector(_StubInfo(known=("BTC",)))
    c._subscribe_symbol("BTC")
    c._subscribe_symbol("BTC")
    assert c.subscribed_symbols.count("BTC") == 1, '不得重复登记（否则 flush 会重复处理）'


def test_refresh_subscriptions_does_not_resubscribe_existing():
    info = _StubInfo(known=("BTC", "ETH"))
    c = _collector(info)
    c._subscribe_symbol("BTC")
    _before = len(info.calls)
    c.running = True
    c.refresh_subscriptions(["BTC", "ETH"])
    # BTC 已订阅 → 不应再次调用 subscribe（ETH 是新的，会调用）
    assert info.calls.count("BTC") == _before, '已订阅的 symbol 不应重复订阅'
    assert "ETH" in c.subscribed_symbols


# ══════════════════════════════════════════════════════════════════════
# 4. 源码级守卫：注册必须在订阅之前
# ══════════════════════════════════════════════════════════════════════

def test_registration_precedes_subscription_in_source():
    import io
    import re
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    src = io.open(os.path.join(root, 'backend/services/market_flow_collector.py'), encoding='utf-8').read()
    m = re.search(r'def _subscribe_symbol\(.*?(?=\n    def )', src, re.S)
    body = m.group(0)
    code = '\n'.join(l for l in body.splitlines() if not l.lstrip().startswith('#'))
    i_reg = code.find('self.subscribed_symbols.append(symbol)')
    i_sub = code.find('.subscribe(')
    assert i_reg > 0 and i_sub > 0
    assert i_reg < i_sub, \
        '注册必须早于任何 subscribe 调用（否则失败时 append 不可达 —— 这正是原 bug）'
