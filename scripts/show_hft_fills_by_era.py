"""按「配置时代」分离统计 HFT 成交（跨时代不可求和）。

## 为什么必须分离

`lane_ledger` 是**不可篡改的历史事实**，它同时包含多个参数时代的成交。
把不同时代混在一起求和会得出误导性结论——本仓库已有明确纪律与先例
（曾因跨时代求和得出假的 93× 敞口告警）。

当前已知的时代分界：
    ≤ 18:43  旧口径 `w_base_bp=30`（挂盘口外 40-50 倍，成交率 0.05%）
    > 18:43  新口径 `w_base_bp=1.5`（H16 扫描结论）

那笔 ARB `price_bp=-74.63` 就是旧时代的产物，它会主导整个汇总的符号。
"""
from __future__ import annotations

import json
import sys
import urllib.request

# 参数切换时刻（本机时区 = 账本 ts 的时区）
CUTOVER = "2026-09-20 18:43"


def get(ep: str):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{ep}", timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def block(title: str, items: list) -> None:
    if not items:
        print(f"\n{title}: 无成交")
        return
    n = len(items)
    s = sum(i["spread_bp"] for i in items)
    p = sum(i["price_bp"] for i in items)
    f = sum(i["fee_bp"] for i in items)
    net = sum(i["net_bp"] for i in items)
    usd = sum(i["net_usd"] for i in items)
    print(f"\n{title}")
    print(f"  笔数 {n}   名义 {sum(i['notional_usd'] for i in items):.2f} USD")
    print(f"  每笔均值: 价差 {s/n:+.2f}  逆选择 {p/n:+.2f}  费率 {f/n:+.2f}  "
          f"净 {net/n:+.2f}bp")
    print(f"  合计:     net {net:+.2f}bp = {usd:+.4f} USD")
    wins = sum(1 for i in items if i["net_bp"] > 0)
    print(f"  胜率 {wins}/{n} = {wins/n:.0%}")
    zero_price = sum(1 for i in items if abs(i["price_bp"]) < 1e-9)
    print(f"  零逆选择 {zero_price}/{n}（价差完整落袋）")


def main() -> int:
    d = get("/api/hft/fills?limit=300&hours=48")
    items = d["items"]
    old = [i for i in items if (i["ts"] or "") < CUTOVER]
    new = [i for i in items if (i["ts"] or "") >= CUTOVER]
    print(f"账本近 {d['hours']}h 共 {len(items)} 笔   分界 {CUTOVER}")
    block("【旧口径】w_base_bp=30（挂盘口外 40-50 倍）", old)
    block("【新口径】w_base_bp=1.5（H16 扫描结论）", new)
    print("\n⚠️ 两段不可相加——旧时代那笔 ARB price_bp=-74.63 会主导整体符号。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
