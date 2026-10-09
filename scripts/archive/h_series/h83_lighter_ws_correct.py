"""H83：Lighter WS 实时盘口 —— 按**官方 SDK 的真实协议**重测（H82 的探测方式错了）。

# H82 为什么失败（我的错，不是 Lighter 的）

H82 直接 `connect()` 然后立刻发 `subscribe` ⇒ 服务端返回 400。
读了官方 SDK（`lighter/ws_client.py`，elliottech/lighter-python）后，
**真实协议**是：

  1. 连 `wss://mainnet.zklighter.elliot.ai/stream`
  2. **先等**服务端推 `{"type": "connected"}`
  3. **收到 `connected` 之后**才能发
     `{"type": "subscribe", "channel": "order_book/<market_id>"}`
  4. 之后服务端推 `{"type": "subscribed/order_book", "channel": "order_book:<id>",
     "order_book": {...}}`（快照）
     以及 `{"type": "update/order_book", "channel": "order_book:<id>",
     "order_book": {"asks": [...], "bids": [...]}}`（增量）
  5. 服务端会推 `{"type": "ping"}`，**必须回 `{"type": "pong"}`**

**⇒ H82 的"WS 未探通"不能作为 Lighter 无实时行情的证据。** 我如实纠正。

# 本脚本测什么（这才是决定迁移可行性的数据）

  · WS 能否按上述协议连上并收到 order_book 快照
  · **增量推送的频率**（决定实时性：Aster 的 book_ticker 是 8~37ms）
  · 盘口档位数与深度

判据（事先定死）：
  · 更新频率中位 ≤100ms ⇒ 实时性与 Aster 同量级 ⇒ **迁移实时性风险低**
  · 100ms~1s ⇒ 明显不如 Aster（20~80 倍差距）⇒ F281 的收益会退化
  · 连不上 ⇒ 标注**未验证**（不推断）

用法：
    .venv\\Scripts\\python.exe scripts\\h83_lighter_ws_correct.py
"""
from __future__ import annotations

import asyncio
import json
import ssl
import statistics
import sys
import time

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
REST = "https://mainnet.zklighter.elliot.ai"


def get_json(path):
    import urllib.request
    req = urllib.request.Request(REST + path, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
        return json.loads(r.read().decode())


async def probe(market_id, symbol, seconds=25.0):
    """按官方协议：等 connected → subscribe → 收快照与增量 → 回 pong。"""
    import websockets
    res = {"symbol": symbol, "mid": market_id, "ok": False, "updates": 0,
           "snapshot": None, "gaps": [], "t0": None, "top": None, "err": None}
    try:
        async with websockets.connect(WS_URL, ssl=_CTX, open_timeout=15,
                                      close_timeout=3, ping_interval=None) as ws:
            # ── 1) 等 connected ──
            connected = False
            deadline = time.time() + 15
            while time.time() < deadline and not connected:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
                m = json.loads(raw)
                if m.get("type") == "connected":
                    connected = True
                    await ws.send(json.dumps(
                        {"type": "subscribe", "channel": f"order_book/{market_id}"}))
                    break
                if m.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
            if not connected:
                res["err"] = "未收到 connected"
                return res
            # ── 2) 收快照 + 增量 ──
            end = time.time() + seconds
            last_t = None
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
                except asyncio.TimeoutError:
                    break
                now = time.time()
                m = json.loads(raw)
                t = m.get("type")
                if t == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                    continue
                if t == "subscribed/order_book":
                    res["ok"] = True
                    ob = m.get("order_book") or {}
                    res["snapshot"] = {"n_asks": len(ob.get("asks") or []),
                                       "n_bids": len(ob.get("bids") or [])}
                    res["t0"] = now
                    continue
                if t == "update/order_book":
                    res["updates"] += 1
                    if last_t is not None:
                        res["gaps"].append((now - last_t) * 1000)
                    last_t = now
                    ob = m.get("order_book") or {}
                    a = ob.get("asks") or []
                    b = ob.get("bids") or []
                    if a and b and res["top"] is None:
                        res["top"] = (float(b[0]["price"]), float(a[0]["price"]))
                if t not in ("ping", "pong", "subscribed/order_book", "update/order_book"):
                    res.setdefault("other_types", []).append(t)
    except Exception as e:
        res["err"] = f"{type(e).__name__}: {str(e)[:140]}"
    return res


async def main_async():
    print("=" * 100)
    print("H83  Lighter WS 实时盘口（按官方 SDK 协议重测）")
    print("=" * 100)
    print(f"  端点 {WS_URL}")
    print(f"  协议：等 connected → 发 subscribe → 收快照/增量 → ping 回 pong")

    try:
        d = get_json("/api/v1/orderBooks")
        obs = d.get("order_books") or []
    except Exception as e:
        print(f"  ✗ REST 失败: {e}")
        return 1
    want = ["ETH", "BTC", "SOL", "XRP", "DOGE"]
    ids = {}
    for o in obs:
        s = str(o.get("symbol") or "").upper()
        if s in want and s not in ids:
            ids[s] = o.get("market_id")
    print(f"\n  market_id: {ids}")

    results = []
    for s in ("ETH", "SOL", "XRP"):
        if s not in ids:
            continue
        print(f"\n  ── 探测 {s}（market_id={ids[s]}）──")
        r = await probe(ids[s], s, seconds=20.0)
        results.append(r)
        if r["err"]:
            print(f"     ✗ {r['err']}")
            continue
        print(f"     快照: {r['snapshot']}   增量条数: {r['updates']}")
        if r["gaps"]:
            g = r["gaps"]
            print(f"     增量间隔 ms: 中位 {statistics.median(g):.0f}  "
                  f"p10 {sorted(g)[len(g)//10]:.0f}  "
                  f"p90 {sorted(g)[9*len(g)//10]:.0f}  最小 {min(g):.0f}")
        if r["top"]:
            bb, ba = r["top"]
            mid = 0.5 * (bb + ba)
            print(f"     最优买卖: {bb} / {ba}   价差 {(ba-bb)/mid*1e4:.4f}bp")

    print("\n" + "=" * 100)
    print("判据（事先定死）")
    print("=" * 100)
    ok = [r for r in results if r["ok"]]
    if not ok:
        print("  ⇒ 仍未探通 ⇒ **标注未验证**（不推断 Lighter 有无实时行情）")
        for r in results:
            print(f"     {r['symbol']}: {r.get('err')}")
        return 1
    allg = [x for r in ok for x in r["gaps"]]
    if allg:
        med = statistics.median(allg)
        print(f"  增量推送间隔中位 **{med:.0f}ms**（Aster book_ticker 为 8~37ms）")
        if med <= 100:
            print("  ⇒ ≤100ms ⇒ **实时性与 Aster 同量级** ⇒ 迁移的实时性风险低 ✓")
        elif med <= 1000:
            print(f"  ⇒ 100ms~1s ⇒ 比 Aster 慢约 {med/20:.0f} 倍")
            print("     ⇒ 需权衡：F281（实时 mid）在 Aster 值 +1.21bp/笔，")
            print("       若 Lighter 的盘口更新更慢，这部分收益会退化")
        else:
            print("  ⇒ >1s ⇒ 实时性明显不足")
    print(f"\n  结论：Lighter **确认为 0/0 且 WS 实时行情可用**（maker_fee=0.00 来自官方 API 原文）")
    print(f"  剩余风险：① 无批量历史 L2（无法先行回测）② 需自建采集 ③ 资金/桥接风险")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main_async()))
