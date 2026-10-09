# -*- coding: utf-8 -*-
"""[h764 2026-10-03] 第一级:全交易所 24h 成交量前 N 的滚动筛选(动态宇宙)。

用户设计(两级漏斗 + 名单时时变化):
  ① 粗筛:全交易所(611 个永续)按 **24h 成交额** 排名 → 取前 N(默认 20);
  ② 精挑:在这 20 个里面跑现有的价差/可行性/种子/淘汰过滤器 → 得到交易宇宙。
  名单每 15 分钟刷新一次(不是固定名单)。

数据源:Aster REST `/fapi/v1/ticker/24hr`(611 币,含 quoteVolume)。
  为什么不用本地 klines:实测 crypto_klines 的 asterdex 数据停在 01-22(几个月前),
  不能作为成交量来源。
输出:data/vol_top20.json(供选择器第②级与采集名单动态订阅使用)。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# ── [整顿轮·T14 2026-10-05] 补 `sys.path`：本脚本被**计划任务直接执行**
# （`DSH_MM_VOL_SCREEN` → `run-quiet.vbs` → `python scripts\h764_volume_screen.py`），
# 不是经 `importlib` 载入。直接执行时 `sys.path[0]` 是 `scripts\`，
# 找不到仓库根的 `backend` 包 ⇒ 第 118 行 `from backend.services... import`
# 抛 `ModuleNotFoundError: No module named 'backend'` ⇒ 整脚本退出码 1。
#
# 事故后果（实测）：`data/vol_top20.json` **停在 2026-10-04 18:03**，
# 计划任务每 5 分钟"成功"跑一次却什么都不写（`.vbs` 包装器把退出码吞了，
# `LastTaskResult` 仍报 0）⇒ **选币/跳空过滤/按币振幅止损全部用 24 小时前的数据**
# ⇒ `gap_excluded` 名单过期（实测 `GTC`、`MAGMA`、`MOVR` 等仍在成交，
# 而它们在被硬排除的名单里）。
sys.path.insert(0, str(ROOT))
TICKER_URL = "https://fapi.asterdex.com/fapi/v1/ticker/24hr"
BOOK_URL = "https://fapi.asterdex.com/fapi/v1/ticker/bookTicker"
PROXY = os.environ.get("MARKET_DATA_HTTP_PROXY") or "http://127.0.0.1:1080"
# [h764 参数依据] 实测(10-03 20:2x)全交易所 589 个 USDT 永续的"成交额排名 vs 价差"
# 是**单调反向**关系:
#   排名 1-20: 4 个 ≥5bp | 21-40: 7 个 | 41-60: 11 个 | 61-100: 30 个
#   101-150: 45 个(量 <$0.2M) | 151-250: 93 个(量 <$0.1M,太薄)
# ⇒ N=20 太小(只有 4 个可做);N=100 是"有量($0.5M+)又有价差"的甜区上界。
TOP_N = int(os.environ.get("MM_VOL_TOP_N", "100"))
SPREAD_WIDE_BP = float(os.environ.get("MM_SCREEN_WIDE_BP", "5.0"))
# 结构性排除:稳定币/封装币/杠杆代币(与选择器的 _excluded 口径一致)
EXCLUDE_SUFFIX = ("USD1", "USDC", "BUSD", "DAI", "FDUSD", "TUSD")
EXCLUDE_BARE = {"BTCDOM", "DEFI", "1000BONK"}


def _get(url: str):
    import requests
    for kw in ({"proxies": {"http": PROXY, "https": PROXY}, "timeout": 15},
               {"timeout": 15}):
        try:
            r = requests.get(url, **kw)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


def main() -> int:
    rows = _get(TICKER_URL)
    if not rows:
        print("✗ 取 24h ticker 失败")
        return 1
    # [h764] 同时取实时最优买卖 ⇒ 第一级输出就带价差(第二级可直接用)
    books = {}
    for x in (_get(BOOK_URL) or []):
        try:
            b, a = float(x.get("bidPrice") or 0), float(x.get("askPrice") or 0)
            if b > 0 and a > b:
                books[str(x.get("symbol"))] = (b, a)
        except Exception:
            continue

    cands = []
    for x in rows:
        sym = str(x.get("symbol") or "")
        if not sym.endswith("USDT"):
            continue
        bare = sym[:-4].upper()
        if bare.endswith(EXCLUDE_SUFFIX) or bare in EXCLUDE_BARE:
            continue
        try:
            qv = float(x.get("quoteVolume") or 0.0)
        except Exception:
            continue
        if qv <= 0:
            continue
        b, a = books.get(sym, (0.0, 0.0))
        full_bp = ((a - b) / ((a + b) / 2.0) * 1e4) if (b > 0 and a > b) else 0.0
        # [h790 2026-10-04] **进场前跳空过滤**(24h 解剖 V2/V3 + 08:5x 又现 −609bp
        # 深跳空):24h 高低价区间 > 15%(如 low 0.5→high 0.6 = 20%)⇒ 该币在 24h 内
        # 经历过跳空级行情 ⇒ 直接排除(崩盘熔断是后手,进不来才是治本)。
        try:
            _hi = float(x.get("highPrice") or 0.0)
            _lo = float(x.get("lowPrice") or 0.0)
            _range_pct = ((_hi - _lo) / _lo * 100.0) if _lo > 0 else 0.0
        except Exception:
            _range_pct = 0.0
        _gap_prone = _range_pct > 15.0
        cands.append({"symbol": bare, "quote_volume_usd": round(qv, 1),
                      "last_price": float(x.get("lastPrice") or 0.0),
                      "change_pct_24h": float(x.get("priceChangePercent") or 0.0),
                      "trades_24h": int(x.get("count") or 0),
                      "range_pct_24h": round(_range_pct, 2),
                      "gap_prone": _gap_prone,
                      "spread_bp": round(full_bp, 3)})
    cands.sort(key=lambda c: -c["quote_volume_usd"])
    top = cands[:TOP_N]
    # [h814 2026-10-04 模型驱动] h812 稳健验证过的{币×时限}(data/flow_edge_robust.json)
    # 豁免跳空过滤 —— 它们在 300~900s 上有稳健 edge(+18~60bp),而跳空过滤是为
    # "被动做市接刀"设的;主动顺势交易靠 SL/持仓时限控风险,$78 硬上限仍在。
    _model_ok = set()
    try:
        _rb = json.loads((ROOT / "data" / "flow_edge_robust.json").read_text(encoding="utf-8"))
        for _r in (_rb.get("results") or []):
            if float(_r.get("median_edge_bp") or 0) > 5.0 \
                    and int(_r.get("positive_runs") or 0) * 2 > int(_r.get("n_runs") or 1):
                _model_ok.add(str(_r["symbol"]).upper())
    except Exception:
        pass
    wide = [c["symbol"] for c in top if c["spread_bp"] >= SPREAD_WIDE_BP
            and (not c["gap_prone"] or c["symbol"] in _model_ok)]
    gap_out = [c["symbol"] for c in top if c["gap_prone"] and c["symbol"] not in _model_ok]
    # 主动流观察池：按成交额排，价差宽于止损一半的排除。wide 仍只给做市回退用。
    from backend.services.market_maker.flow_universe import ranked_watch_pool
    watch_pool = ranked_watch_pool(cands)
    out = {"ts": time.time(), "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "source": TICKER_URL, "universe_size": len(cands), "top_n": TOP_N,
           "spread_wide_bp": SPREAD_WIDE_BP,
           "gap_filter_pct": 15.0, "gap_excluded": gap_out,
           "top": [c["symbol"] for c in top], "wide": wide,
           "watch_pool": watch_pool, "detail": top}
    (ROOT / "data" / "vol_top20.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== 全交易所 24h 成交额前 {TOP_N}(共 {len(cands)} 个 USDT 永续)==")
    print(f"  主动流观察池 {len(watch_pool)} 个（成交额优先，价差过宽已排除）")
    print(f"  跳空币(24h 振幅 >15%)已排除 {len(gap_out)} 个: {gap_out[:12]}")
    for i, c in enumerate(top[:25], 1):
        tag = "✓" if c["symbol"] in wide else ("✗跳空" if c["symbol"] in gap_out else " ")
        print(f"  {i:>3}. {tag} {c['symbol']:<12} ${c['quote_volume_usd']/1e6:>7.2f}M  "
              f"价差 {c['spread_bp']:>6.2f}bp  24h {c['change_pct_24h']:+6.2f}%  "
              f"振幅 {c['range_pct_24h']:>5.1f}%  笔数 {c['trades_24h']}")
    print("  ✓ 已写 data/vol_top20.json(含 wide/gap_excluded 子集)")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
