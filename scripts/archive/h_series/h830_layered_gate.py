# -*- coding: utf-8 -*-
"""[h830 用户"全面检查"证据落地] 分层门:把 h829 的实盘裁决写成可换上的候选门。

背景(三套标签之争的实盘裁决,h829):
  30s  +0.93bp(t=+1.44)  |  180s +0.42bp(t=+0.28)  |  300s −6.74bp(t=−2.08)
⇒ 30~180s 的真实挂单成交**不低于**中性,300s+ 才有显著逆向选择。
⇒ 现生产门(全时限同标准,最严口径)把 34 个币全关:过度保守 + 不可falsify
  (永不交易 ⇒ 永远不会被证伪 ⇒ 学习停滞)。

本脚本产出**候选门**(不覆盖生产件):对 `flow_gate_model.json` 的每个候选,
只保留满足下列全部条件的:
  ① 时限 ∈ {30, 180}(实盘裁决支持的档);
  ② 该币的考试段 mean_y > 安全垫 且 n_eff ≥ 30(协议);
  ③ 经验层:`data/flow_fill_markout.json` 在对应时限的均值 ≥ 0(实盘不为负);
写 data/flow_gate_layered.json + 打印对比,供决策(是否切换生产门)。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

ALLOWED_HZ = (30.0, 180.0)
MARGIN_BP = 1.0
MIN_N_EFF = 30.0


def _j(name):
    try:
        return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    model = _j("flow_gate_model.json")
    mk = _j("flow_fill_markout.json")
    agg = mk.get("agg") or {}
    emp = {30.0: float((agg.get("mo_30s") or {}).get("mean_bp") or 0.0),
           180.0: float((agg.get("mo_180s") or {}).get("mean_bp") or 0.0),
           300.0: float((agg.get("mo_300s") or {}).get("mean_bp") or 0.0)}
    print(f"经验层(实盘挂单成交后漂移): 30s {emp[30.0]:+.2f}bp | "
          f"180s {emp[180.0]:+.2f}bp | 300s {emp[300.0]:+.2f}bp")
    out = {"ts": time.time(), "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "protocol": "horizon_layered_empirical",
           "allowed_horizons": list(ALLOWED_HZ),
           "empirical_markout": emp, "gates": {}}
    n_open = 0
    for sym, v in (model.get("gates") or {}).items():
        hz = float(v.get("horizon_sec") or v.get("max_hold_sec") or 0.0)
        oos = v.get("oos") or {}
        mean_y = oos.get("mean_y")
        ne = oos.get("n_eff")
        mu = v.get("mu")
        reasons = []
        if hz not in ALLOWED_HZ:
            reasons.append(f"horizon_not_layered({hz})")
        if mean_y is None or float(mean_y) <= MARGIN_BP:
            reasons.append("oos_not_positive")
        if ne is None or float(ne) < MIN_N_EFF:
            reasons.append("n_eff_low")
        if mu is None or float(mu) <= MARGIN_BP:
            reasons.append("mu_low")
        if emp.get(hz, 0.0) < 0.0:
            reasons.append(f"empirical_negative({emp.get(hz):+.2f})")
        allow = not reasons
        if allow:
            n_open += 1
        out["gates"][sym] = {
            "allow": allow,
            "side": (v.get("side") if allow else None),
            "mu": mu, "max_hold_sec": hz, "horizon_sec": hz,
            "oos": {"mean_y": mean_y, "n_eff": ne, "win_rate": oos.get("win_rate")},
            "reason": ("ok" if allow else ";".join(reasons)),
            "empirical_bp": emp.get(hz),
        }
        tag = "✅ 开" if allow else "   关"
        print(f"  {tag} {sym:<8} H={hz:>5.0f}s mu={mu} OOS={mean_y} n_eff={ne} "
              f"经验={emp.get(hz, 0.0):+.2f} {'' if allow else '| ' + ';'.join(reasons)}")
    (ROOT / "data" / "flow_gate_layered.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ⇒ 候选门开门 {n_open}/{len(out['gates'])}(未覆盖生产门;"
          f"切换需把 runner 或生产者指向 flow_gate_layered.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
