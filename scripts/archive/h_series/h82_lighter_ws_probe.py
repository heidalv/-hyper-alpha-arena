"""H82：Lighter 的 WS 实时盘口是否可用 —— 决定迁移可行性的关键一问。

# 为什么这一步是决定性的

H81 实测：
  · Lighter 可达，**官方 API 原文 `maker_fee: "0.00"` / `taker_fee: "0.0000"`** ✓
  · BTC $444M/日、ETH $263M/日、SOL $36.7M/日 ⇒ **流动性不是问题**
  · 但 **REST RTT 中位 657ms**

657ms 对 15s 决策节奏**够用**。但本项目刚修完 F281
（引擎 mid 从 15 秒陈旧的快照切到 8~37ms 的实时盘口，值 **+1.21bp/笔**）
⇒ **若 Lighter 拿不到实时 L2，等于把 F281 的收益还回去。**

所以关键问题是：**Lighter 有没有 WebSocket 实时盘口推送？**
  · 有 ⇒ REST 的 657ms 无关紧要，迁移可行性大幅提升
  · 没有（只有 REST 轮询）⇒ 657ms 轮询 ≈ 每 0.66 秒一次盘口，
    比 Aster 的 8~37ms 差 20~80 倍，**F281 的成果会退化**

# 判据（事先定死）

  · WS 可连且能收到 orderbook 推送，且推送间隔 ≤100ms ⇒ **实时性达标，值得影子验证**
  · WS 可连但推送间隔 >1s ⇒ 实时性不足，需重新评估
  · WS 不可连 ⇒ 只能用 REST 轮询 ⇒ **不迁移**

用法：
    .venv\\Scripts\\python.exe scripts\\h82_lighter_ws_probe.py
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

WS_CANDIDATES = [
    "wss://mainnet.zklighter.elliot.ai/stream",
    "wss://mainnet.zklighter.elliot.ai/ws",
    "wss://api.zklighter.elliot.ai/stream",
]

# Lighter 公开文档里的订阅格式（market_id: ETH=0/1, BTC=?）——用 order_book 订阅试
SUB_TEMPLATES = [
    {"type": "subscribe", "channel": "order_book/{mid}"},
    {"type": "subscribe", "channel": "order_book", "market_id": "{mid}"},
    {"type": "subscribe", "channel": "ticker/{mid}"},
]


def _imports():
    try:
        import websockets  # noqa: F401
        return "websockets"
    except Exception:
        pass
    try:
        import wsproto  # noqa: F401
        return "wsproto"
    except Exception:
        pass
    return None


async def try_ws(url, sub, seconds=12.0):
    """连一个 WS，发订阅，统计收到多少条、以及消息间隔。"""
    import websockets
    t0 = time.time()
    n = 0
    stamps = []
    first = None
    try:
        async with websockets.connect(url, ssl=_CTX, open_timeout=12,
                                      close_timeout=3) as ws:
            await ws.send(json.dumps(sub))
            deadline = time.time() + seconds
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
                except asyncio.TimeoutError:
                    break
                n += 1
                now = time.time()
                stamps.append(now)
                if first is None:
                    first = msg[:220]
            return {"ok": True, "n": n, "stamps": stamps, "first": first,
                    "ms": (time.time() - t0) * 1000}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {str(e)[:140]}",
                "ms": (time.time() - t0) * 1000}


async def main_async():
    print("=" * 100)
    print("H82  Lighter WebSocket 实时盘口探测（决定迁移可行性）")
    print("=" * 100)
    lib = _imports()
    print(f"\n  WS 客户端库: {lib or '**未安装**（需要 websockets）'}")
    if not lib or lib != "websockets":
        print("  ⇒ 无法直接测 WS。改用 REST 轮询间隔间接推断（见下）")
        return await rest_poll_fallback()

    # 先拿 market_id（ETH 与 BTC）
    import urllib.request
    req = urllib.request.Request("https://mainnet.zklighter.elliot.ai/api/v1/orderBooks",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
        d = json.loads(r.read().decode())
    obs = d.get("order_books") or []
    ids = {}
    for o in obs:
        s = str(o.get("symbol") or "").upper()
        if s in ("ETH", "BTC", "SOL", "XRP", "DOGE"):
            ids.setdefault(s, o.get("market_id"))
    print(f"  取到 market_id: {ids}")

    target = ids.get("ETH") or ids.get("BTC") or (obs[0].get("market_id") if obs else 0)

    best = None
    for url in WS_CANDIDATES:
        for tpl in SUB_TEMPLATES:
            sub = json.loads(json.dumps(tpl).replace("{mid}", str(target)))
            r = await try_ws(url, sub, seconds=10.0)
            status = "✓" if (r["ok"] and r["n"] > 0) else "✗"
            print(f"\n  {status} {url}")
            print(f"      sub={sub}")
            if r["ok"]:
                print(f"      收到 {r['n']} 条消息  用时 {r['ms']:.0f}ms")
                if r["n"]:
                    print(f"      首条: {r['first']}")
                    if len(r["stamps"]) > 2:
                        gaps = [ (r["stamps"][i+1]-r["stamps"][i])*1000
                                 for i in range(len(r["stamps"])-1) ]
                        print(f"      消息间隔 中位 {statistics.median(gaps):.0f}ms  "
                              f"最小 {min(gaps):.0f}ms  最大 {max(gaps):.0f}ms")
                    best = r
            else:
                print(f"      err: {r['err']}")
            if best:
                break
        if best:
            break

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    if best:
        gaps = [ (best["stamps"][i+1]-best["stamps"][i])*1000
                 for i in range(len(best["stamps"])-1) ]
        med = statistics.median(gaps) if gaps else float("inf")
        print(f"  WS 可用，推送间隔中位 {med:.0f}ms")
        if med <= 100:
            print("  ⇒ ≤100ms ⇒ **实时性达标** ⇒ 值得做影子验证 ✓")
        elif med <= 1000:
            print("  ⇒ 100ms~1s ⇒ 实时性一般，不如 Aster 的 8~37ms，需权衡")
        else:
            print("  ⇒ >1s ⇒ 实时性不足")
    else:
        print("  ⇒ **WS 未探通**（可能是订阅格式不对，或端点不同）")
        print("     这**不等于** Lighter 没有 WS —— 只说明本脚本的探测方式没找到。")
        print("     诚实标注：**未验证**，需查官方文档确认端点与订阅格式。")
    return 0


async def rest_poll_fallback():
    """没有 websockets 库时：测 REST 轮询能达到的最快节奏（间接推断）。"""
    import urllib.request
    print("\n  ── REST 轮询回退测试（连发 10 次 orderBooks，量最快节奏）──")
    lat = []
    for i in range(10):
        t0 = time.time()
        try:
            req = urllib.request.Request(
                "https://mainnet.zklighter.elliot.ai/api/v1/orderBooks",
                headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
                r.read(200)
            lat.append((time.time() - t0) * 1000)
        except Exception as e:
            print(f"    第 {i+1} 次失败: {e}")
    if lat:
        print(f"    最快 {min(lat):.0f}ms  中位 {statistics.median(lat):.0f}ms")
        print(f"    ⇒ 串行轮询最快约每 {min(lat):.0f}ms 一次盘口")
        print(f"    ⇒ 对比 Aster 的 8~37ms：慢约 {min(lat)/20:.0f} 倍")
        if min(lat) > 300:
            print("    ⇒ **实时性显著不足** ⇒ 按 H82 判据：不迁移")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main_async()))
