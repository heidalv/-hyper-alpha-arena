# -*- coding: utf-8 -*-
"""[H140 2026-09-21] 开启「超时只挂单不 taker」—— 第 1 项（用户选定）。

# 一句话

**入场腿 maker 免费，taker 是 97% 亏损的来源** ⇒ 出库条件只保留"价格真不利"。

# 依据（全部来自我们自己的账本，非纸面推断）

| 事实 | 数值 | 出处 |
|---|---|---|
| 入场腿（maker）平均手续费 | **0.0000 bp** | `lane_ledger` 12,269 笔 |
| 强平腿（taker）平均手续费 | **−3.9956 bp** | `lane_ledger` 917 笔 |
| 强平腿累计 taker 费 | **−$50.87** | 同上 |
| 整夜亏损 | −$52.20 | H105 |
| ⇒ taker 费占亏损 | **97.4%** | — |
| 强平腿 `price_bp ≤ −40bp` 占比 | **10.9%** | H138 |
| 强平腿 `price_bp` 中位 | **−7.39 bp** | H138 |
| 被动出库 ≤120s 完成比例 | 96.1% | H137 |
| 被动出库 ≤300s 完成比例 | **100%** | H137 |

**⇒ 300s 窗口不是瓶颈；近 90% 的强平是「为时间付费」而非「为价格付费」。**

# 改什么

`LaneRiskLimits.timeout_exit_maker_only = True`（F296，默认 False）：

  · 超时后 **不再**打对手价平仓，改为登记 `skip="timeout_maker_only"`
    并停止加仓侧，把出库交给**减仓侧挂单**（maker，免费）
  · **taker 权只留给 ①′ 价格止损**（`stop_loss_bp=40`，已由 F295/H127 恒启用）

# 必须同时盯的代价（本改动的已知副作用）

  1. 持仓变长 ⇒ 敞口占用更久 ⇒ 可能顶到净/总敞口上限而**阻塞新仓**
     监控：`skip_counts.symbol_exposure` / `net_exposure` 是否上行
  2. 某个币若**长期不回摆**，仓位会一直挂着
     监控：`status.timeout_exit_blocked` 与 `states.*.qty` 是否长期不归零
  3. 若 ①′ 的 40bp 止损频繁触发 ⇒ 说明"真价格不利"很常见，那要重新校准阈值

回滚：`.venv\\Scripts\\python.exe scripts\\h140_enable_maker_exit.py --rollback`

用法：
    .venv\\Scripts\\python.exe scripts\\h140_enable_maker_exit.py
    .venv\\Scripts\\python.exe scripts\\h140_enable_maker_exit.py --apply
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"

FLAG = "timeout_exit_maker_only"


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

            print("=" * 92)
            print("H140  开启「超时只挂单不 taker」（出库条件只留价格）")
            print("=" * 92)

            if a.rollback:
                saved = meta.get("h140_rollback")
                if saved is None:
                    print(f"  没有 h140_rollback 快照 ⇒ 直接置回 False")
                    params[FLAG] = False
                else:
                    params[FLAG] = saved
                meta["params"] = params
                meta.pop("h140_rollback", None)
                cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                            " WHERE lane_id=%s",
                            (json.dumps(meta, ensure_ascii=False, default=str), LANE))
                conn.commit()
                print(f"\n  已回退：{FLAG} = {params[FLAG]}")
                return 0

            old = params.get(FLAG)
            print(f"\n  {FLAG:<30} 旧 = {old}")
            print(f"  {'':<30} 新 = **True**")
            print(f"\n  配套参数（本次不动，仅供核对）：")
            for k in ("stop_loss_bp", "stop_loss_vol_min", "stop_loss_fast_mult",
                      "stop_maker_grace_sec", "max_one_side_seconds", "min_hold_seconds"):
                print(f"    {k:<30} {params.get(k)}")

            if old is True:
                print("\n  已是 True，无需改动。")
                return 0
            if not a.apply:
                print("\n  （预览）加 --apply 执行")
                return 0

            meta["h140_rollback"] = old
            params[FLAG] = True
            meta["params"] = params
            ops = list(meta.get("ops_changes") or [])
            ops.append({"by": "h140_enable_maker_exit",
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "reason": "maker 免费、taker 占整夜亏损 97.4%（实测 −$50.87/−$52.20）；"
                                  "917 笔强平中 price_bp≤−40bp 仅 10.9%、中位仅 −7.39bp ⇒ "
                                  "近 90% 是为时间付费。改为超时只挂单不 taker，"
                                  "taker 权只留价格止损",
                        "changes": {FLAG: {"from": old, "to": True}}})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        conn.commit()
    print(f"\n  => 已写入注册表。runner 会在 ≤60s 内热采用（F251 指纹含 params）。")
    print(f"     生效核对：心跳 `limits.{FLAG}` 应为 true；")
    print(f"              出库改被动后 `skip_counts` 应出现 `timeout_maker_only`。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
