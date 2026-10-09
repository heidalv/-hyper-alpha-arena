# -*- coding: utf-8 -*-
"""[2026-09-24 第3轮·续] 中线"从没动过"那一桶的入场画像 + 筛子交叉验证（只用 09-15 后样本）。

上一步结论：116 笔里 56 笔（48%）峰值 <1%，合计 −$426.46（均 −7.62、胜率 5.4%）——
它们就是中线全部亏损来源；其余 60 笔合计 +$390。
本脚本回答：
  A. 这 56 笔"从没动过"的单，入场时有什么可观测的共同点（4h EMA21 位置 / 模型方向 / 时段 / 币种）；
  B. 两个"两段都坏"的筛子（低于 EMA21 / 逆模型方向）合并后，剩余样本在**前后半**各是什么表现；
  C. 用它们做门禁的代价：会挡掉多少笔、挡掉的部分里有没有"好单"（误杀率）。
"""
from __future__ import annotations

import io
import statistics as st
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import audit_mid_entry_quality_after0915_20260924 as A  # noqa: E402

# 注意：被导入模块已在模块级包装过 sys.stdout，这里不得再包一次（重复包装会把底层
# buffer 关掉，导致 "I/O operation on closed file"）。


def main() -> int:
    trades = A.enrich(A.load())
    half = len(trades) // 2
    dead = [t for t in trades if float(t["peak_pnl_pct"] or 0) < 0.01]
    alive = [t for t in trades if float(t["peak_pnl_pct"] or 0) >= 0.01]
    print("样本 %d 笔：从没动过 %d / 动过 %d" % (len(trades), len(dead), len(alive)))

    def share(rows, fn):
        if not rows:
            return "0/0"
        k = sum(1 for t in rows if fn(t))
        return "%d/%d (%.0f%%)" % (k, len(rows), 100.0 * k / len(rows))

    print("\n== A. 两桶的入场画像对比 ==")
    feats = [
        ("入场价低于 4h EMA21", lambda t: (t.get("ema_dist") or 0) < 0),
        ("入场价高于 EMA21 >3%", lambda t: (t.get("ema_dist") or 0) > 3),
        ("模型方向=short", lambda t: t.get("model_dir") == "short"),
        ("模型方向=neutral", lambda t: t.get("model_dir") == "neutral"),
        ("4h 标签=down", lambda t: t.get("ema_label") == "down"),
        ("4h 标签=up", lambda t: t.get("ema_label") == "up"),
        ("16-24 点入场（欧/美盘）", lambda t: t["opened_at"].hour >= 16),
    ]
    for name, fn in feats:
        print("  %-24s 从没动过 %-14s 动过 %s" % (name, share(dead, fn), share(alive, fn)))

    print("\n== A2. 从没动过 56 笔的币种分布（top）==")
    cnt = {}
    for t in dead:
        cnt[t["symbol"]] = cnt.get(t["symbol"], 0) + 1
    print("  " + ", ".join("%s×%d" % (k, v) for k, v in sorted(cnt.items(), key=lambda x: -x[1])[:10]))

    print("\n== B/C. 门禁筛子（组合）在前后半的表现与误杀率 ==")
    filters = [
        ("F1 只挡 低于EMA21", lambda t: (t.get("ema_dist") or 0) < 0),
        ("F2 只挡 逆模型(short)", lambda t: t.get("model_dir") == "short"),
        ("F1+F2 任一命中即挡", lambda t: ((t.get("ema_dist") or 0) < 0) or t.get("model_dir") == "short"),
        ("F3 挡 低于EMA21 或 模型neutral/short",
         lambda t: ((t.get("ema_dist") or 0) < 0) or t.get("model_dir") in ("short", "neutral")),
        # [加测] 时段门：16-24 点（欧/美盘）入场在上一张表里是重灾区
        ("F4 只挡 16-24 点入场", lambda t: t["opened_at"].hour >= 16),
        ("F2+F4（逆模型 或 16-24 点）",
         lambda t: t.get("model_dir") == "short" or t["opened_at"].hour >= 16),
        ("F1+F2+F4（三者任一）",
         lambda t: ((t.get("ema_dist") or 0) < 0) or t.get("model_dir") == "short"
         or t["opened_at"].hour >= 16),
    ]
    base = A.agg(trades)
    print("  基线: n=%d 总 %+.2f 均 %+.2f" % (base["n"], base["total"], base["avg"]))
    for name, fn in filters:
        kept = [t for t in trades if not fn(t)]
        blocked = [t for t in trades if fn(t)]
        k1 = [t for t in trades[:half] if not fn(t)]
        k2 = [t for t in trades[half:] if not fn(t)]
        b, a1, a2, kb = A.agg(kept), A.agg(k1), A.agg(k2), A.agg(blocked)
        # 误杀率：被挡掉的单里，峰值 >=2.5% 的（本可赚钱的）占比
        miss = sum(1 for t in blocked if float(t["peak_pnl_pct"] or 0) >= 0.025)
        miss_pnl = sum(float(t["pnl"] or 0) for t in blocked if float(t["peak_pnl_pct"] or 0) >= 0.025)
        print("  %-34s 剩余 n=%3d 总 %+8.2f 均 %+6.2f | 前半 n=%3d 均 %+6.2f | 后半 n=%3d 均 %+6.2f"
              % (name, b["n"], b["total"], b["avg"], a1["n"], a1["avg"], a2["n"], a2["avg"]))
        print("  %-34s   被挡 n=%3d（其中峰值>=2.5%% 的 %d 笔，合计 %+.2f = 误杀）"
              % ("", kb["n"], miss, miss_pnl))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
