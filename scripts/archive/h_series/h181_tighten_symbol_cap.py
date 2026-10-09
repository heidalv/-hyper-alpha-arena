# -*- coding: utf-8 -*-
"""[H181 2026-09-21] 收紧单币持仓上限（用户选定方案 ①）。

# 用户选定 ①

「收紧单币持仓上限 —— 超过就只允许减仓侧报价，强制不再累积。」

# 原提的 1.5 被数据否掉

H180 实测本时代 400 个周期的**峰值持仓折合"单腿数"**：

    p50 0.63   p75 1.21   **p90 2.14**   p99 3.70   最大 4.24

| ratio | 单币上限$ | 折合腿数 | 封住的周期% | 判定 |
|---|---|---|---|---|
| **1.5** | $444 | 0.75 | **47.0%** | ✗ **装不下一条腿 ⇒ 加仓侧永久封死 ⇒ 做市停摆** |
| 2.0 | $592 | 1.00 | 32.2% | ⚠️ 仅容一条腿 |
| **4.0** | $1,183 | **2.00** | **13.2%** | ✓ **采用** |
| 6.0（现状） | $1,775 | 3.00 | 3.2% | ← 允许堆 3 条腿，就是用户看到的问题 |

⇒ 采用 **4.0**（2 条腿上限）：封住 13.2% 的周期（**正是堆到 2 条腿以上、出问题的那些**），
保留 86.8% 的正常往返。

# 最坏损失的变化

    单币满仓 = 4.0 × 权益 = $1,183
    单币打止损 = 40bp × $1,183 = **$4.73**（1.60% 权益）
    3 币同时打止损（最坏） = **$14.20 = 4.8% 权益**
    对比现状（6.0）：单币 $7.10、三币 **$21.30 = 7.2% 权益**

# 为什么收紧上限不会卡死出库（关键）

`core.check_side_allowed` 第 1220-1221 行：

```python
q = book.qty(symbol)
signed = 1.0 if side in ("buy","long","b") else -1.0
if abs(q) > 1e-12 and q * signed < 0:
    return True, "reduce"        # ← 减仓方向**永远允许**，先于任何敞口闸
```

⇒ 收紧 `max_net_directional_ratio` **只拦加仓侧**，减仓/出库不受影响。

用法：
    .venv\\Scripts\\python.exe scripts\\h181_tighten_symbol_cap.py
    .venv\\Scripts\\python.exe scripts\\h181_tighten_symbol_cap.py --apply
    .venv\\Scripts\\python.exe scripts\\h181_tighten_symbol_cap.py --rollback
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
TARGET = {"max_net_directional_ratio": 4.0}


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
            print("H181  收紧单币持仓上限")
            print("=" * 92)

            if a.rollback:
                saved = meta.get("h181_rollback") or old
                p.update({k: v for k, v in saved.items() if v is not None})
                meta["params"] = p
                meta.pop("h181_rollback", None)
                cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                            " WHERE lane_id=%s",
                            (json.dumps(meta, ensure_ascii=False, default=str), LANE))
                c.commit()
                print(f"\n  已回退：{saved}")
                return 0

            eq = 295.87
            cr = float(p.get("compound_ratio") or 0.0)
            leg = cr * eq
            print(f"\n  {'参数':<32} {'旧':>8} {'新':>8}")
            for k, v in TARGET.items():
                print(f"  {k:<32} {str(old[k]):>8} {v:>8}")
            print(f"\n  权益 ${eq:,.0f}  compound_ratio={cr} ⇒ 单腿 ${leg:,.0f}")
            print(f"  单币上限：{old['max_net_directional_ratio']}× = "
                  f"${old['max_net_directional_ratio']*eq:,.0f}"
                  f" → {TARGET['max_net_directional_ratio']}× = "
                  f"${TARGET['max_net_directional_ratio']*eq:,.0f}")
            print(f"  折合腿数：{old['max_net_directional_ratio']*eq/leg:.2f} → "
                  f"**{TARGET['max_net_directional_ratio']*eq/leg:.2f} 条腿**")
            print(f"\n  最坏损失（3 币同时满仓打止损）：")
            print(f"    旧 {3*0.0040*old['max_net_directional_ratio']*eq:,.2f} USD"
                  f"（{3*0.0040*old['max_net_directional_ratio']*100:.1f}% 权益）")
            print(f"    新 **{3*0.0040*TARGET['max_net_directional_ratio']*eq:,.2f} USD**"
                  f"（{3*0.0040*TARGET['max_net_directional_ratio']*100:.1f}% 权益）")
            print(f"\n  ⚠️ 收紧只拦**加仓侧**；减仓/出库侧在 check_side_allowed 里")
            print(f"     先于任何敞口闸返回 True ⇒ **不会卡死出库**。")

            if not a.apply:
                print("\n  （预览）加 --apply 执行")
                return 0

            meta.setdefault("h181_rollback", old)
            p.update(TARGET)
            meta["params"] = p
            ops = list(meta.get("ops_changes") or [])
            ops.append({"by": "h181_tighten_symbol_cap",
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "reason": "用户选定方案①：三仓全空、总敞口 5.48× 权益（净敞口全同向），"
                                  "由行情项主导盈亏。H180 实测峰值持仓 p90=2.14 条腿 ⇒ "
                                  "上限设 4.0（2 条腿），封住 13.2% 的堆积周期；"
                                  "原提的 1.5 会封死 47% 的周期（装不下一条腿）",
                        "old": old, "new": TARGET})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()
    print(f"\n  => 已写入注册表。不在 env 白名单 ⇒ 热更新，≤60s 生效，无需重启。")
    print(f"     生效核对：心跳 `limits.max_net_directional_ratio` 应为 4.0；")
    print(f"     之后 `skip_counts.symbol_exposure` 会上升（这是预期的：堆积被拦）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
