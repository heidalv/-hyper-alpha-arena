# -*- coding: utf-8 -*-
"""Z44：相关性桶并发上限的反事实验证（第 4 轮，(c)/(d) 项接续）。

背景（§41.3）：`risk_band_resolver.check_bucket_can_open()` 已实现但**从未接线**，
而 `settings.CORRELATION_BUCKETS` 已按实测相关性定义（majors=3 / mid-alt=1 / indep=1）。
本轮在**现有全局 cap=4 之上**再叠加桶级上限，做 75 天时间顺序回放：
  - 语义与生产一致：`当前桶内持仓数 >= 桶上限` 即拦；未入桶的币不设限（get_correlation_bucket 返回 None）；
  - 仍持仓（status=open）的仓位永远占名额且不被跳过；
  - 被跳过的仓位不占名额、不重投资金。
输出：总 USD / 均值 / 胜率 / 模式率 / ≤-2% / 逐月 / walk-forward / 9-9 夜取舍。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_audit_ml"))

from Z35_concurrency_cap import agg, load  # noqa: E402

from backend.config.settings import CORRELATION_BUCKETS  # noqa: E402

BUCKET_OF = {}
for _b in CORRELATION_BUCKETS:
    for _s in _b.get("symbols", []):
        BUCKET_OF.setdefault(str(_s).upper(), _b["name"])
CAP_OF = {b["name"]: int(b["max_concurrent_positions"]) for b in CORRELATION_BUCKETS}


def bucket_of(sym: str):
    return BUCKET_OF.get(str(sym or "").upper())


def replay(recs, *, global_cap=4, bucket_scale=1.0, bucket_cap_override=None):
    """时间顺序贪婪回放：同时受全局上限与桶级上限约束。"""
    caps = dict(CAP_OF)
    if bucket_cap_override:
        caps.update(bucket_cap_override)
    if bucket_scale != 1.0:
        caps = {k: max(1, int(round(v * bucket_scale))) for k, v in caps.items()}
    open_list, keep, skip = [], [], []
    for r in recs:
        open_list = [x for x in open_list if x["close_ts"] > r["open_ts"]]
        if r["is_open"]:
            open_list.append(r)
            continue
        n_all = len(open_list)
        b = bucket_of(r["symbol"])
        n_b = sum(1 for x in open_list if bucket_of(x["symbol"]) == b) if b else 0
        blocked = (global_cap is not None and global_cap > 0 and n_all >= global_cap) or (
            b is not None and n_b >= caps.get(b, 99)
        )
        if blocked:
            skip.append(r)
        else:
            keep.append(r)
            open_list.append(r)
    return keep, skip


def line(label, keep, skip, base_usd):
    a = agg(keep)
    if not a:
        return
    bym = defaultdict(float)
    for r in keep:
        bym[r["mon"]] += r["usd"]
    bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
    print(f"{label:<34}{a['n']:>5}{a['usd']:>+10.2f}{a['mean']:>+9.3f}{a['win']:>7.3f}"
          f"{a['pat']:>8.3f}{a['le2']:>7}{a['usd']-base_usd:>+10.2f}  {bym_s}")


def main() -> int:
    recs_all = load(75)
    recs = [r for r in recs_all if not r["is_open"]]
    actual = sum(r["usd"] for r in recs)
    n_open = len(recs_all) - len(recs)
    print(f"已平仓 n={len(recs)}（另含仍持仓 {n_open} 笔占名额）  实际 USD={actual:+.2f}")
    print(f"桶定义：{CAP_OF}；未入桶币种不设桶上限（同生产语义）")
    nb = sorted({bucket_of(r["symbol"]) or "(未入桶)" for r in recs})
    print(f"样本覆盖桶：{nb}\n")

    print(f"{'方案':<34}{'保留n':>5}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}"
          f"{'≤-2%':>7}{'ΔUSD':>10}  逐月USD")
    variants = [
        ("仅全局 cap=4（现状）", dict(global_cap=4)),
        ("+桶级上限（配置值）", dict(global_cap=4, bucket_scale=1.0)),
        ("+桶级上限 ×0.67（更紧）", dict(global_cap=4, bucket_scale=0.67)),
        ("桶级 only（无全局）", dict(global_cap=0, bucket_scale=1.0)),
        ("无任何上限（对照）", dict(global_cap=0, bucket_cap_override={k: 999 for k in CAP_OF})),
    ]
    for label, kw in variants:
        keep, skip = replay(recs_all, **kw)
        line(label, keep, skip, actual)

    print("\n=== 被桶级闸拦下的笔（配置值口径）===")
    keep0, skip0 = replay(recs_all, global_cap=4)
    keep1, skip1 = replay(recs_all, global_cap=4, bucket_scale=1.0)
    ids0 = {r["id"] for r in keep0}
    extra = [r for r in skip1 if r["id"] in ids0]      # 桶级闸新增拦下的
    if extra:
        a = agg(extra)
        print(f"  桶级闸额外拦下 {a['n']} 笔 合计 USD={a['usd']:+.2f} 均值={a['mean']:+.3f}% "
              f"模式率={a['pat']:.3f} ≤-2%={a['le2']} 最差={a['worst']:+.2f}")
        for r in sorted(extra, key=lambda x: x["usd"])[:10]:
            print(f"    #{r['id']:<5}{r['symbol']:<9}{bucket_of(r['symbol']) or '-':<9}"
                  f"{r['tier']:<6}{r['mon']} USD={r['usd']:>+8.2f} 峰值={r['peak']:>5.2f}% "
                  f"{r['opened'][:16]}")
    else:
        print("  （无新增拦截）")

    print("\n=== 9/9 夜窗口（9/9 17:00 起）===")
    for label, kw in variants[:3]:
        keep, _ = replay(recs_all, **kw)
        kid = {r["id"] for r in keep}
        ln = [r for r in recs if r["opened"] >= "2026-09-09 17:00:00"]
        kept = [r for r in ln if r["id"] in kid]
        print(f"  {label:<32} 当晚 {len(ln)} 笔 → 保留 {len(kept)} 笔 "
              f"USD={sum(r['usd'] for r in kept):+.2f}（实际 {sum(r['usd'] for r in ln):+.2f}）")

    print("\n=== walk-forward：前 2/3 选变体 → 后 1/3 验证 ===")
    cut = int(len(recs_all) * 2 / 3)
    tr_all, te_all = recs_all[:cut], recs_all[cut:]
    tr = [r for r in tr_all if not r["is_open"]]
    best = None
    for label, kw in variants:
        keep, _ = replay(tr_all, **kw)
        u = sum(r["usd"] for r in keep)
        if best is None or u > best[1]:
            best = (label, u, kw)
    print(f"  训练最优={best[0]}（训练 ${best[1]:+.2f} vs 实际 ${sum(r['usd'] for r in tr):+.2f}）")
    for label, kw in (("仅全局 cap=4", dict(global_cap=4)), (best[0], best[2])):
        keep, _ = replay(te_all, **kw)
        print(f"  验证 {label:<30} n={len(keep)} USD={sum(r['usd'] for r in keep):+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
