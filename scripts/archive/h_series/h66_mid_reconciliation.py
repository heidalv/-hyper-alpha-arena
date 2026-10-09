"""H66：决定性检验 —— 巨亏笔的 `mid` 是真实盘口 mid，还是记账错配？

# H65 的结果把范围缩小了

| 指标（巨亏5% vs 正常） | 巨亏笔 | 正常笔 | 比值 |
|---|---|---|---|
| 前置数据间隔 ms | 197 | 110 | 1.8x ✗ |
| 价差 bp | 1.3655 | 1.1795 | 1.2x ✗ |
| **成交价偏离真实 mid** | **+2.0993bp** | **−0.7314bp** | **符号相反** |

⇒ **陈旧 mid 与行情事件都被否掉**（都不显著）。
⇒ 符号差异是真的（我们的成交在巨亏笔上偏离 2.83bp），但**远不足以解释 −40bp 量级的均亏**。

**⇒ 那 −40bp 是从哪来的？** 账本 `paper_pnl` 是用
`(px vs mid)` 算的。若 `mid` 字段记的是**别的时点/别的口径**的 mid，
那这个"亏损"就不是价格移动，而是**记账错配**（本项目已犯过 19 次口径错误，
其中 F252 就是净额虚报 71 倍）。

# 本脚本的决定性检验

对每一笔 fill 腿，取三个量：
  1. 账本里的 `px`（成交价）
  2. 账本里的 `mid`（记账 mid）
  3. **数据库里该时刻的真实盘口 mid**（用 `asterdex_book_ticker` 严格因果取）

然后比对 `账本 mid` vs `真实 mid`：
  · 若两者**基本相等** ⇒ 记账 mid 是对的 ⇒ −40bp 是**真实价格移动** ⇒ 问题是"暴露方向"
  · 若两者**差几十 bp** ⇒ **记账错配** ⇒ 必须先修记账，否则所有 P&L 数字都不可信

同时独立重算 paper_pnl 并与账本值对照，确认公式口径。

判据（事先定死）：
  · |账本mid − 真实mid| 中位 < 1bp ⇒ 记账正确，亏损真实
  · |账本mid − 真实mid| 中位 > 5bp ⇒ 记账错配，**先修口径再谈策略**

用法：
    .venv\\Scripts\\python.exe scripts\\h66_mid_reconciliation.py
"""
from __future__ import annotations

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

NOTIONAL = 28.3195


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

    ca = psycopg2.connect(_dsn("alpha_arena"))
    ca.autocommit = True
    cura = ca.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cura.execute(
        "SELECT amount_usd::float a, created_at,"
        "       metadata_json::jsonb->>'symbol' sym,"
        "       metadata_json::jsonb->>'side' sd,"
        "       (metadata_json::jsonb->>'px')::float px,"
        "       (metadata_json::jsonb->>'mid')::float mid,"
        "       (metadata_json::jsonb->>'qty')::float qty,"
        "       (metadata_json::jsonb->>'edge_bp')::float edge"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND COALESCE(metadata_json::jsonb->>'phase','')='fill'"
        "   AND created_at >= '2026-09-20 00:00:00'"
        "   AND metadata_json::jsonb->>'px' IS NOT NULL"
        " ORDER BY created_at")
    rows = cura.fetchall()
    ca.close()
    if not rows:
        print("无数据")
        return 1

    cb = psycopg2.connect(_dsn("alpha_market"))
    cb.autocommit = True
    curb = cb.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 逐币缓存盘口（避免 N 次查询）
    cache = {}

    def load(sym):
        vs = sym if sym.endswith("USDT") else f"{sym}USDT"
        if vs in cache:
            return cache[vs]
        curb.execute(
            "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
            "  FROM asterdex_book_ticker WHERE symbol=%s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 172800000"
            " ORDER BY event_ts_ms", (vs,))
        d = curb.fetchall()
        if not d:
            cache[vs] = None
            return None
        t = np.array([r["event_ts_ms"] for r in d], dtype=np.int64)
        b = np.array([r["b"] for r in d]); a = np.array([r["a"] for r in d])
        ok = (b > 0) & (a > b)
        cache[vs] = (t[ok], 0.5 * (b[ok] + a[ok]), b[ok], a[ok])
        return cache[vs]

    recs = []
    for r in rows:
        c = load(r["sym"])
        if c is None:
            continue
        t, mid, b, a = c
        ms = int(r["created_at"].timestamp() * 1000)
        j = np.searchsorted(t, ms, side="right") - 1
        if j < 0:
            continue
        real_mid = float(mid[j])
        if real_mid <= 0:
            continue
        recs.append({
            "bp": r["a"] / NOTIONAL * 1e4,
            "sym": r["sym"], "sd": r["sd"],
            "px": r["px"], "mid_book": r["mid"], "mid_real": real_mid,
            "edge": r["edge"], "qty": r["qty"],
            "dmid_bp": (r["mid"] - real_mid) / real_mid * 1e4,
            "px_dev_real_bp": (r["px"] - real_mid) / real_mid * 1e4,
            "lag_ms": int(ms - int(t[j])),
        })
    if not recs:
        print("无可用样本")
        return 1

    bp = np.array([x["bp"] for x in recs])
    dmid = np.array([x["dmid_bp"] for x in recs])
    pxdev = np.array([x["px_dev_real_bp"] for x in recs])
    o = np.argsort(bp)
    k5 = max(1, int(len(bp) * 0.05))

    print("=" * 104)
    print("H66  决定性检验：账本 mid 是不是真实盘口 mid")
    print("=" * 104)
    print(f"  样本 {len(bp):,} 笔 fill 腿（能与盘口对齐的）")
    print(f"  均值 {bp.mean():+.4f}bp   中位 {np.median(bp):+.4f}bp")

    print("\n" + "=" * 104)
    print("【核心指标】账本 mid vs 真实盘口 mid")
    print("=" * 104)
    print(f"  |账本mid − 真实mid| :  中位 {np.median(np.abs(dmid)):>10.4f}bp   "
          f"p95 {np.percentile(np.abs(dmid),95):>10.4f}bp   "
          f"最大 {np.abs(dmid).max():>10.4f}bp")
    print(f"  有符号偏差          :  中位 {np.median(dmid):>+10.4f}bp   "
          f"p5 {np.percentile(dmid,5):>+10.4f}   p95 {np.percentile(dmid,95):>+10.4f}")
    print(f"  帧延迟 lag          :  中位 {np.median([x['lag_ms'] for x in recs]):>10,.0f}ms")

    # 分组
    print("\n  分组对照：")
    for lab, idx in (("最亏 5%", o[:k5]), ("其余 95%", o[k5:])):
        print(f"\n    ── {lab}  n={len(idx):,}")
        print(f"       |账本mid−真实mid| 中位 {np.median(np.abs(dmid[idx])):>9.4f}bp   "
              f"p95 {np.percentile(np.abs(dmid[idx]),95):>9.4f}bp")
        print(f"       成交价偏离真实mid  中位 {np.median(pxdev[idx]):>+9.4f}bp   "
              f"p5 {np.percentile(pxdev[idx],5):>+9.4f}   "
              f"p95 {np.percentile(pxdev[idx],95):>+9.4f}")
        print(f"       账本 edge_bp      中位 {np.median([recs[i]['edge'] or 0 for i in idx]):>9.4f}")

    # ── 决定性判读 ──
    print("\n" + "=" * 104)
    print("判读（事先定死）")
    print("=" * 104)
    med_abs = float(np.median(np.abs(dmid)))
    if med_abs < 1.0:
        print(f"  |账本mid − 真实mid| 中位 = {med_abs:.4f}bp < 1bp")
        print("  ⇒ **记账正确，亏损是真实的** ⇒ 问题在暴露方向，不在口径")
    elif med_abs > 5.0:
        print(f"  |账本mid − 真实mid| 中位 = {med_abs:.4f}bp > 5bp")
        print("  ⇒ **记账错配** ⇒ 必须先修记账口径，否则所有 P&L 数字都不可信 ✗✗")
    else:
        print(f"  |账本mid − 真实mid| 中位 = {med_abs:.4f}bp（1~5bp 之间）")
        print("  ⇒ 记账基本对但有系统性小偏差 ⇒ 需要进一步定位")

    # 独立重算 paper_pnl，确认公式
    print("\n" + "=" * 104)
    print("独立重算 paper_pnl（确认记账公式）")
    print("=" * 104)
    rec_pnl = []
    for x in recs:
        # 买：mid − px 为正收益；卖：px − mid
        sign = -1.0 if str(x["sd"]).lower() == "buy" else 1.0
        rec_pnl.append(sign * (x["mid_book"] - x["px"]) / x["mid_book"] * x["qty"] * x["mid_book"])
    rec_pnl = np.array(rec_pnl)
    acct = np.array([x["bp"] * NOTIONAL / 1e4 for x in recs])
    diff = rec_pnl - acct
    print(f"  重算 vs 账本：中位差 {np.median(np.abs(diff)):>12.6f} USD  "
          f"最大差 {np.abs(diff).max():>12.6f} USD")
    if np.median(np.abs(diff)) < 1e-4:
        print("  ⇒ 记账公式 = sign × (mid − px) × qty ✓（已确认）")
    else:
        print("  ⇒ 记账公式与 sign×(mid−px)×qty **不一致** ⇒ 口径未确认 ✗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
