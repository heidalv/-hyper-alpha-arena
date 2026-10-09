# -*- coding: utf-8 -*-
"""[H127 2026-09-21] 修复「价格止损从未武装」+ 回撤杠杆 —— 给每周期亏损装上硬上界。

# 事故与数字

09:45~10:00 亏损 **$44.13**，三维分解：

    价差（我们赚的）      +$0.65
    行情漂移（我们赔的）  −$42.65   ← **96.7%**
    手续费                −$2.14

最差几笔（全是 taker 强平腿）：

    09:55:53 ONDO  名义 $755  持仓期间行情 −172.01bp  ⇒ −$13.73
    09:51:51 ASTER 名义 $1515 持仓期间行情 −104.49bp  ⇒ −$16.54
    09:51:18 DOGE  名义 $632  持仓期间行情  −89.41bp  ⇒ −$5.94

# 为什么没有止损拦住（真正的原因）

`runner.py:770`：

    if _sl_bp > 0 and _sl_vmin > 0 and sigma_norm < _sl_vmin and not _fast_arm:
        _sl_bp = 0.0        # ← 止损被清零

注册表 `stop_loss_vol_min = 1.0`，而实测 `sigma_norm ≈ 0.185~0.33`
（心跳 `avg_sigma`）⇒ **条件恒成立 ⇒ 价格止损从来没有武装过**。
实证：`lane_ledger` 里由 `stop_loss` 触发的成交在今天 5,910 笔中 **0 笔**。

于是唯一上界只剩 `max_one_side_seconds = 300s`，而它是**时间**上界不是**金额**上界：
300 秒里行情走多远，我们就赔多远（100bp × $754 = $7.5，172bp = $13.7）。

# 修什么

| 参数 | 旧 | 新 | 理由 |
|---|---|---|---|
| `stop_loss_vol_min` | 1.0 | **0.0** | 0 = 恒启用（代码注释明写"0 = 恒启用"）。原值 1.0 让止损永不生效 ✗ |
| `stop_loss_bp` | 60 | **40** | 给每周期亏损一个硬上界：40bp × 单腿 |
| `stop_loss_fast_mult` | 0.0 | **3.0** | 快武装：单步中价移动 ≥3× 波动基准时跳过慢速 σ 闸，避免"快跌中止损成交在 −75bp" |
| `compound_ratio` | 3.0 | **1.0** | 上界要配得上仓位。$754/腿 ⇒ 40bp = $3.0；$251/腿 ⇒ 40bp = $1.0 |

**不把 `compound_ratio` 降回 0.5**：用户明确要"加杠杆测试、不要怕亏"。
但**杠杆必须配上界** —— 这才是这次事故真正的教训：
不是"别加杠杆"，是"加杠杆时那个被关掉的止损会等比放大"。

# 残留风险（如实说明）

止损是**逐 tick** 判定的（15s 一拍）⇒ 两次 tick 之间的跳空不受保护。
40bp 是"下界"，不是"上限"：极端跳空仍可能超过它。
真正严格的金额上界只有"把单腿调小"，那是另一个旋钮（`compound_ratio`）。

用法：
    .venv\\Scripts\\python.exe scripts\\h127_fix_stop_loss.py            # 预览
    .venv\\Scripts\\python.exe scripts\\h127_fix_stop_loss.py --apply
    .venv\\Scripts\\python.exe scripts\\h127_fix_stop_loss.py --rollback
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
    "stop_loss_vol_min": 0.0,     # 0 = 恒启用（关键修复）
    "stop_loss_bp": 40.0,         # 每周期亏损硬上界
    "stop_loss_fast_mult": 3.0,   # 快武装，避免止损成交远低于触发线
    "compound_ratio": 1.0,        # 上界要配得上仓位
    "max_net_directional_ratio": 3.0,
    "max_net_exposure_ratio": 24.0,
    "max_gross_notional_ratio": 48.0,
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()

    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            params = dict(meta.get("params") or {})

            print("=" * 96)
            print("H127  修复「价格止损从未武装」+ 让上界配得上杠杆")
            print("=" * 96)

            if a.rollback:
                saved = meta.get("h127_rollback") or {}
                if not saved:
                    print("  没有 h127_rollback 快照，无法回退")
                    return 1
                for k, v in saved.items():
                    params[k] = v
                meta["params"] = params
                meta.pop("h127_rollback", None)
                cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                            " WHERE lane_id=%s",
                            (json.dumps(meta, ensure_ascii=False, default=str), LANE))
                conn.commit()
                print("\n  已回退：")
                for k, v in sorted(saved.items()):
                    print(f"    {k:<28} {v}")
                return 0

            print(f"\n  {'参数':<28} {'旧':>10} {'新':>10}")
            print("  " + "-" * 52)
            changed = {}
            for k, v in NEW.items():
                old = params.get(k)
                print(f"  {k:<28} {str(old):>10} {v:>10}")
                if old != v:
                    changed[k] = old

            eq = 209.78
            print(f"\n  按当前权益 ${eq:.2f} 估：")
            print(f"    单腿 ${NEW['compound_ratio']*eq:,.0f}（旧 ${3.0*eq:,.0f}）")
            print(f"    每周期亏损上界 40bp × ${NEW['compound_ratio']*eq:,.0f} "
                  f"= **${0.0040*NEW['compound_ratio']*eq:,.2f}**"
                  f"（旧：无上界，实测最大单笔 −$16.54）")

            if not a.apply:
                print(f"\n  （预览）要改 {len(changed)} 项。加 --apply 执行。")
                return 0

            if not meta.get("h127_rollback"):
                meta["h127_rollback"] = changed
            params.update(NEW)
            meta["params"] = params
            ops = list(meta.get("ops_changes") or [])
            ops.append({"by": "h127_fix_stop_loss",
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "reason": "价格止损因 stop_loss_vol_min=1.0 从未武装（今日 5910 笔成交中 "
                                  "stop_loss 触发 0 笔）；09:45~10:00 亏 $44.13 中 96.7% 是持仓期行情漂移。"
                                  "改为恒启用 + 40bp 上界 + 快武装，并下调杠杆让上界配得上仓位",
                        "old": changed, "new": NEW})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        conn.commit()
    print(f"\n  => 已写入注册表（{len(NEW)} 项）。runner 会在 ≤60s 内热采用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
