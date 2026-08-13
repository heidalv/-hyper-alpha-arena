"""短线策略验证门禁（M1）。

按《短线因子交易评估计划》§4 的硬门逐项判定：
1. OOS 净收益 > 0
2. 随机基准 permutation p < 0.05
3. 分桶单调显著（net_ret rho p<0.05 且 rho>0）或分层组合转正
4. 样本量 >= min_samples（默认 3000）
5. parity >= 0.70（回测-纸盘一致度，v1 未建立前返回 pending）

只读判定，不改变任何交易行为；M4 前用于报告，M4 后可作为晋升硬拦。
"""
from __future__ import annotations

import math
from typing import Any, Dict


def _monotonic_ok(buckets: list) -> Dict[str, Any]:
    """对分数桶做秩相关检验。返回 (ok, detail)。"""
    if not buckets or len(buckets) < 3:
        return {"ok": False, "detail": "分桶数 < 3"}
    mids, net = [], []
    for i, b in enumerate(buckets):
        label = str(b.get("bucket", ""))
        try:
            lo = float(label.split("-")[0].replace("<", "0").replace(">=", ""))
        except Exception:
            lo = float(i * 10)
        try:
            mid = lo + 5.0
            if "<" in label:
                mid = 15.0
            elif ">=" in label:
                mid = lo + 5.0
            elif "-" in label:
                hi = float(label.split("-")[1])
                mid = (lo + hi) / 2.0
        except Exception:
            pass
        mids.append(mid)
        net.append(float(b.get("avg_net_ret", 0.0) or 0.0))

    n = len(mids)

    def _rank(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    rx, ry = _rank(mids), _rank(net)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    rho = num / (dx * dy) if dx and dy else 0.0
    try:
        t = rho * math.sqrt((n - 2) / max(1e-9, 1 - rho * rho))
        from scipy.stats import t as _t
        p = 2.0 * (1.0 - _t.cdf(abs(t), n - 2))
    except Exception:
        p = 1.0
    layered_positive = all(float(b.get("avg_net_ret", 0.0) or 0.0) > 0 for b in buckets)
    ok = (p < 0.05 and rho > 0) or layered_positive
    return {
        "ok": bool(ok),
        "detail": {
            "rho": round(rho, 4),
            "p": round(p, 5),
            "layered_positive": layered_positive,
        },
    }


def evaluate_gate(metrics: Dict[str, Any], min_samples: int = 3000) -> Dict[str, Any]:
    """对一次回测/影子结果做硬门判定。metrics 字段见 scalp_strategy_backtest。"""
    n = int(metrics.get("n", 0) or 0)
    avg_net = float(metrics.get("avg_net_ret", 0.0) or 0.0)
    perm_p = float(metrics.get("permutation_p", 1.0) or 1.0)
    buckets = metrics.get("buckets") or []
    parity = metrics.get("parity_score")

    gates: Dict[str, Dict[str, Any]] = {
        "net_oos_positive": {
            "ok": avg_net > 0,
            "value": round(avg_net, 6),
        },
        "random_benchmark": {
            "ok": perm_p < 0.05,
            "value": round(perm_p, 4),
        },
        "bucket_monotonic": {
            "ok": bool(_monotonic_ok(buckets)["ok"]),
            "value": _monotonic_ok(buckets)["detail"],
        },
        "min_samples": {
            "ok": n >= min_samples,
            "value": n,
        },
    }
    if parity is None:
        gates["parity"] = {"ok": None, "value": None, "status": "pending"}
    else:
        gates["parity"] = {
            "ok": float(parity) >= 0.70,
            "value": round(float(parity), 3),
        }

    fails = [k for k, g in gates.items() if g.get("ok") is False]
    pending = [k for k, g in gates.items() if g.get("ok") is None]
    if fails:
        verdict = "fail"
    elif pending:
        verdict = "pending"
    else:
        verdict = "pass"
    if n < min_samples:
        verdict = "insufficient_data"

    return {
        "verdict": verdict,
        "gates": gates,
        "failed": fails,
        "pending": pending,
        "min_samples": min_samples,
    }
