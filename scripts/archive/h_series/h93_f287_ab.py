"""H93：F287 的 A/B 实测（用**完整口径** pnl+fee，并做等长时间窗对照）。

# 为什么用"等长时间窗"而不是"累计对比"

上一轮我直接把"F287 之前（3 小时）"与"F287 之后（6 分钟）"的强平率相比，
那**不可比**（窗口长度差 30 倍，且前窗跨越了 F286 的 0 宽度 artifact 期）。

本脚本改为：**取 F287 之前同样长度的时间窗**做对照，保证两侧时长一致。

# 口径（H92 的结论）

  · **必须用 `paper_pnl + paper_fee`**（只算 pnl 会高估 ~36%）
  · 按周期折算：每周期 USD 与 bp（按 $135 腿量）
  · 强平率用 H84 的**推导周期**（不是 position_id 计数器）

# 判据（事先定死）

  · 样本 < 30 周期/侧 ⇒ **只报方向，不下结论**
  · 若"完整口径每周期净额"改善 > 2 个合并标准误 ⇒ 有效
  · 若无改善 ⇒ F287 回滚（`mm_apply_reduce_mult.py --rollback`）

用法：
    .venv\\Scripts\\python.exe scripts\\h93_f287_ab.py
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

F287_TS = 1789931100.0     # 2026-09-21 03:05:00 本地
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

    print("=" * 100)
    print("H93  F287 A/B 实测（完整口径 pnl+fee，等长窗口）")
    print("=" * 100)
    now = time.time()
    span = now - F287_TS
    print(f"  F287 上线于 {time.strftime('%H:%M:%S', time.localtime(F287_TS))}")
    print(f"  之后已运行 **{span/60:.1f} 分钟** ⇒ 对照窗取同样长度")

    eps = derive(load())
    pre = [e for e in eps if F287_TS - span <= (e.get("ts0") or 0) < F287_TS]
    post = [e for e in eps if (e.get("ts0") or 0) >= F287_TS]
    print(f"  对照窗周期 {len(pre)}   F287 后周期 {len(post)}")

    # 完整口径：把 ledger 行按 (symbol, 秒) 归到周期
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT created_at, action, amount_usd::float a,"
        "       metadata_json::jsonb->>'symbol' sym"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= to_timestamp(%s)", (F287_TS - span - 600,))
    led = cur.fetchall()
    cn.close()
    by = defaultdict(float)
    for r in led:
        by[(r["sym"], int(r["created_at"].timestamp()))] += r["a"]

    def ep_pnl(e):
        tot = 0.0
        for t in {t for t in e["fillts"] if t}:
            for ds in (0, 1, -1, 2):
                k = (e["sym"], int(t) + ds)
                if k in by:
                    tot += by[k]
                    break
        return tot

    for lab, sub in (("F287 之前（等长窗）", pre), ("F287 之后", post)):
        print("\n" + "=" * 100)
        print(f"{lab}")
        print("=" * 100)
        if not sub:
            print("  无周期")
            continue
        n = len(sub)
        f = sum(1 for e in sub if e["flat"])
        vals = np.array([ep_pnl(e) for e in sub])
        d = np.array([e["dur_s"] for e in sub])
        se = vals.std(ddof=1) / np.sqrt(n) if n > 1 else float("nan")
        print(f"  周期 {n}   含强平 {f}   **强平率 {f/n*100:.1f}%**")
        print(f"  时长中位 {np.median(d):.0f}s")
        print(f"  完整口径（pnl+fee）每周期：")
        print(f"    均值 {vals.mean():>+9.5f} USD   中位 {np.median(vals):>+9.5f}   "
              f"合计 {vals.sum():>+9.4f}")
        print(f"    标准误 {se:.5f}   95%CI [{vals.mean()-1.96*se:+.5f}, "
              f"{vals.mean()+1.96*se:+.5f}]")
        print(f"    折算 bp（${NOTIONAL:.0f} 腿）: {vals.mean()/NOTIONAL*1e4:+.3f} bp/周期")

    print("\n" + "=" * 100)
    print("判据（事先定死）")
    print("=" * 100)
    if len(pre) < 30 or len(post) < 30:
        print(f"  ⚠️ 样本不足（前 {len(pre)} / 后 {len(post)}，需各 ≥30）")
        print("     ⇒ **只报方向，不下结论**。继续累积后重跑本脚本。")
    else:
        v0 = np.array([ep_pnl(e) for e in pre])
        v1 = np.array([ep_pnl(e) for e in post])
        f0 = sum(1 for e in pre if e["flat"]) / len(pre)
        f1 = sum(1 for e in post if e["flat"]) / len(post)
        diff = v1.mean() - v0.mean()
        pooled = np.sqrt(v0.var(ddof=1)/len(v0) + v1.var(ddof=1)/len(v1))
        print(f"  强平率：{f0*100:.1f}% → {f1*100:.1f}%")
        print(f"  每周期净额：{v0.mean():+.5f} → {v1.mean():+.5f} USD  "
              f"（改善 {diff:+.5f}）")
        print(f"  合并标准误 {pooled:.5f}  比值 {abs(diff)/max(pooled,1e-12):.2f}")
        if diff > 2 * pooled:
            print("  ⇒ **改善 > 2 SE ⇒ F287 有效**")
        elif diff < -2 * pooled:
            print("  ⇒ **恶化 > 2 SE ⇒ 应回滚**：mm_apply_reduce_mult.py --rollback")
        else:
            print("  ⇒ 在噪声内 ⇒ 继续观察，暂不动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
