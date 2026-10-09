# -*- coding: utf-8 -*-
"""[H120 2026-09-21] 强度升级：把杠杆/持仓窗口/日亏闸一次调到"敢试"的档位。

# 为什么

用户明确要求：「aster 用的是合约交易，加杠杆测试，放大本金，测试看看情况怎么样，
**不要怕亏**」。这是**模拟仓（paper，不花钱）**，唯一目的是**尽快把设计试出来**。
此前的默认档位（`compound_ratio=0.5`、`max_symbol_notional_ratio=1.0`、
`daily_loss_stop_pct=25`）让每一轮实验都慢且容易被闸门打断，反馈速度不够。

# 改什么（逐项都有理由，不是乱调）

| 参数 | 旧 | 新 | 理由 |
|---|---|---|---|
| `compound_ratio` | 0.5 | **3.0** | 杠杆 = 这个数 × 权益。$251 权益 ⇒ 单腿 $753（旧 $125，**6×**） |
| `max_symbol_notional_ratio` | 1.0 | **5.0** | 单币上限 = 权益 × 此值。旧值 $251 **小于新单腿** ⇒ 会把加仓侧卡死（改杠杆必须同步改它） |
| `max_net_exposure_ratio` | 4.0 | **8.0** | 组合净敞口上限。10 币同时同向时旧值会顶穿 |
| `min_hold_seconds` | 30 | **0** | 让引擎能立刻走被动出库路径，而不是被"最少持有 30s"按住 |
| `max_one_side_seconds` | 120 | **300** | H97 实测：被动出库 ≤60s 84.6%、≤120s 98.5%、≤300s 99.6%，而 60→300s 只多付 **+0.69bp** 漂移 vs 省下 **4.36bp** taker ⇒ 每笔强平净赚 **+3.67bp** |
| `daily_loss_stop_pct` | 25 | **80** | 硬闸会把实验打断。$251 × 80% ⇒ 闸位 ≈ $50，留足试错空间又不至于归零 |

**不动 `vol_pause_sigma=0.7`**：H112 实测高波动档强平率 11.6% / 每周期 −0.03419，
最低档 6.7% / −0.00614（1.74×），这个闸是有实测依据的，不属于"该拆的保护"。

# 生效链路（必须三方一致，否则出现"改了没生效"）

注册表 `lane_registry.meta_json.params` 是权威；`.env` 里**只有**
`MM_SPREAD_MULT / MM_SPREAD_MULT_REDUCE / MM_MIN_EDGE_FRAC / MM_SPREAD_CROSS_MARGIN /
MM_W_BASE_BP / MM_MIN_WIDTH_BP / MM_K_INV` 这 7 个键能覆盖注册表
（见 `apply_env_param_overrides`）。本脚本改的 6 个参数**都不在其中**
⇒ 只写注册表即可，且写入后 runner 会在 ≤60s 内热采用（F251/F282 指纹含 params）。

用法：
    .venv\\Scripts\\python.exe scripts\\h120_escalate.py            # 预览
    .venv\\Scripts\\python.exe scripts\\h120_escalate.py --apply    # 执行
    .venv\\Scripts\\python.exe scripts\\h120_escalate.py --rollback # 回退
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"

NEW = {
    "compound_ratio": 3.0,
    # ── 上限必须按「单腿金额」重算，否则加仓侧永远挂不出去 ──────────────
    # 单腿 = compound_ratio × 权益 = 3.0 × $251 ≈ **$754**。
    #
    # 实测踩了两次坑：
    #   ① 改 `max_symbol_notional_ratio` **完全无效** —— core.py:678 明写
    #      「未接线，见上（历史字段）」，真正判定单币的是下面这个
    #      `max_net_directional_ratio`（core.py:1161）。
    #   ② 第一版把它留在 1.0 = $251 < 腿 $754 ⇒ `symbol_exposure` 拦截数
    #      从 21 一路涨到 59，**一笔新仓都开不出来**（quoted_decisions 在涨，
    #      fills 不动，从这个组合就能看出是"报了价但全被闸掉"）。
    "max_net_directional_ratio": 4.0,          # 单币 ≤ 权益 4× = $1,004 > 腿 $754 ✓
    "max_symbol_notional_ratio": 12.0,         # 未接线，顺手对齐免得误导后人
    "max_net_exposure_ratio": 32.0,            # 10 币同向最坏净 $7,540
    "max_gross_notional_ratio": 64.0,          # 总敞口 Σ|仓位| 最坏 $15,080
    "min_hold_seconds": 0.0,
    "max_one_side_seconds": 300.0,
    "daily_loss_stop_pct": 80.0,
}


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


def load_meta(cur) -> dict:
    cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"车道 {LANE} 不存在")
    return dict(row[0] or {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()

    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            meta = load_meta(cur)
            params = dict(meta.get("params") or {})

            print("=" * 100)
            print("H120  强度升级（模拟仓，目标是尽快试出设计）")
            print("=" * 100)

            if a.rollback:
                saved = meta.get("h120_escalate_rollback") or {}
                if not saved:
                    print("  没有找到 h120_escalate_rollback 快照 ⇒ 无法回退。")
                    return 1
                for k, v in saved.items():
                    params[k] = v
                meta["params"] = params
                meta.pop("h120_escalate_rollback", None)
                ops = list(meta.get("ops_changes") or [])
                ops.append({"ts": datetime.now().isoformat(timespec="seconds"),
                            "by": "h120_escalate", "action": "rollback",
                            "restored": saved})
                meta["ops_changes"] = ops[-40:]
                cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                            " WHERE lane_id=%s", (json.dumps(meta, ensure_ascii=False,
                                                             default=str), LANE))
                conn.commit()
                print("\n  已回退：")
                for k, v in sorted(saved.items()):
                    print(f"    {k:<34} {v}")
                return 0

            print(f"\n  {'参数':<34} {'旧':>12} {'新':>12}   {'倍数':>7}")
            print("  " + "-" * 72)
            changed = {}
            for k, v in NEW.items():
                old = params.get(k)
                try:
                    mult = f"{v/float(old):.1f}x" if old not in (None, 0) else "—"
                except Exception:
                    mult = "—"
                print(f"  {k:<34} {str(old):>12} {v:>12}   {mult:>7}")
                if old != v:
                    changed[k] = old

            print(f"\n  未改动（有实测依据，不拆）：")
            for k in ("vol_pause_sigma", "spread_mult", "spread_mult_reduce",
                      "spread_cross_margin", "stop_loss_bp"):
                print(f"    {k:<34} {params.get(k)}")

            if not changed:
                print("\n  全部已是目标值，无需改动。")
                return 0

            if not a.apply:
                print(f"\n  （预览模式）要改 {len(changed)} 项。加 --apply 执行。")
                return 0

            if not meta.get("h120_escalate_rollback"):
                meta["h120_escalate_rollback"] = changed
            params.update(NEW)
            meta["params"] = params
            ops = list(meta.get("ops_changes") or [])
            ops.append({"ts": datetime.now().isoformat(timespec="seconds"),
                        "by": "h120_escalate", "reason":
                        "用户要求加杠杆/放大本金做实验（模拟仓不花钱）；"
                        "杠杆=compound_ratio×权益；同步放宽单币/净敞口上限以免卡死加仓侧",
                        "new": NEW, "old": changed})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s", (json.dumps(meta, ensure_ascii=False,
                                                         default=str), LANE))
            conn.commit()
            print(f"\n  ⇒ 已写入注册表（{len(NEW)} 项）。runner 会在 ≤60s 内热采用。")
            print("     权益 $251 ⇒ 单腿 ≈ **$753**（旧 ≈ $125）")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
