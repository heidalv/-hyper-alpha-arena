# -*- coding: utf-8 -*-
"""[F363 2026-09-18] 开仓准确率**测量器**（只读；含预注册判定规则）。

## 为什么要有它

用户的核心问题之一「**开仓准确率有提升吗**」在本次复查里得到的答案是
**"不足以判定"**（POST 样本仅 19~27 笔，p=0.147~0.243，且**结论随 cut 边界翻转**）。
问题不在于"没有结论"，而在于**没有一台可重复、口径固定、不许事后改口径的测量器**：
每换一次口径（去重方式、边界、指标）就能得到相反的答案。

本脚本把口径**预注册**在代码里，任何人任何时候跑都得到同一套判定：

| 项 | 规定 |
|---|---|
| 主终点 | `trade_facts` 的**胜率**（定义：`pnl > 0` 记为赢；同时并列报 `outcome` 口径） |
| 切分点 | `--cut`（默认架构升级窗口 `2026-09-16T00:00:00Z`），PRE = cut 之前 `--days` 天 |
| 检验 | 两比例 z 检验，**双侧 α=0.05** |
| 功效 | 80%（用于反推"每组还需多少笔"） |
| 次终点 | `signal_trade_feedback` 逐单去重胜率（按 `trade_id` 聚合）、`signal_ledger` 命中率与超额 bp |
| 边界敏感性 | **每次都自动重算** cut ±12h / ±24h —— 防止"换个边界就有结论" |
| 判定 | 只有"p<0.05 **且** 边界敏感性下方向一致"才输出"已可判定" |

**只读**：`SET app.is_admin='on'`（alpha_arena 有 58 张 FORCE RLS 表，不设会读到 0 行）；
不写任何表、不改文件、不重启。

用法：
    python scripts/measure_entry_accuracy.py                    # 默认切分点
    python scripts/measure_entry_accuracy.py --cut 2026-09-17T00:00:00
    python scripts/measure_entry_accuracy.py --days 30 --min-effect 0.05
"""
from __future__ import annotations

import argparse
import io
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEFAULT_CUT = "2026-09-16T00:00:00+00:00"
Z_ALPHA_2 = 1.959963985      # 双侧 0.05
Z_POWER = 0.841621234        # 80% 功效


# ───────────────────────── 统计（不依赖 scipy） ─────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def two_prop_z(k1: int, n1: int, k2: int, n2: int) -> tuple:
    """两比例 z 检验（pooled）。返回 (z, p_two_sided, p1, p2)。"""
    if not n1 or not n2:
        return 0.0, 1.0, 0.0, 0.0
    p1, p2 = k1 / n1, k2 / n2
    p_pool = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se <= 0:
        return 0.0, 1.0, p1, p2
    z = (p2 - p1) / se
    return z, 2 * (1 - _norm_cdf(abs(z))), p1, p2


def required_n_per_group(p1: float, p2: float, alpha: float = 0.05,
                         power: float = 0.80) -> int:
    """两比例检验每组所需样本（正态近似）。p2<=p1 时按 p2=p1+0.05 兜底。"""
    z_a = Z_ALPHA_2 if abs(alpha - 0.05) < 1e-9 else 1.959963985
    z_b = Z_POWER if abs(power - 0.80) < 1e-9 else 0.841621234
    if p2 <= p1:
        p2 = min(0.99, p1 + 0.05)
    pbar = (p1 + p2) / 2
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar)) + z_b * math.sqrt(
        p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    den = (p2 - p1) ** 2
    return int(math.ceil(num / den)) if den > 0 else 10 ** 9


def welch_ci(xs1, xs2) -> tuple:
    """Welch t 统计量 + 正态近似 95% CI（样本小时仅作参考，脚本会标注）。"""
    n1, n2 = len(xs1), len(xs2)
    if n1 < 2 or n2 < 2:
        return 0.0, (float("nan"), float("nan"))
    m1, m2 = sum(xs1) / n1, sum(xs2) / n2
    v1 = sum((x - m1) ** 2 for x in xs1) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in xs2) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se <= 0:
        return 0.0, (m2 - m1, m2 - m1)
    t = (m2 - m1) / se
    return t, (m2 - m1 - Z_ALPHA_2 * se, m2 - m1 + Z_ALPHA_2 * se)


# ───────────────────────── 取数（只读） ─────────────────────────

def _fetch(db, sql: str, **params):
    from sqlalchemy import text
    return db.execute(text(sql), params).fetchall()


def collect(cut: datetime, days: int) -> dict:
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        db.execute(__import__("sqlalchemy").text("SET app.is_admin='on'"))
        pre_lo = cut - timedelta(days=days)
        out: dict = {"cut": cut.isoformat(), "pre_from": pre_lo.isoformat(), "days": days}

        # ① trade_facts 胜率（主终点）
        r = _fetch(db, """
            SELECT
              COUNT(*) FILTER (WHERE ts < :cut)                                AS n_pre,
              COUNT(*) FILTER (WHERE ts >= :cut)                               AS n_post,
              COUNT(*) FILTER (WHERE ts < :cut AND pnl > 0)                    AS w_pre,
              COUNT(*) FILTER (WHERE ts >= :cut AND pnl > 0)                   AS w_post,
              COUNT(*) FILTER (WHERE ts < :cut AND backfilled IS TRUE)         AS bf_pre,
              COUNT(*) FILTER (WHERE ts >= :cut AND backfilled IS TRUE)        AS bf_post,
              COUNT(*) FILTER (WHERE ts < :cut AND outcome = 'win')            AS ow_pre,
              COUNT(*) FILTER (WHERE ts >= :cut AND outcome = 'win')           AS ow_post
            FROM trade_facts WHERE ts >= :lo
        """, cut=cut, lo=pre_lo)
        n_pre, n_post, w_pre, w_post, bf_pre, bf_post, ow_pre, ow_post = [int(x or 0) for x in r[0]]
        out["trade_facts"] = {
            "n_pre": n_pre, "n_post": n_post, "win_pre": w_pre, "win_post": w_post,
            "backfilled_pre": bf_pre, "backfilled_post": bf_post,
            "outcome_win_pre": ow_pre, "outcome_win_post": ow_post,
        }
        post_per_day = None
        if n_post:
            span = max((datetime.now(timezone.utc) - cut).total_seconds() / 86400.0, 0.25)
            post_per_day = n_post / span
        out["post_per_day"] = post_per_day

        # ② signal_trade_feedback 逐单去重胜率（次终点）
        r = _fetch(db, """
            WITH per_trade AS (
              SELECT trade_id,
                     AVG(trade_pnl_pct) AS avg_pct,
                     MIN(created_at)    AS first_at
              FROM signal_trade_feedback
              WHERE trade_id IS NOT NULL AND trade_pnl_pct IS NOT NULL
                AND created_at >= :lo
              GROUP BY trade_id
            )
            SELECT
              COUNT(*) FILTER (WHERE first_at < :cut)                          AS n_pre,
              COUNT(*) FILTER (WHERE first_at >= :cut)                         AS n_post,
              COUNT(*) FILTER (WHERE first_at < :cut AND avg_pct > 0)          AS w_pre,
              COUNT(*) FILTER (WHERE first_at >= :cut AND avg_pct > 0)         AS w_post
            FROM per_trade
        """, cut=cut.replace(tzinfo=None), lo=pre_lo.replace(tzinfo=None))
        a, b, c, d = [int(x or 0) for x in r[0]]
        out["signal_trade_feedback"] = {"n_pre": a, "n_post": b, "win_pre": c, "win_post": d}

        # ③ signal_ledger 命中率 + 超额 bp
        cut_ms = int(cut.timestamp() * 1000)
        lo_ms = int(pre_lo.timestamp() * 1000)
        r = _fetch(db, """
            SELECT
              COUNT(*) FILTER (WHERE created_ms < :cm)                         AS n_pre,
              COUNT(*) FILTER (WHERE created_ms >= :cm)                        AS n_post,
              COUNT(*) FILTER (WHERE created_ms < :cm AND hit = 1)             AS h_pre,
              COUNT(*) FILTER (WHERE created_ms >= :cm AND hit = 1)            AS h_post,
              AVG(excess_bp) FILTER (WHERE created_ms < :cm)                   AS ex_pre,
              AVG(excess_bp) FILTER (WHERE created_ms >= :cm)                  AS ex_post
            FROM signal_ledger WHERE created_ms >= :lo
        """, cm=cut_ms, lo=lo_ms)
        n1, n2, h1, h2, ex1, ex2 = r[0]
        out["signal_ledger"] = {
            "n_pre": int(n1 or 0), "n_post": int(n2 or 0),
            "hit_pre": int(h1 or 0), "hit_post": int(h2 or 0),
            "excess_bp_pre": float(ex1) if ex1 is not None else None,
            "excess_bp_post": float(ex2) if ex2 is not None else None,
        }
        return out
    finally:
        db.close()


# ───────────────────────── 报告 ─────────────────────────

def _pct(k: int, n: int) -> str:
    return f"{k / n:.2%}" if n else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", default=DEFAULT_CUT)
    ap.add_argument("--days", type=int, default=30, help="PRE 窗口天数")
    ap.add_argument("--min-effect", type=float, default=0.0,
                    help=">0 时按该最小可检测效应(比例)反推样本，而不是用实测 POST 比例")
    args = ap.parse_args()

    cut = datetime.fromisoformat(args.cut)
    if cut.tzinfo is None:
        cut = cut.replace(tzinfo=timezone.utc)

    print("=" * 84)
    print("开仓准确率测量（预注册口径；只读）")
    print(f"切分点 cut = {cut.isoformat()}   PRE 窗口 = 前 {args.days} 天")
    print("=" * 84)

    base = collect(cut, args.days)
    tf = base["trade_facts"]
    z, p, p1, p2 = two_prop_z(tf["win_pre"], tf["n_pre"], tf["win_post"], tf["n_post"])
    print("\n【主终点】trade_facts 胜率（赢 = pnl > 0）")
    print(f"  PRE : {_pct(tf['win_pre'], tf['n_pre'])}  (n={tf['n_pre']}, 其中 backfilled={tf['backfilled_pre']})")
    print(f"  POST: {_pct(tf['win_post'], tf['n_post'])}  (n={tf['n_post']}, 其中 backfilled={tf['backfilled_post']})")
    print(f"  Δ = {(p2 - p1) * 100:+.2f}pp   两比例 z 检验: z={z:.3f}  **p={p:.4f}**  (双侧 α=0.05)")
    print(f"  并列口径（outcome='win'）: PRE {_pct(tf['outcome_win_pre'], tf['n_pre'])} / "
          f"POST {_pct(tf['outcome_win_post'], tf['n_post'])}")

    p2_for_n = args.min_effect if args.min_effect > 0 else p2
    need = required_n_per_group(p1, p1 + p2_for_n if args.min_effect > 0 else p2)
    print(f"\n【还需多少样本】每组 {need} 笔（80% 功效, α=0.05；"
          f"{'按最小可检测效应 %.3f' % args.min_effect if args.min_effect > 0 else '按实测 POST 比例'}）")
    rate = base.get("post_per_day")
    if rate:
        gap = max(0, need - tf["n_post"])
        print(f"  POST 当前 {tf['n_post']} 笔，日均 {rate:.1f} 笔 ⇒ 约需 {gap / rate:.1f} 天"
              f"（若按 PRE 日均 {tf['n_pre'] / max(args.days, 1):.1f} 笔则 "
              f"{gap / max(tf['n_pre'] / max(args.days, 1), 0.1):.1f} 天）")
    else:
        print("  POST 无成交 ⇒ 无法估算天数")

    stf = base["signal_trade_feedback"]
    z2, p2v, q1, q2 = two_prop_z(stf["win_pre"], stf["n_pre"], stf["win_post"], stf["n_post"])
    print("\n【次终点】signal_trade_feedback 逐单去重胜率（按 trade_id 聚合 trade_pnl_pct）")
    print(f"  PRE : {_pct(stf['win_pre'], stf['n_pre'])} (n={stf['n_pre']})   "
          f"POST: {_pct(stf['win_post'], stf['n_post'])} (n={stf['n_post']})   "
          f"Δ={(q2 - q1) * 100:+.2f}pp  z={z2:.3f}  p={p2v:.4f}")

    sl = base["signal_ledger"]
    z3, p3v, s1, s2 = two_prop_z(sl["hit_pre"], sl["n_pre"], sl["hit_post"], sl["n_post"])
    print("\n【辅助】signal_ledger 命中率 / 平均超额")
    print(f"  命中率 PRE {_pct(sl['hit_pre'], sl['n_pre'])} (n={sl['n_pre']}) → "
          f"POST {_pct(sl['hit_post'], sl['n_post'])} (n={sl['n_post']})  "
          f"Δ={(s2 - s1) * 100:+.2f}pp  p={p3v:.4f}")
    ex1, ex2 = sl["excess_bp_pre"], sl["excess_bp_post"]
    if ex1 is not None and ex2 is not None:
        print(f"  平均超额 PRE {ex1:+.1f}bp → POST {ex2:+.1f}bp  （Δ={ex2 - ex1:+.1f}bp）")

    print("\n【边界敏感性】每次都重算 —— 结论若随边界翻转则不可作验收依据")
    flips = []
    for dh in (-24, -12, 12, 24):
        c2 = cut + timedelta(hours=dh)
        try:
            b2 = collect(c2, args.days)
            t2 = b2["trade_facts"]
            zz, pp, _, _ = two_prop_z(t2["win_pre"], t2["n_pre"], t2["win_post"], t2["n_post"])
            sign = "↑" if (t2["win_post"] / max(t2["n_post"], 1)) > (t2["win_pre"] / max(t2["n_pre"], 1)) else "↓"
            print(f"  cut{dh:+3d}h: POST n={t2['n_post']:<4} 胜率={_pct(t2['win_post'], t2['n_post']):<8} "
                  f"Δ={((t2['win_post'] / max(t2['n_post'], 1)) - (t2['win_pre'] / max(t2['n_pre'], 1))) * 100:+.2f}pp "
                  f"p={pp:.4f} 方向{sign}")
            flips.append(sign)
        except Exception as exc:
            print(f"  cut{dh:+3d}h: 计算失败 {str(exc)[:80]}")

    print("\n" + "=" * 84)
    sig = p < 0.05
    stable = len(set(flips)) <= 1 if flips else False
    main_dir = "↑" if p2 > p1 else "↓"
    if sig and stable and (not flips or flips[0] == main_dir):
        print("判定：**已可判定**（p<0.05 且边界敏感性下方向一致）")
    elif sig and not stable:
        print("判定：**暂不可判定** —— 主切分点显著，但边界敏感性下方向不一致（结论随边界翻转）")
    else:
        print(f"判定：**暂不可判定** —— 样本不足（p={p:.4f}；POST n={tf['n_post']}，"
              f"每组需 {need} 笔）")
    print("口径提示：本脚本的终点/切分/检验/功效已预注册在文件头；"
          "若需换口径，请显式改参数并在报告中标注，不要事后择口径。")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
