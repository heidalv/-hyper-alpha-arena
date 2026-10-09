"""H55：引擎的挂宽是"绝对 bp"，而各币的真实价差相差 74 倍 —— 这就是挂单打不到人的根因。

# 根因

引擎（`backend/services/market_maker/core.py`）的挂宽是**相对中价的绝对 bp**：

    w_base_bp = 5.0（.env MM_W_BASE_BP=5）   min_width_bp = 3.0
    实盘 status: avg_width_bp = 1.425 / 1.41，avg_base_bp = 1.546

而 H53b 实测的真实价差（book_ticker 与 20 档 depth 快照交叉核对一致）：

| 币   | 价差 p50    | 折美元            | 引擎 1.425bp 相当于 |
|------|-------------|-------------------|---------------------|
| BTC  | 0.0124bp    | $0.10 / $80,855   | **115 倍半价差**    |
| ETH  | 0.0388bp    | $0.01 / $2,605    | 37 倍               |
| SOL  | 0.9247bp    | $0.01 / $108      | 1.5 倍              |

⇒ **同一个 `w_base_bp=1.425` 在 BTC 上是"挂在天外"（115 倍半价差，永远打不到），
   在 SOL 上却是"贴着盘口"（1.5 倍半价差，能被逆选择屠杀）。**

这解释了用户报的两个现象：
  · "完全没有交易" —— BTC/ETH 上挂单离盘口 37~115 倍半价差
  · 而 status 显示 fills=1596 / fills_per_hour=313 —— 补丁级的成交来自宽挂单被大行情穿越，
    每次穿越都是**逆选择**（价格已经走了 1.425bp 才碰到我们）⇒ **equity 285.50 / 300**

# 正确参数化（H54 的结论同样适用于此）

挂宽必须**以半价差为单位**（或"价差的倍数"），因为：
  · 价差是**唯一**把"场地 tick 粒度 / 流动性 / 波动"三者都编码进去的自变量
  · 0.05bp 在 BTC 上是 4 倍半价差（穿越），在 SOL 上是 0.05 倍（几乎贴中点）—— **同一个数字两种物理**
  ⇒ 绝对 bp 无法同时适配，**任何**跨币固定 bp 都必然在一半币上错。

# 本脚本产出

对车道实际挂单的每个币，测**真实价差分布**，并算出引擎当前宽度相当于几倍半价差，
给出建议的 `spread_mult` 区间（挂宽 = 该倍数 × 半价差）。

用法：
    .venv\\Scripts\\python.exe scripts\\h55_spread_scaled_width.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "research_l1" / "out" / "h55_spread_scaled_width.json"
ENGINE_W_BP = float(os.getenv("MM_W_BASE_BP", "5.0"))
ENGINE_MIN_BP = 3.0
LIVE_AVG_BP = 1.425   # 实盘 status 的 avg_width_bp（bid 侧）


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

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 车道实际挂的币 = 近期 book_ticker 里出现过的（按持仓/成交活跃度取前 N）
    cur.execute(
        "SELECT symbol, count(*) n FROM asterdex_book_ticker"
        " WHERE event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000"
        " GROUP BY symbol ORDER BY n DESC"
    )
    syms = [(r["symbol"], r["n"]) for r in cur.fetchall()]
    print("=" * 108)
    print("H55  真实价差 × 引擎挂宽 —— 为什么挂单打不到人（以及为什么 SOL 上会被屠杀）")
    print("=" * 108)
    print(f"  引擎 MM_W_BASE_BP={ENGINE_W_BP}  min_width_bp={ENGINE_MIN_BP}"
          f"  实盘 avg_width_bp≈{LIVE_AVG_BP}")
    print(f"  场地：asterdex  近 2h 有盘口数据的币 {len(syms)} 个\n")

    print(f"{'币':<12} {'盘口行数':>10} {'价差p50 bp':>11} {'价差p95 bp':>11} "
          f"{'半价差 p50 bp':>13} {'引擎宽/半价差':>13} {'能打到?':>9}")
    print("-" * 108)

    rowsout = []
    for s, n in syms:
        cur.execute(
            "SELECT bid_px::float b, ask_px::float a FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000"
            " ORDER BY event_ts_ms",
            (s,),
        )
        d = cur.fetchall()
        if len(d) < 500:
            continue
        b = np.array([r["b"] for r in d])
        a = np.array([r["a"] for r in d])
        ok = (b > 0) & (a > b)
        if ok.sum() < 500:
            continue
        b, a = b[ok], a[ok]
        mid = 0.5 * (b + a)
        sp = (a - b) / mid * 1e4
        p50 = float(np.percentile(sp, 50))
        p95 = float(np.percentile(sp, 95))
        half = p50 / 2.0
        mult = LIVE_AVG_BP / half if half > 0 else float("inf")
        # 判读：挂宽远大于半价差 ⇒ 打不到；远小于 1 倍 ⇒ 会被逆选择
        if mult > 20:
            verdict = "✗打不到"
        elif mult > 4:
            verdict = "~偏外"
        elif mult >= 0.5:
            verdict = "✓可竞争"
        else:
            verdict = "✗太贴"
        rowsout.append({"symbol": s, "spread_p50_bp": round(p50, 4),
                        "spread_p95_bp": round(p95, 4), "half_p50_bp": round(half, 4),
                        "engine_w_over_half": round(mult, 2), "verdict": verdict})
        print(f"{s:<12} {int(ok.sum()):>10,} {p50:>11.4f} {p95:>11.4f} "
              f"{half:>13.4f} {mult:>12.1f}x {verdict:>9}")

    cn.close()
    if not rowsout:
        print("无数据")
        return 1

    sps = np.array([r["spread_p50_bp"] for r in rowsout])
    print("\n" + "=" * 108)
    print("汇总")
    print("=" * 108)
    print(f"  价差 p50 跨度：{sps.min():.4f}bp（{rowsout[int(sps.argmin())]['symbol']}）"
          f" ~ {sps.max():.4f}bp（{rowsout[int(sps.argmax())]['symbol']}）"
          f"  ⇒ **{sps.max()/max(sps.min(),1e-9):.0f} 倍**")
    n_ok = sum(1 for r in rowsout if r["verdict"].startswith("✓"))
    n_out = sum(1 for r in rowsout if "打不到" in r["verdict"] or "偏外" in r["verdict"])
    n_tight = sum(1 for r in rowsout if "太贴" in r["verdict"])
    print(f"  可竞争 {n_ok} 个 · 太靠外（打不到/偏外）{n_out} 个 · 太贴（被逆选择）{n_tight} 个")
    print(f"\n  ⇒ **同一个绝对 bp 宽度不可能同时适配跨度为 {sps.max()/max(sps.min(),1e-9):.0f} 倍的价差。**")
    print(f"  ⇒ 必须改成 **挂宽 = spread_mult × 半价差**。")
    print(f"\n  建议 spread_mult（挂宽 / 半价差）：")
    print(f"     · Albers Table 1 的最优格是 QP≈0（队首）—— 队首需**改善最优价**，")
    print(f"       即 spread_mult < 1（报价进到价差内），此时我们**就是**最优价 ⇒ 成交有保障")
    print(f"     · 但 H54 会给出在 Aster 极窄价差下 spread_mult 的下界（穿越风险）")
    print(f"     · 起步建议：**spread_mult = 0.9**（略进价差内，不穿越），按币自适应")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"engine_w_base_bp": ENGINE_W_BP, "engine_min_bp": ENGINE_MIN_BP,
         "live_avg_width_bp": LIVE_AVG_BP, "symbols": rowsout},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H55] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
