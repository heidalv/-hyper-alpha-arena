"""H94：完整成本桥 —— 把 −2.737 bp/周期拆成可归因的分项，给剩余杠杆排序。

# 为什么需要这个

到目前为止我的成本认知是**逐块拼出来的**，而且拼错过好几次：
  · 一开始只算 `paper_pnl`（漏了 fee，占 37%）
  · 强平成本先说 −12.73bp/笔，后按周期口径又不同
  · 进出场腿的归因换过三种口径

**⇒ 没有一个统一的"钱从哪来、到哪去"的桥。** 本脚本建立它。

# 口径（全部用 H92 的结论）

  · 完整口径 = `paper_pnl + paper_fee`（**必须含 fee**）
  · 周期 = H84 从持仓曲线推导（不是 position_id 计数器）
  · 分母明确写清：按周期 / 按行 / 按 bp（$135 腿）

# 成本桥的分项（尽量用**可观测账本量**，不引入新模型）

  ① **毛价差捕获**：`paper_pnl` 中 `phase=fill` 且非平仓的部分
     （注意：账本没有直接分列 spread/price，所以这里用**代理**并标注）
  ② **强平成本**：`phase=flatten` 的 pnl + 对应 `paper_fee`
  ③ **其余费用**：非强平腿的 fee
  ④ **残差**：合计 − 上面各项（若有显著残差，说明账本口径仍有未知项）

# 判据（事先定死）

  · 若 ① 为正、② 为负且 |②| > ① ⇒ **强平是唯一障碍**，集中打它
  · 若 ① 本身为负 ⇒ 入场侧就是亏的，光修出库不够
  · 残差 > 总额的 10% ⇒ **口径仍不完整**，先别谈策略

用法：
    .venv\\Scripts\\python.exe scripts\\h94_cost_bridge.py
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
DAY = "2026-09-21 00:00:00"


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

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=" * 96)
    print("H94  完整成本桥（今天，含 fee）")
    print("=" * 96)

    # ── 按 (action, phase) 拆 ──
    cur.execute(
        "SELECT action, COALESCE(metadata_json::jsonb->>'phase','(no phase)') ph,"
        "       count(*) n, COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action<>'create_account'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= %s"
        " GROUP BY 1,2 ORDER BY 2,1", (DAY,))
    rows = cur.fetchall()
    print(f"\n  {'action':<12} {'phase':<12} {'行数':>7} {'合计USD':>12} {'每行bp':>10}")
    print("  " + "-" * 58)
    tot = 0.0
    phase_tot = defaultdict(float)
    for r in rows:
        s = float(r["s"]); n = int(r["n"])
        tot += s
        phase_tot[(r["action"], r["ph"])] += s
        print(f"  {r['action']:<12} {r['ph']:<12} {n:>7} {s:>+12.4f} "
              f"{s/max(n,1)/NOTIONAL*1e4:>+10.3f}")
    print("  " + "-" * 58)
    print(f"  {'合计':<12} {'':<12} {'':>7} {tot:>+12.4f}")

    # ── 周期数 ──
    from h84_derive_episodes import derive, load
    eps = derive(load())
    epsd = [e for e in eps if (e.get("ts0") or 0) >= 1789920000.0]
    n_ep = len(epsd)
    n_flat_ep = sum(1 for e in epsd if e["flat"])
    print(f"\n  今天周期 {n_ep}（含强平 {n_flat_ep} = {n_flat_ep/max(n_ep,1)*100:.1f}%）")
    print(f"  ⇒ 完整口径 {tot:+.4f} USD / {n_ep} 周期 = **{tot/max(n_ep,1):+.5f} USD/周期**")
    print(f"  ⇒ 折算 **{tot/max(n_ep,1)/NOTIONAL*1e4:+.3f} bp/周期**")

    # ── 成本桥 ──
    print("\n" + "=" * 96)
    print("成本桥（按 phase × action 归集）")
    print("=" * 96)
    fill_pnl = phase_tot.get(("paper_pnl", "fill"), 0.0)
    flat_pnl = phase_tot.get(("paper_pnl", "flatten"), 0.0)
    noph_pnl = sum(v for (a, p), v in phase_tot.items()
                   if a == "paper_pnl" and p == "(no phase)")
    fill_fee = phase_tot.get(("paper_fee", "fill"), 0.0)
    flat_fee = phase_tot.get(("paper_fee", "flatten"), 0.0)
    noph_fee = sum(v for (a, p), v in phase_tot.items()
                   if a == "paper_fee" and p == "(no phase)")

    items = [
        ("① 入场腿 pnl（做市成交的价格损益）", fill_pnl),
        ("② 入场腿 fee", fill_fee),
        ("③ 强平腿 pnl（市价平仓的价格损益）", flat_pnl),
        ("④ 强平腿 fee（taker 费）", flat_fee),
    ]
    if abs(noph_pnl) > 1e-9 or abs(noph_fee) > 1e-9:
        items.append(("⑤ 无 phase 标记的 pnl", noph_pnl))
        items.append(("⑥ 无 phase 标记的 fee", noph_fee))

    print(f"\n  {'分项':<38} {'USD':>12} {'bp/周期':>10} {'占比':>8}")
    print("  " + "-" * 72)
    known = 0.0
    for lab, v in items:
        known += v
        share = v / tot * 100 if abs(tot) > 1e-12 else 0.0
        print(f"  {lab:<38} {v:>+12.4f} {v/max(n_ep,1)/NOTIONAL*1e4:>+10.3f} "
              f"{share:>7.1f}%")
    resid = tot - known
    print("  " + "-" * 72)
    print(f"  {'已知分项合计':<38} {known:>+12.4f}")
    print(f"  {'残差（未归因）':<38} {resid:>+12.4f} "
          f"{resid/max(n_ep,1)/NOTIONAL*1e4:>+10.3f} "
          f"{resid/tot*100 if abs(tot)>1e-12 else 0:>7.1f}%")
    print(f"  {'全部合计':<38} {tot:>+12.4f}")

    # ── 判据 ──
    print("\n" + "=" * 96)
    print("判据")
    print("=" * 96)
    print(f"\n  入场侧（①+②）= {fill_pnl+fill_fee:+.4f} USD "
          f"（{(fill_pnl+fill_fee)/max(n_ep,1)/NOTIONAL*1e4:+.3f} bp/周期）")
    print(f"  出库侧（③+④）= {flat_pnl+flat_fee:+.4f} USD "
          f"（{(flat_pnl+flat_fee)/max(n_ep,1)/NOTIONAL*1e4:+.3f} bp/周期）")
    print(f"  残差占比 = {abs(resid)/max(abs(tot),1e-12)*100:.1f}%")

    if abs(resid) / max(abs(tot), 1e-12) > 0.10:
        print("\n  ⇒ **残差 > 10% ⇒ 账本口径仍不完整** ⇒ 先别谈策略，先搞清钱去哪了")
    else:
        if fill_pnl + fill_fee > 0 and flat_pnl + flat_fee < 0:
            print("\n  ⇒ **入场侧为正、出库侧为负** ⇒ 出库（强平）是唯一障碍")
            print("     ⇒ 集中打强平率 / 出库成交概率（F287 方向）")
        elif fill_pnl + fill_fee < 0:
            print("\n  ⇒ **入场侧本身为负** ⇒ 光修出库不够，入场质量也要改")
        else:
            print("\n  ⇒ 两侧同号，需逐项细看")

    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
