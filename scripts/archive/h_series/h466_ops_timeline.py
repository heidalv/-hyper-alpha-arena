"""部署审计：把 lane_registry.meta_json 里的 ops 时间线打出来（只读）。

背景：h465 小时级分解发现 **09-28 12:00→13:00 UTC（本地 20:00→21:00）** 出现断崖：
  腿速 225/h → 65/h、每腿名义 270$ → 110$、每腿手续费 −0.25bp → −1.4bp、
  净/腿 由 ±2bp 摆到稳定 −3bp。
本脚本回答"那一刻到底改了什么"——注册表里 `ops` 是每次 deploy/rollback 的审计流水。

用法：python scripts/h466_ops_timeline.py [--tail 40]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h466_ops_timeline.json"


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
    ap.add_argument("--tail", type=int, default=40)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            m = cur.fetchone()[0]
            print("meta 顶层键:", sorted(m))
            ops = list(m.get("ops") or m.get("ops_changes") or [])
            print(f"ops 条数: {len(ops)}（键={'ops' if m.get('ops') else 'ops_changes'}）")
            print("=" * 100)
            for op in ops[-a.tail:]:
                ts = str(op.get("ts") or "")
                # 转本地时间便于对照小时级表
                if ts:
                    try:
                        import datetime as dt
                        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=dt.timezone.utc)
                        local = t.astimezone(dt.timezone(dt.timedelta(hours=8)))
                        ts = f"{local:%m-%d %H:%M:%S}L/{t:%H:%M}Z"
                    except Exception:  # noqa: BLE001
                        pass
                print(f"{ts:26s} {op.get('action'):22s} field={op.get('field')}")
                frm, to = op.get("from"), op.get("to")
                if frm or to:
                    print(f"{'':26s}   from={json.dumps(frm, ensure_ascii=False)} "
                          f"-> to={json.dumps(to, ensure_ascii=False)}")
                if op.get("note"):
                    print(f"{'':26s}   note={op['note']}")
            OUT.write_text(json.dumps(ops, ensure_ascii=False, indent=2), encoding="utf-8")
            print("=" * 100)
            print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
