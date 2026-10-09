# -*- coding: utf-8 -*-
"""[H185 2026-09-21] 降回 compound_ratio = 1.0（用户确认）。

# 为什么

H183/H184 查实：权益 $306.53 → $290.69（**−5.17%**），亏损 **100% 来自行情项**、
手续费为 0。而价差项一直稳定为正（+0.38~+1.57/10 分钟）
⇒ **收入模型没变，是杠杆把行情风险同比放大了**。

`compound_ratio: 1.0 → 2.0` 是用户为"放大本金"设的。
在**期望仍为负**的阶段，降杠杆是唯一**不依赖任何预测**就能减少亏损的动作。

# 同批调整（必须同步，否则闸门与腿量不匹配 —— H181 的教训）

单腿 = compound_ratio × 权益。ratio 减半 ⇒ 单腿减半 ⇒
按"权益倍数"定义的敞口上限若不动，折合腿数会**翻倍**（闸门变松）。

    单腿：$592 → **$296**
    max_net_directional_ratio 4.0 → 2.5（折合腿数保持 ~2 条）
    max_net_exposure_ratio   48.0 → 24.0
    max_gross_notional_ratio 96.0 → 48.0

用法：
    .venv\\Scripts\\python.exe scripts\\h185_reduce_leverage.py
    .venv\\Scripts\\python.exe scripts\\h185_reduce_leverage.py --apply
    .venv\\Scripts\\python.exe scripts\\h185_reduce_leverage.py --rollback
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
TARGET = {
    "compound_ratio": 1.0,
    "max_net_directional_ratio": 2.0,
    "max_net_exposure_ratio": 24.0,
    "max_gross_notional_ratio": 48.0,
}
# ⚠️ 量纲：单腿 = compound_ratio × 权益 = 1.0 × 权益 ⇒ 单币上限 2.0× 权益 = **2 条腿**
#    （与 H181 定的"2 条腿"一致。若写 2.5 就变成 2.5 条腿，等于悄悄放宽）


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            p = dict(meta.get("params") or {})
            old = {k: p.get(k) for k in TARGET}

            print("=" * 92)
            print("H185  降回 compound_ratio = 1.0")
            print("=" * 92)

            if a.rollback:
                saved = meta.get("h185_rollback") or {}
                if saved:
                    p.update(saved)
                    meta["params"] = p
                    meta.pop("h185_rollback", None)
                    cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                                " WHERE lane_id=%s",
                                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
                    c.commit()
                    print(f"\n  已回退：{saved}")
                else:
                    print("\n  无回滚快照")
                return 0

            eq = 290.69
            print(f"\n  {'参数':<32} {'旧':>8} {'新':>8}")
            print("  " + "-" * 50)
            for k, v in TARGET.items():
                print(f"  {k:<32} {str(old.get(k)):>8} {v:>8}")

            print(f"\n  按权益 ${eq:,.0f} 换算：")
            for r, lab in ((old.get("compound_ratio") or 2.0, "旧"),
                           (TARGET["compound_ratio"], "新")):
                leg = r * eq
                print(f"    {lab} 单腿 ${leg:,.0f}"
                      f"  单币上限 {TARGET['max_net_directional_ratio']}× = "
                      f"${TARGET['max_net_directional_ratio']*eq:,.0f}"
                      f" = {TARGET['max_net_directional_ratio']*eq/leg:.1f} 条腿")
            print(f"\n  最坏损失（3 币同时满仓打止损）：")
            for r, lab in ((old.get("compound_ratio") or 2.0, "旧"),
                           (TARGET["compound_ratio"], "新")):
                leg = r * eq
                cap = TARGET["max_net_directional_ratio"] * eq
                print(f"    {lab} 单币止损 = 40bp × ${cap:,.0f} = ${0.0040*cap:,.2f}"
                      f"；三币 **${3*0.0040*cap:,.2f}**"
                      f"（{3*0.0040*cap/eq*100:.2f}% 权益）")

            if not a.apply:
                print("\n  （预览）加 --apply 执行")
                return 0

            meta.setdefault("h185_rollback", old)
            p.update(TARGET)
            meta["params"] = p
            ops = list(meta.get("ops_changes") or [])
            ops.append({"by": "h185_reduce_leverage",
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "reason": "权益较峰值 −5.17%，亏损 100% 来自行情项（费=0）⇒ "
                                  "收入模型未变，是杠杆放大行情风险。在期望转正前降杠杆是"
                                  "唯一不依赖预测的减亏动作。同步按新单腿重算敞口上限",
                        "old": old, "new": TARGET})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()
    print(f"\n  => 已写入注册表。均不在 env 白名单 ⇒ 热更新，≤60s 生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
