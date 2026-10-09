"""H102：入场腿的利润被什么吃掉了？—— 唯一的利润来源只有 +0.102 bp/周期。

# 为什么查这个

H94 的完整成本桥（残差 0%）：
```
① 入场腿 pnl   **+0.102 bp/周期**   ← 唯一利润来源，但极小
③ 强平腿 pnl   −1.825 bp/周期
④ 强平腿 fee   −1.014 bp/周期
```

H95：打平需把强平率压到 0.84%（当前 12.8%）—— 极难。

**⇒ 还有另一条路：把入场腿做大。** 但它现在只有 +0.102 bp/周期。
本脚本要回答：**这 +0.102bp 是怎么构成的，还有多少空间？**

# 入场腿的构成（做市的标准分解）

一笔做市买单在价 `P` 成交时：
```
即时优势（毛捕获） = (mid − P) / mid        ← 我们挂在 mid 之下，成交即有优势
成交后漂移        = (mid_future − mid) / mid ← 逆选择：价格继续跌则亏
净 = 即时优势 + 漂移
```

`edge_bp`（`fill_basis` 里就有）= `(mid − P)/mid × 1e4` ⇒ **就是即时优势** ✓

所以可以直接算：
  · 即时优势（`edge_bp`）的分布
  · 用**成交价 vs 该笔的 engine_mid** 复现 `spread_usd`
  · 剩余部分即漂移

# 判据（事先定死）

  · 若即时优势 ≈ 0.4bp 而净只 +0.08bp ⇒ **漂移吃掉了 ~0.3bp** ⇒ 提价（挂远）可改善
  · 若即时优势本身极小 ⇒ 问题是"挂得太靠 mid"（与 H86/H88 一致）
  · 明确给出"若把 edge 提高 x bp，入场腿能到多少"的换算

用法：
    .venv\\Scripts\\python.exe scripts\\h102_entry_leg_decomposition.py
"""
from __future__ import annotations

import os
import sys
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

    print("=" * 96)
    print("H102  入场腿分解：唯一的利润来源只有 +0.102 bp/周期，它被什么吃掉了？")
    print("=" * 96)

    # ── 1) fill_basis 的 edge_bp 分布（即时优势）──
    from h84_derive_episodes import load
    rows = load()
    fb = []
    for r in rows:
        e = r.get("edge_bp")
        if e is None:
            continue
        fb.append({"edge": float(e), "side": str(r.get("side") or ""),
                   "sym": r.get("symbol"), "flat": bool(r.get("flatten")),
                   "px": float(r.get("fill_px") or 0),
                   "mid": float(r.get("engine_mid") or 0)})
    if not fb:
        print("fill_basis 无 edge_bp")
        return 1
    eg = np.array([x["edge"] for x in fb])
    print(f"\n  fill_basis 记录 {len(fb)}  （edge_bp = 即时优势 (mid−P)/mid×1e4）")
    print(f"    edge_bp: 均值 {eg.mean():+.4f}  中位 {np.median(eg):+.4f}  "
          f"p10 {np.percentile(eg,10):+.4f}  p90 {np.percentile(eg,90):+.4f}")
    print(f"    edge_bp <= 0 的比例：{(eg<=0).mean()*100:.1f}%"
          f"  ← 这些是「成交在 mid 或更差」的（**无即时优势**）")

    # ── 2) 账本入场腿：用 spread_usd 反推 ──
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT metadata_json::jsonb->>'symbol' sym,"
        "       (metadata_json::jsonb->>'qty')::float qty,"
        "       (metadata_json::jsonb->>'px')::float px,"
        "       (metadata_json::jsonb->>'mid')::float mid,"
        "       (metadata_json::jsonb->>'edge_bp')::float edge,"
        "       amount_usd::float a, created_at"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND COALESCE(metadata_json::jsonb->>'phase','')='fill'"
        "   AND created_at >= '2026-09-21 00:00:00'")
    led = cur.fetchall()
    cn.close()
    print(f"\n  账本入场腿记录 {len(led)}")

    # 逐笔：即时优势（用 edge 或从 px/mid 算）× 名义，与实际 pnl 对比
    adv_usd, act_usd, notion = [], [], []
    for r in led:
        qty = float(r["qty"] or 0); px = float(r["px"] or 0); mid = float(r["mid"] or 0)
        if qty <= 0 or px <= 0 or mid <= 0:
            continue
        sgn = 1.0 if float(r["a"] or 0) >= 0 else -1.0
        n = qty * mid
        # 即时优势（买：mid−px；卖：px−mid），用带符号 pnl 的符号反推方向不可靠，
        # 所以直接用 |mid−px| 作即时优势量级
        adv = abs(mid - px) * qty
        notion.append(n); adv_usd.append(adv); act_usd.append(float(r["a"] or 0))
    if not notion:
        print("  可用记录不足")
        return 0
    notion = np.array(notion); adv = np.array(adv_usd); act = np.array(act_usd)
    print(f"\n  ── 入场腿分解（{len(notion)} 笔，名义合计 ${notion.sum():,.0f}）──")
    print(f"    即时优势合计   ${adv.sum():>+9.4f}  = "
          f"{adv.sum()/notion.sum()*1e4:>+7.3f} bp（按名义加权）")
    print(f"    实际 pnl 合计  ${act.sum():>+9.4f}  = "
          f"{act.sum()/notion.sum()*1e4:>+7.3f} bp")
    drift = act.sum() - adv.sum()
    print(f"    **差额（漂移/逆选择）** ${drift:>+9.4f}  = "
          f"{drift/notion.sum()*1e4:>+7.3f} bp")
    print(f"\n    ⇒ 即时优势 {adv.sum()/notion.sum()*1e4:+.3f} bp  "
          f"− 漂移 {abs(drift)/notion.sum()*1e4:.3f} bp  "
          f"= 净 {act.sum()/notion.sum()*1e4:+.3f} bp")
    print(f"       **捕获率 = {act.sum()/max(adv.sum(),1e-9)*100:.1f}%**"
          f"（即时优势里最终留下多少）")

    print("\n" + "=" * 96)
    print("判据")
    print("=" * 96)
    cap = act.sum() / max(adv.sum(), 1e-9)
    print(f"\n  捕获率 {cap*100:.1f}%")
    if cap < 0.3:
        print("  ⇒ **捕获率 <30% ⇒ 即时优势被逆选择吃掉 70%+**")
        print("     ⇒ 单独把报价挂远（提 edge）未必有用 —— 挂远会同时降低成交率")
        print("     ⇒ 真正的杠杆是**筛掉会逆行的那部分成交**（H85 已证不可预测）")
    elif cap < 0.6:
        print("  ⇒ 捕获率 30~60% ⇒ 典型的做市水平，逆选择吃掉一半左右")
    else:
        print("  ⇒ 捕获率 >60% ⇒ 入场质量不错，瓶颈不在这里")

    print(f"\n  ── 与 H95 门槛的换算 ──")
    print(f"    入场腿当前贡献 = {act.sum()/notion.sum()*1e4:+.3f} bp/笔（按名义加权）")
    print(f"    若捕获率从 {cap*100:.0f}% 提到 60%：")
    newbp = adv.sum() / notion.sum() * 1e4 * 0.60
    print(f"      入场腿 → {newbp:+.3f} bp/笔  （+{newbp - act.sum()/notion.sum()*1e4:+.3f}）")
    print(f"    H95 打平需抵消 {12.218:.3f} bp/次 × 强平率")
    print(f"      当前强平率 12.8% ⇒ 需 {12.218*0.128:.3f} bp/笔 来抵消")
    print(f"      ⇒ 入场腿需达 {12.218*0.128:.3f} bp 才打平，"
          f"当前 {act.sum()/notion.sum()*1e4:+.3f} bp")
    print(f"      ⇒ **缺口 {12.218*0.128 - act.sum()/notion.sum()*1e4:+.3f} bp**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
