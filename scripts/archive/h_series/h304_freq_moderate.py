# -*- coding: utf-8 -*-
"""H304 适度提频（用户反馈频次低）：θ3→2bp + 波动入场门 0.5→0.3。

# 频次诊断（5000 时代 2.7h）：skip 主因 = model_below_thr 4314 / vol_regime 1340 /
#   symbol_exposure 1026；side_counts none=6510（一半决策不挂单）。
# 权衡（12h 影子）：θ0 候选 −0.87bp < θ3 的 −0.74bp——完全放开会多亏；
#   但跳变速退已关（昨晚 −$340 的出血口），基线质量正在改善（23:00 时 −7.3 只损失）。
#   故取中间档：θ 3→2（放 1/3 被拦信号）+ vol 门 0.5→0.3（放部分波动时段）。

# 用法
    python scripts/h304_freq_moderate.py            # 应用 + 验证热采用
    python scripts/h304_freq_moderate.py --rollback
    python scripts/h304_freq_moderate.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h304_freq_moderate_state.json"
TARGET = {"side_trend_min_bp": 2.0, "vol_pause_mult": 0.3}
KEYS = list(TARGET.keys())


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


def read_params() -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            return dict(cur.fetchone()[0] or {})


def write_params(patch: dict) -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            for key, val in patch.items():
                cur.execute(
                    "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                    " %s::text[], to_jsonb(%s::float8), true), updated_at=now()"
                    " WHERE lane_id=%s", (f"{{params,{key}}}", float(val), LANE))
        c.commit()


def live_params() -> dict:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    d = dict(j.get("params") or {})
    for k, v in dict(j.get("limits") or {}).items():
        d.setdefault(k, v)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    cur = read_params()
    print("=" * 96)
    print("H304  适度提频：θ 3→2bp + vol 门 0.5→0.3")
    print("=" * 96)

    if a.rollback:
        if not STATE.exists():
            print("  ✗ 无快照")
            return 1
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        orig = snap["original"]
        write_params(orig)
        t0 = time.time()
        while time.time() - t0 < 150:
            live = live_params()
            if all(abs(float(live.get(k) or 0) - float(orig.get(k) or 0)) < 1e-9 for k in KEYS):
                STATE.unlink(missing_ok=True)
                print(f"  ✓ 已恢复 { {k: orig.get(k) for k in KEYS} }")
                return 0
            time.sleep(5)
        print("  ⚠️ 150s 未确认")
        return 1

    if a.status:
        print(f"  注册表: { {k: cur.get(k) for k in KEYS} }")
        print(f"  实盘:   { {k: live_params().get(k) for k in KEYS} }")
        return 0

    print(f"  当前: { {k: cur.get(k) for k in KEYS} }")
    print(f"  目标: {TARGET}")
    STATE.write_text(json.dumps({
        "saved_at": dt.datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": {k: cur.get(k) for k in KEYS},
        "reason": "用户反馈频次低：θ3→2 放 1/3 被拦信号；vol 门 0.5→0.3 放部分波动时段（跳变速退已关，基线改善中）",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 快照存 {STATE}")
    write_params(TARGET)
    print("  已写入，等热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_params()
        if all(abs(float(live.get(k) or 0) - TARGET[k]) < 1e-9 for k in KEYS):
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）：{ {k: live.get(k) for k in KEYS} }")
            return 0
        time.sleep(5)
    print(f"  ⚠️ 150s 未确认（实盘 { {k: live_params().get(k) for k in KEYS} }）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
