"""H60：F280 上线后的实盘对账 —— 每笔净额修前 vs 修后。

## 为什么要单独做这个

H56 的反事实说 F280 把跨币均值从 −1.4167bp 改到 −0.7792bp。
**但那是模拟。** 实盘有没有同样改善，必须用**真实账本**对账：
本项目已有 19 次口径错误，其中至少 3 次是"模拟说好了、实盘没动"
（H36 符号错、F189 参数没生效、F204 陈旧测试）。

## 口径

**每笔净额** = 该时间窗内 `paper_pnl` 合计 / 该窗内成交笔数 / 每笔名义 × 1e4
每笔名义取 `fill_notional`（= compound_ratio × equity ≈ 0.1 × equity）。

**切点**：F280 上线时刻（registry `f280_spread_mult_rollback.applied_at`）。
修前 = 同一批币、同一模式的上一段窗口（默认同样时长，紧邻切点之前）。

**注意排除**：09-14/09-15 那两个 epoch 的配置与本轮不同（`max_one_side_seconds`
与 `w_base_bp` 都改过），**不能混进来比**。所以默认只取"今天"这一段。

用法：
    .venv\\Scripts\\python.exe scripts\\h60_post_f280_audit.py
    .venv\\Scripts\\python.exe scripts\\h60_post_f280_audit.py --cutoff "2026-09-21 00:00:00"
"""
from __future__ import annotations

import argparse
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

LANE = os.getenv("MM_LANE_ID", "mm_asterdex")


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="", help="F280 上线时刻（本地时间）")
    ap.add_argument("--window-min", type=float, default=0.0,
                    help="切点两侧各取多少分钟（0=自动用切点至今）")
    a = ap.parse_args()

    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── 1) 找切点 ──
    cur.execute(
        "SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
    row = cur.fetchone()
    cutoff = a.cutoff
    if not cutoff and row and row["meta_json"]:
        import json
        try:
            m = row["meta_json"]
            m = json.loads(m) if isinstance(m, str) else m
            cut = (m.get("f280_spread_mult_rollback") or {}).get("applied_at")
            if cut:
                # ISO(UTC) → 本地
                import datetime
                dt = datetime.datetime.fromisoformat(cut.replace("Z", "+00:00"))
                cutoff = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                print(f"  从 registry 读到切点（UTC {cut}）→ 本地 {cutoff}")
        except Exception as e:
            print(f"  读取切点失败：{e}")
    if not cutoff:
        print("  未指定 --cutoff 且 registry 无记录 ⇒ 按当前时间往前 60 分钟当切点")
        import datetime
        cutoff = (datetime.datetime.now()
                  - datetime.timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 96)
    print(f"H60  F280 上线后实盘对账   切点 = {cutoff}")
    print("=" * 96)

    # ── 2) 分窗统计 ──
    # 修后 = [cutoff, now]；修前 = [cutoff - Δ, cutoff)，Δ = 修后长度
    # ⚠️ `created_at` 是 **timestamp without time zone**（本地墙钟）⇒
    #    所有比较量必须用**naive 本地时间**。用 aware 或 UTC 会静默错配
    #    （第 9 条教训：H27 用 UTC 比对得到 0/289 匹配 ✗）。
    import datetime
    now = datetime.datetime.now()
    cdt = datetime.datetime.strptime(cutoff, "%Y-%m-%d %H:%M:%S")
    if a.window_min > 0:
        delta = datetime.timedelta(minutes=a.window_min)
    else:
        delta = now - cdt
    pre_lo = cdt - delta

    def window(lo, hi, label):
        cur.execute(
            "SELECT count(*) n, COALESCE(SUM(amount_usd),0) s,"
            "       min(created_at) mn, max(created_at) mx"
            "  FROM arbitrage_paper_ledgers"
            " WHERE account_id=101 AND action='paper_pnl'"
            "   AND created_at >= %s AND created_at < %s",
            (lo, hi))
        r = cur.fetchone()
        n = int(r["n"] or 0)
        s = float(r["s"] or 0)
        print(f"\n  ── {label}：{lo} ~ {hi}")
        print(f"     成交 {n:,} 笔   paper_pnl 合计 {s:>+11.4f} USD"
              f"   （实际时间跨度 {r['mn']} ~ {r['mx']}）")
        if n == 0:
            return {"n": 0, "s": 0.0, "bp": float("nan")}
        # 每笔名义
        cur.execute("SELECT COALESCE(AVG(amount_usd),0) a FROM arbitrage_paper_ledgers"
                    " WHERE account_id=101 AND action='paper_pnl'"
                    "   AND created_at >= %s AND created_at < %s", (lo, hi))
        # 每笔名义从 fill_notional（0.1×equity）拿更准
        notional = 28.5
        try:
            import json
            sf = ROOT / "logs" / "mm_lane_status.json"
            if sf.exists():
                notional = float(json.loads(sf.read_text(encoding="utf-8"))
                                 .get("fill_notional") or 28.5)
        except Exception:
            pass
        bp = s / n / notional * 1e4
        print(f"     每笔名义 ${notional:.4f}  ⇒ **每笔净额 {bp:>+8.4f} bp**")
        return {"n": n, "s": s, "bp": bp}

    pre = window(pre_lo, cdt, "修前（F280 之前）")
    post = window(cdt, now, "修后（F280 之后）")

    print("\n" + "=" * 96)
    print("对账结果")
    print("=" * 96)
    if pre["n"] and post["n"]:
        print(f"  修前 {pre['bp']:>+8.4f} bp/笔  （{pre['n']:,} 笔）")
        print(f"  修后 {post['bp']:>+8.4f} bp/笔  （{post['n']:,} 笔）")
        d = post["bp"] - pre["bp"]
        print(f"  改善 {d:>+8.4f} bp/笔")
        print(f"\n  H56 反事实预测的改善量：**+0.638 bp/笔**（跨币均值，模拟）")
        if post["n"] < 500:
            print(f"\n  ⚠️ 修后样本仅 {post['n']:,} 笔 —— **不足以判定**。"
                  f"建议至少累计 2,000 笔再说。")
        if d > 0.3:
            print("  ⇒ 实盘改善与模拟**方向一致且量级相当** ✓")
        elif d > 0:
            print("  ⇒ 实盘有改善，但**小于模拟预测** ⇒ 需查为何模拟偏乐观")
        else:
            print("  ⇒ **实盘没有改善甚至更差** ⇒ 模拟与实盘分叉，必须查根因 ✗✗")
    else:
        print(f"  样本不足（修前 {pre['n']} 笔，修后 {post['n']} 笔）—— 等累计够再看")

    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
