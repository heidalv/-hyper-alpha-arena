# -*- coding: utf-8 -*-
"""[2026-09-24 第8轮] 中线入场门禁的 **walk-forward 验证**（防"在同一批数据上挑门禁"的过拟合）。

协议（写死，先定规则再看结果）：
  1. 116 笔按 opened_at 排序，切成 3 折（每折 ~39 笔）；
  2. 第 k 折做**样本外**：只用前 k 折数据，从候选门禁里挑"剔除后剩余样本均/笔最高"的那一条，
     然后用它去过滤第 k+1 折，看样本外表现；
  3. 报告每折选中的门禁、样本外剩余样本的均/笔与笔数，以及"被挡掉的样本外盈亏"。

只读；不改任何参数。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import audit_mid_entry_quality_after0915_20260924 as A  # noqa: E402

GATES = [
    ("无门禁", lambda t: False),
    ("挡 低于EMA21", lambda t: (t.get("ema_dist") or 0) < 0),
    ("挡 逆模型(short)", lambda t: t.get("model_dir") == "short"),
    ("挡 模型neutral/short", lambda t: t.get("model_dir") in ("short", "neutral")),
    ("挡 16-24点入场", lambda t: t["opened_at"].hour >= 16),
    ("挡 逆模型 或 16-24点", lambda t: t.get("model_dir") == "short" or t["opened_at"].hour >= 16),
    ("挡 低于EMA21 或 16-24点",
     lambda t: ((t.get("ema_dist") or 0) < 0) or t["opened_at"].hour >= 16),
    ("挡 低于EMA21 或 逆模型",
     lambda t: ((t.get("ema_dist") or 0) < 0) or t.get("model_dir") == "short"),
]


def base(rows):
    return A.agg(rows)


def main() -> int:
    trades = A.enrich(A.load())
    trades.sort(key=lambda t: t["opened_at"])
    n = len(trades)
    k = 3
    size = n // k
    folds = [trades[i * size:(i + 1) * size] for i in range(k - 1)] + [trades[(k - 1) * size:]]
    print("样本 %d 笔 → %d 折（每折 %d 笔）" % (n, k, size))
    for i, f in enumerate(folds, 1):
        b = base(f)
        print("  折%d: n=%d 总 %+.2f 均 %+.2f 胜率 %.1f%%  (%s ~ %s)"
              % (i, b["n"], b["total"], b["avg"], b["win"],
                 str(f[0]["opened_at"])[5:16], str(f[-1]["opened_at"])[5:16]))

    print("\n== walk-forward：前 k 折选门禁 → 第 k+1 折样本外 ==")
    for kk in range(1, k):
        train = [t for f in folds[:kk] for t in f]
        test = folds[kk]
        scored = []
        for name, fn in GATES:
            kept = [t for t in train if not fn(t)]
            a = base(kept)
            scored.append((a["avg"], a["n"], name, fn))
        scored.sort(reverse=True)
        best_avg, best_n, best_name, best_fn = scored[0]
        kept_test = [t for t in test if not best_fn(t)]
        blocked_test = [t for t in test if best_fn(t)]
        at, bt = base(kept_test), base(blocked_test)
        ab = base(test)
        print("  train=折1..%d：选中『%s』（train 剩余 n=%d 均 %+.2f）" % (kk, best_name, best_n, best_avg))
        print("     样本外（折%d，无门禁基线 均 %+.2f 总 %+.2f）：剩余 n=%d 均 %+.2f 总 %+.2f ；被挡 n=%d 总 %+.2f"
              % (kk + 1, ab["avg"], ab["total"], at["n"], at["avg"], at["total"],
                 bt["n"], bt["total"]))
        # 也列出样本外每条门禁的表现，便于看稳定性
        print("     样本外各门禁：", end="")
        for name, fn in GATES:
            kept = [t for t in test if not fn(t)]
            aa = base(kept)
            print(" %s(均%+.2f,n=%d)" % (name.replace("挡 ", ""), aa["avg"], aa["n"]), end="")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
