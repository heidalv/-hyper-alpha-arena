# -*- coding: utf-8 -*-
"""H130：宇宙反弹的归因 —— 是旧任务那次执行的滞后写入，还是有人持续覆盖。

每 20s 同时读「注册表 symbols」+「注册表 updated_at」+「心跳 symbols」+「ops_changes 尾部」。
若 ops_changes 尾部的 `after` 是 10 币、时间戳落在 10:04 之后 ⇒ 是旧脚本那次执行迟到的写入。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]


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
    for i in range(4):
        with psycopg.connect(dsn()) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json->'symbols', updated_at FROM lane_registry"
                            " WHERE lane_id='mm_asterdex'")
                syms, upd = cur.fetchone()
                cur.execute("""SELECT meta_json->'ops_changes' FROM lane_registry
                               WHERE lane_id='mm_asterdex'""")
                ops = cur.fetchone()[0] or []
        st = json.loads(Path("logs/mm_lane_status.json").read_text(encoding="utf-8"))
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 注册表 updated_at={upd.strftime('%H:%M:%S')}")
        print(f"    注册表 symbols ({len(syms)}): {syms}")
        print(f"    心跳   symbols ({len(st.get('symbols') or [])}): {st.get('symbols')}")
        if ops:
            o = ops[-1]
            print(f"    最后一条 ops_changes: by={o.get('by')} ts={o.get('ts')} "
                  f"after={o.get('after') or (o.get('new') and 'params') or ''}")
        if i < 3:
            time.sleep(20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
