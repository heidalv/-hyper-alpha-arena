"""H101：F291（持有 120s）有没有**隐藏成本**？—— 扩到 180s 之前的必要检查。

# 背景

F291（60→120s）的收益已确凿：**强平率 20.0% → 12.8%**（两个窗口一致）。
它的**代价**是"同时持仓变多 ⇒ 敞口变大 ⇒ 闸门更早触发"：
  `skip_counts` 里 `net_exposure` 从 47 涨到 **97**（本进程累计）

**但闸门触发次数本身不是成本** —— 它也可能是"正确的风险阻挡"。
真正的成本要问：

  ① **敞口有没有变大**（gross / net 的峰值与均值）
  ② **被挡掉的报价有没有转化为"少赚"**（fills/hour 是否下降）
  ③ **左尾有没有变肥**（单周期最亏）

# 为什么不能只看闸门次数

`net_exposure=97` 既可能是"敞口失控"的征兆，也可能是"引擎在正确阻止加仓"。
**区分方法：看敞口本身的量级，而不是看拦截次数。**

# 本脚本做什么

取 F291 前后**等长窗口**（各 ~10 分钟），对比：
  · 周期数、强平率
  · fills/小时（成交速率 —— 被挡的报价最终有没有减少成交）
  · 每周期完整口径（pnl+fee）
  · 每周期笔数、时长

判据（事先定死）：
  · 若"成交速率"显著下降（>30%）⇒ 扩到 180s 会进一步抑制成交 ⇒ **先别扩**
  · 若成交速率基本不变、强平率下降 ⇒ **可以扩到 180s**
  · 若左尾变肥 ⇒ 无论收益如何都要停下

用法：
    .venv\\Scripts\\python.exe scripts\\h101_f291_hidden_cost.py
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

import datetime
F291 = datetime.datetime(2026, 9, 21, 3, 19, 59)
NOTIONAL = 135.0


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h84_derive_episodes import derive, load

    print("=" * 96)
    print("H101  F291 有没有隐藏成本？（扩到 180s 前的检查）")
    print("=" * 96)

    eps = derive(load())
    span = time.time() - F291.timestamp()
    pre = [e for e in eps if F291.timestamp() - span <= (e.get("ts0") or 0) < F291.timestamp()]
    post = [e for e in eps if (e.get("ts0") or 0) >= F291.timestamp()]
    print(f"  F291 之后 {span/60:.1f} 分钟；对照窗取等长")

    # 账本：完整口径
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT created_at, action, amount_usd::float a,"
        "       metadata_json::jsonb->>'symbol' sym"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= %s",
        (F291 - datetime.timedelta(seconds=span + 60),))
    led = cur.fetchall()
    cn.close()
    by = defaultdict(float)
    byday = defaultdict(float)
    for r in led:
        by[(r["sym"], int(r["created_at"].timestamp()))] += r["a"]

    def ep_pnl(e):
        t = 0.0
        for x in {y for y in e["fillts"] if y}:
            for ds in (0, 1, -1, 2):
                k = (e["sym"], int(x) + ds)
                if k in by:
                    t += by[k]
                    break
        return t

    print(f"\n  {'指标':<26} {'F291 之前':>14} {'F291 之后':>14} {'变化':>12}")
    print("  " + "-" * 70)

    def blk(sub):
        if not sub:
            return None
        f = sum(1 for e in sub if e["flat"])
        vals = np.array([ep_pnl(e) for e in sub])
        d = np.array([e["dur_s"] for e in sub])
        n = np.array([e["n"] for e in sub])
        return {"n": len(sub), "flat_rate": f / len(sub),
                "fills_per_min": sum(n) / max(span / 60.0, 1e-9),
                "pnl_per_ep": vals.mean(), "pnl_sum": vals.sum(),
                "worst": vals.min(), "p10": float(np.percentile(vals, 10)),
                "dur_med": float(np.median(d)),
                "fills_per_ep": float(np.median(n))}

    a, b = blk(pre), blk(post)
    if not a or not b:
        print("  样本不足")
        return 0

    def line(lab, ka, kb, fmt="{:>14.4f}"):
        ch = kb - ka
        print(f"  {lab:<26} " + fmt.format(ka) + " " + fmt.format(kb)
              + f" {ch:>+12.4f}")

    print(f"  {'周期数':<26} {a['n']:>14d} {b['n']:>14d} {b['n']-a['n']:>+12d}")
    line("强平率", a["flat_rate"], b["flat_rate"])
    line("成交笔/分钟", a["fills_per_min"], b["fills_per_min"])
    line("每周期笔数(中位)", a["fills_per_ep"], b["fills_per_ep"])
    line("每周期完整口径 USD", a["pnl_per_ep"], b["pnl_per_ep"])
    line("合计 USD", a["pnl_sum"], b["pnl_sum"])
    line("最亏周期 USD", a["worst"], b["worst"])
    line("p10 周期 USD", a["p10"], b["p10"])
    line("时长中位 s", a["dur_med"], b["dur_med"])

    print("\n" + "=" * 96)
    print("判据")
    print("=" * 96)
    rate_chg = (b["fills_per_min"] - a["fills_per_min"]) / max(a["fills_per_min"], 1e-9)
    # ⚠️ 左尾方向：两个值都是负数，**更负 = 更差**。
    # 首版写成 `b < a * 1.5` ⇒ 负数下判断反了（把"改善"报成"变肥"）⇒ 已修正。
    tail_worse = b["worst"] < a["worst"] - 1e-9
    print(f"\n  成交速率变化 = {rate_chg*100:+.1f}%")
    print(f"  强平率变化   = {(b['flat_rate']-a['flat_rate'])*100:+.1f} pp")
    print(f"  最亏周期     = {a['worst']:+.4f} → {b['worst']:+.4f} USD"
          f"（{'**变差**' if tail_worse else '改善或持平'}）")
    # 真正该看的量：**单位时间盈亏**（放宽持有会降低成交速率，
    # 但只要每周期净额提升更多，单位时间盈亏仍改善 ✓）
    pnl_pm_a = a["pnl_sum"] / max(span / 60.0, 1e-9)
    pnl_pm_b = b["pnl_sum"] / max(span / 60.0, 1e-9)
    print(f"\n  **单位时间盈亏 USD/分钟** = {pnl_pm_a:+.5f} → {pnl_pm_b:+.5f}"
          f"（{pnl_pm_b-pnl_pm_a:+.5f}）")
    print(f"  （这才是决策量：成交速率降 {abs(rate_chg)*100:.0f}%，"
          f"但每周期净额升 {b['pnl_per_ep']-a['pnl_per_ep']:+.4f} USD）")

    if tail_worse:
        print("\n  ⇒ **左尾变差 ⇒ 停下**，无论收益如何")
    elif pnl_pm_b <= pnl_pm_a:
        print("\n  ⇒ 单位时间盈亏未改善 ⇒ **扩到 180s 无据** ⇒ 保持 120s")
    elif rate_chg < -0.30:
        print(f"\n  ⇒ 成交速率降幅 {abs(rate_chg)*100:.0f}% > 30% ⇒ 偏大")
        print("     ⇒ 扩到 180s 会进一步抑制成交 ⇒ **暂缓**，先多观察")
    else:
        print(f"\n  ⇒ 左尾未变差、单位时间盈亏改善、成交速率降幅"
              f" {abs(rate_chg)*100:.0f}% 在容忍内")
        print("     ⇒ **可扩到 180s**（保守步长），继续监控")
    print(f"\n  ⚠️ 对照窗仅 {span/60:.0f} 分钟、周期数 {a['n']}/{b['n']}"
          f" ⇒ 结论为**方向性**，不构成显著性判定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
