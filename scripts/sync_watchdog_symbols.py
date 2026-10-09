"""同步看门狗脚本里的两个 symbol 名单（避免手工编辑 PowerShell 出错）。

## 为什么用脚本改而不是手改

`aster-depth-watchdog.ps1` 的两个名单参数是**长单行字符串**，手工编辑极易漏币；
而漏币的后果已经实测过两次（8 币名单丢掉 5 个币、13 币名单丢掉 19 个币的 book）。
⇒ 名单由本脚本从**单一权威源**（下方常量）写入，并做长度断言。

用法：
    python sync_watchdog_symbols.py             # 写入
    python sync_watchdog_symbols.py --check     # 只校验，不写
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

WATCHDOG = Path(__file__).resolve().parent / "aster-depth-watchdog.ps1"

# ── 单一权威源：改这里，再跑本脚本 ──────────────────────────────
# book/trades 订阅名单 = 完整采集宇宙
#
# [H136 2026-09-21] 加 USD1 合约。官方费率（docs.asterdex.com/trading/perpetuals/
# fees-and-specs/fees.md）：USDT 永续 taker 0.04%（4.0bp）、**USD1 永续 taker
# 0.005%（0.5bp）** ⇒ 便宜 8 倍。我们强平腿每周期成本 ≈10.67bp 里 taker 占 41%
# （H105/H126 实测）⇒ 换 USD1 每周期可省 `(4.36−0.86)bp × 该币强平率`。
# 先采数据再用实测决定：SOLUSD1 是唯一"候选里有点差 + 有 USD1 版本"的币
# （24h $1.87M vs SOLUSDT $90.6M、点差 3.58bp vs 0.895bp ⇒ 有利有弊，书薄但排队靠前）。
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "LINKUSDT", "ADAUSDT", "AVAXUSDT", "SUIUSDT", "NEARUSDT", "ARBUSDT",
    "ENAUSDT", "WLDUSDT", "ONDOUSDT", "ASTERUSDT", "HYPEUSDT", "ZECUSDT",
    "XLMUSDT", "TAOUSDT", "UNIUSDT", "SEIUSDT", "PENDLEUSDT", "1000PEPEUSDT",
    "LTCUSDT", "XMRUSDT", "WLFIUSDT", "1000SHIBUSDT", "AAVEUSDT",
    "VIRTUALUSDT", "PUMPUSDT", "LITUSDT",
    # [H136] USD1 合约（taker 0.5bp，便宜 8 倍）
    "SOLUSD1", "BTCUSD1", "ETHUSD1",
]
# 20 档深度订阅名单（较重，按做市评分选出）
DEPTH_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "UNIUSDT", "ASTERUSDT", "HYPEUSDT", "ZECUSDT", "ONDOUSDT", "ARBUSDT",
    "SEIUSDT",
    # 2026-09-20 扩容：近期点差 ≥ 5bp 且盘口更新活跃（h15 评分）
    "VIRTUALUSDT", "PENDLEUSDT", "1000SHIBUSDT", "PUMPUSDT", "NEARUSDT",
    "ADAUSDT", "ENAUSDT", "SUIUSDT", "AAVEUSDT", "WLDUSDT",
    # [H136 2026-09-21] 补上"p25 点差合格但此前没采深度"的币 ——
    # H131 实测：这 4 个币点差在 [0.8,4.0]bp 内、只是**我们没采深度**
    # ⇒ 引擎无法为它们报价。不是它们不合格，是采集列表太短。
    "TAOUSDT", "WLFIUSDT", "1000PEPEUSDT", "XMRUSDT",
    # [H136] USD1 合约（唯一可做市的 USD1 标的）
    "SOLUSD1",
]

RE_SYMBOLS = re.compile(r"\[string\]\$Symbols = '([^']*)',")
RE_DEPTH = re.compile(r"\[string\]\$DepthSymbols = '([^']*)',")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写入")
    args = ap.parse_args()

    if not WATCHDOG.is_file():
        print(f"ERR watchdog not found: {WATCHDOG}")
        return 2
    src = WATCHDOG.read_text(encoding="utf-8-sig")

    m_sym = RE_SYMBOLS.search(src)
    m_dep = RE_DEPTH.search(src)
    if not m_sym or not m_dep:
        print("ERR 未能在看门狗脚本中定位 $Symbols / $DepthSymbols 参数行")
        return 3

    cur_sym = [s for s in m_sym.group(1).split(",") if s]
    cur_dep = [s for s in m_dep.group(1).split(",") if s]
    want_sym = list(SYMBOLS)
    want_dep = list(DEPTH_SYMBOLS)

    print(f"当前: Symbols={len(cur_sym)}  DepthSymbols={len(cur_dep)}")
    print(f"目标: Symbols={len(want_sym)}  DepthSymbols={len(want_dep)}")

    # 断言：深度名单必须是订阅名单的子集（否则深度订阅会拿到未订阅的币）
    extra = set(want_dep) - set(want_sym)
    if extra:
        print(f"ERR 深度名单含未在订阅名单里的币: {sorted(extra)}")
        return 4

    if args.check:
        ok = (cur_sym == want_sym and cur_dep == want_dep)
        print("校验:", "一致 ✅" if ok else "不一致 ❌")
        if not ok:
            print("  订阅差集:", sorted(set(want_sym) ^ set(cur_sym)))
            print("  深度差集:", sorted(set(want_dep) ^ set(cur_dep)))
        return 0 if ok else 1

    new_src = RE_SYMBOLS.sub(lambda _: f"[string]$Symbols = '{','.join(want_sym)}',", src, count=1)
    new_src = RE_DEPTH.sub(lambda _: f"[string]$DepthSymbols = '{','.join(want_dep)}',", new_src, count=1)
    WATCHDOG.write_text(new_src, encoding="utf-8")
    print("已写入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
