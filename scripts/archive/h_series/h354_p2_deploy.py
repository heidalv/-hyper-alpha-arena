# -*- coding: utf-8 -*-
"""H354 P2 形态（60s VWAP 回归）实盘微试跑部署。

动作（一次幂等）：
  1. lane_registry(mm_asterdex).meta.params.vwap_revert_bp = 2.0
  2. meta.stats_since 重置为 now（新统计时代，试跑判定用）
  3. meta.ops_changes 追加审计条目（回滚 = 设回 0）
  4. meta.h354_p2_trial 写入试跑元信息（供 12h 判定脚本读取）

回滚（12h 判定不达标或用户叫停时）：
   python scripts/h354_p2_deploy.py --rollback
   ⇒ vwap_revert_bp = 0，stats_since 重置，ops_changes 记回滚。

用法: python scripts/h354_p2_deploy.py [--rollback] [--bp 2.0]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
DEFAULT_BP = 2.0


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--bp", type=float, default=DEFAULT_BP)
    a = ap.parse_args()

    import psycopg

    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id = %s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = row[0] or {}
            meta = json.loads(meta) if isinstance(meta, str) else dict(meta)
            params = dict(meta.get("params") or {})
            old_bp = float(params.get("vwap_revert_bp") or 0.0)
            new_bp = 0.0 if a.rollback else a.bp

            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            ops = list(meta.get("ops_changes") or [])
            ops.append({
                "ts": now_iso,
                "action": "h354_p2_rollback" if a.rollback else "h354_p2_microtrial",
                "field": "params.vwap_revert_bp",
                "from": old_bp,
                "to": new_bp,
                "note": ("P2 试跑回滚：VWAP 回归闸关闭" if a.rollback
                         else "P2 形态微试跑：偏离60s VWAP≥2bp 只挂回归侧；"
                              "回滚=--rollback；判定=12h 后 h354 判定脚本"),
            })
            meta["ops_changes"] = ops[-20:]
            pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
            params["vwap_revert_bp"] = new_bp
            meta["params"] = params
            if not a.rollback:
                meta["h354_p2_trial"] = {
                    "started_at": now_iso,
                    "bp": new_bp,
                    "baseline_params": {"vwap_revert_bp": old_bp},
                    "rollback_to": 0.0,
                    "judge_at": (dt.datetime.now(dt.timezone.utc)
                                 + dt.timedelta(hours=12)).isoformat(),
                    "criteria": "12h 后对比 12h 前基线（era 对比）与同期 h326 口径 net_bp/腿",
                }
            else:
                meta["h354_p2_trial"] = {
                    **dict(meta.get("h354_p2_trial") or {}),
                    "rolled_back_at": now_iso,
                }

            cur.execute(
                "UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id = %s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
            c.commit()

    print("=" * 88)
    print(f"h354 P2 微试跑 {'回滚' if a.rollback else '部署'} 完成：")
    print(f"  车道            : {LANE}")
    print(f"  vwap_revert_bp  : {old_bp} → {new_bp}")
    print(f"  stats_since     : {now_iso}（新统计时代）")
    print(f"  ops_changes     : {len(ops)} 条（保留最近 20）")
    print("=" * 88)
    print("下一步：python scripts/h218_restart_worker.py  （加载 vwap60 新代码）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
