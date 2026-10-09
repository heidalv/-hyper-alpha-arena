# -*- coding: utf-8 -*-
"""F338 上线：把 `max_leg_notional_mult` 写进注册表（先 0.0 建立可回滚的原值）。

顺序很重要：
  ① 先写 0.0（= 关闭，与旧行为逐字一致）⇒ 建立"原值"
  ② `h188 --save` 把它记为回滚点
  ③ 再改 3.0 ⇒ 任何人事后都能 `h188 --restore` 回到关闭
  ④ 重启 worker（新代码才会读这个字段）

只写一个键，绝不整体覆写 meta（避免抹掉 rollback 快照）。
"""
from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
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
    ap.add_argument("--value", type=float, required=True,
                    help="max_leg_notional_mult 的目标值（0 = 关闭）")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            p = dict(cur.fetchone()[0] or {})
            before = p.get("max_leg_notional_mult", "<absent>")
            cur.execute("""
                UPDATE lane_registry
                   SET meta_json = jsonb_set(meta_json, '{params,max_leg_notional_mult}',
                                             to_jsonb(%s::float8), true),
                       updated_at = now()
                 WHERE lane_id = %s
            """, (a.value, LANE))
        c.commit()
    print(f"  max_leg_notional_mult: {before} → {a.value}")

    # 读回确认（写进去 ≠ 写对了）
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params'->'max_leg_notional_mult'"
                        " FROM lane_registry WHERE lane_id=%s", (LANE,))
            got = cur.fetchone()[0]
    print(f"  读回确认：{got}")
    if got is None or abs(float(got) - a.value) > 1e-12:
        print("  ✗ 读回与写入不一致")
        return 1
    print("  ✓ 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
