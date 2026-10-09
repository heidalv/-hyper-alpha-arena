# -*- coding: utf-8 -*-
"""H182：确认/复原 A/B 中断后的配置，并打印当前完整生效参数。

A/B 脚本在 `finally` 里恢复配置，但被 SIGTERM 杀掉时 `finally` 不会跑
⇒ 必须手动核对，否则会留下"B 臂参数 + 无 A/B 在跑"的隐性状态。

用法：
    .venv\\Scripts\\python.exe scripts\\h182_restore_after_ab.py
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
# A 臂（对照）= 用户当前想要的稳态
STEADY = {"reduce_quote_disabled": False, "timeout_exit_maker_only": True}


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
    print("=" * 84)
    print("H182  A/B 中断后的配置核对/复原")
    print("=" * 84)

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            p = dict(meta.get("params") or {})
            need = {k: v for k, v in STEADY.items() if bool(p.get(k)) != bool(v)}
            print(f"\n  当前值：{ {k: p.get(k) for k in STEADY} }")
            if need:
                print(f"  ⇒ 需要复原：{need}")
                p.update(STEADY)
                meta["params"] = p
                cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                            " WHERE lane_id=%s",
                            (json.dumps(meta, ensure_ascii=False, default=str), LANE))
                c.commit()
                print("  已复原。")
            else:
                print("  ⇒ 已是稳态配置，无需改动。")

    stf = ROOT / "logs" / "mm_lane_status.json"
    j = json.loads(stf.read_text(encoding="utf-8"))
    lim, par = j.get("limits") or {}, j.get("params") or {}
    print(f"\n  ── 心跳里的完整生效参数 ──")
    print(f"  {'参数':<32} {'值':>12}")
    print("  " + "-" * 46)
    # ⚠️ 必须**两个字典都查**：`QuoteParams` 落 `params`，`LaneRiskLimits` 落 `limits`。
    #    第一版只查 `params` ⇒ `take_profit_bp`/`stop_loss_bp` 等全显示 None（假缺失）。
    for k in ("compound_ratio", "spread_mult", "spread_mult_reduce"):
        print(f"  {k:<32} {str(par.get(k)):>12}")
    print("  " + "-" * 46)
    for k in ("take_profit_bp", "take_profit_maker_grace_sec",
              "stop_loss_bp", "stop_loss_vol_min", "stop_loss_fast_mult",
              "max_one_side_seconds", "min_hold_seconds",
              "reduce_quote_disabled", "timeout_exit_maker_only",
              "max_net_directional_ratio", "max_net_exposure_ratio",
              "max_gross_notional_ratio", "daily_loss_stop_pct"):
        v = lim.get(k, par.get(k))
        print(f"  {k:<32} {str(v):>12}")
    print(f"\n  宇宙 {j.get('symbols')}   权益 ${(j.get('equity') or 0):,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
