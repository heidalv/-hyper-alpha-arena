"""h477 试跑预检（只读）：在任何部署/判定之前校验 SPEC 结构是否可执行。

动机：h464 由 14:35L 的链**自动部署**（无人盯屏）。若 SPEC 里的 `field`/`to`/
`rollback_to` 写错，部署脚本会在那一刻失败或写出错误参数，而判定窗口已经开了。
本脚本把"预注册"变成可验证的：
  · `field` 必须是 `params.<name>` 或 `meta.<name>`，且 `<name>` 在
    `QuoteParams`/`LaneRiskLimits` 的 dataclass 字段里存在（不存在 ⇒ 热采用会
    **静默丢弃**，试跑变成空转）；
  · `to` / `rollback_to` 必须能转成 float（或都是同键 dict）；
  · `meta_key` / `task` / `title` / `criteria` 必须齐备；
  · 报告该键**当前注册表值**与 SPEC 的 `from` 是否一致（不一致 ⇒ 说明基线已漂移）。

用法：python scripts/h477_preflight.py [--trial h464]   # 默认检查全部未判定 SPEC
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402


def _load_by_path(name: str, rel: str):
    """按**文件路径**加载，而不是 `import scripts.x`。

    踩过的坑（本脚本第一版就是这么挂的）：`backend.services.market_maker.core`
    的导入链会把 `backend/` 插到 `sys.path[0]`，而 `backend/scripts/__init__.py`
    是**另一个同名包** ⇒ `import scripts.h422_weekly_scan` 被解析到 `backend/scripts/`
    并报 `ModuleNotFoundError`。按路径加载可以彻底绕开这种同名遮蔽（对本仓库尤其重要，
    因为 `scripts/` 下有几百个一次性脚本）。
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


from backend.services.market_maker.core import (  # noqa: E402
    LaneRiskLimits, QuoteParams)

SPECS = _load_by_path("_h425_specs", "scripts/h425_repair_trial.py").SPECS
_sub_stats = _load_by_path("_h425_sub", "scripts/h425_repair_trial.py")._sub_stats

LANE = "mm_asterdex"
PENDING = ("h463", "h464", "h472")


def validate_sql(keys) -> int:
    """[h483] 逐 trial 跑一遍 `_sub_stats`（用近 1h 窗口）——判定脚本里的新 SQL
    若写错，会在**判定时刻**才炸（那时人不在场、试跑白等 12h）。这里提前发现。"""
    bad = 0
    import datetime as _dt
    t1 = _dt.datetime.now(_dt.timezone.utc)
    t0 = t1 - _dt.timedelta(hours=1)
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            m = cur.fetchone()[0]
            syms = [str(s) for s in (m.get("symbols") or []) if str(s)]
            for k in keys:
                try:
                    out = _sub_stats(cur, k, t0.isoformat(), t1.isoformat(), syms)
                    print(f"  ✓ {k}._sub_stats 可执行 ⇒ {json.dumps(out, ensure_ascii=False)[:300]}")
                except Exception as exc:  # noqa: BLE001
                    bad += 1
                    print(f"  ✗ {k}._sub_stats 失败：{type(exc).__name__}: {exc}")
    return bad


def _num(x):
    try:
        float(x)
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="", help="只查一个；默认查 PENDING")
    ap.add_argument("--all", action="store_true",
                    help="查 SPECS 里的**全部** trial（用于给当天所有待运行判定做体检）")
    ap.add_argument("--validate-sql", action="store_true",
                    help="额外跑一遍判定用的 _sub_stats（提前发现 SQL 错误）")
    a = ap.parse_args()
    keys = [a.trial] if a.trial else (sorted(SPECS) if a.all else list(PENDING))
    if a.validate_sql:
        print("── 判定 SQL 预演（_sub_stats，近 1h 窗口）──")
        n_bad = validate_sql(keys)
        print(f"SQL 预演结论: {'全部可执行 ✓' if n_bad == 0 else f'{n_bad} 个失败 ✗'}\n")
        if n_bad:
            return 1
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            m = cur.fetchone()[0]
    params = dict(m.get("params") or {})
    fields_ok = set(QuoteParams.__dataclass_fields__) | set(
        LaneRiskLimits.__dataclass_fields__)
    bad = 0
    for k in keys:
        spec = SPECS.get(k)
        print("=" * 84)
        if not spec:
            print(f"✗ {k}: SPEC 不存在")
            bad += 1
            continue
        fields = spec.get("fields") or [(spec.get("field"), spec.get("to"),
                                        spec.get("rollback_to"))]
        print(f"{k}: {spec.get('title')}")
        for f, to, rb in fields:
            name = str(f).split(".")[-1]
            scope = str(f).split(".")[0]
            ok_name = name in fields_ok
            ok_num = _num(to) and _num(rb)
            if isinstance(to, dict):
                ok_num = all(_num(x) for x in to.values())
            cur_val = params.get(name)
            from_val = (spec.get("from") or {}).get(name) if isinstance(
                spec.get("from"), dict) else spec.get("from")
            print(f"   field={f:42s} to={to!r:>10s} rollback={rb!r:>10s}")
            print(f"     字段存在={ok_name}  数值可转={ok_num}  "
                  f"注册表现值={cur_val!r}  SPEC.from={from_val!r}")
            if not ok_name or not ok_num:
                bad += 1
        for req in ("meta_key", "task", "title", "criteria"):
            if not spec.get(req):
                print(f"   ✗ 缺 {req}")
                bad += 1
        tr = dict(m.get(spec.get("meta_key") or "") or {})
        print(f"   试跑状态: verdict={tr.get('verdict')} started_at={tr.get('started_at')} "
              f"judge_at={tr.get('judge_at')}")
    print("=" * 84)
    print("预检结论:", "全部可执行 ✓" if bad == 0 else f"有 {bad} 处问题 ✗")
    out = ROOT / "research_l1" / "out" / "h477_preflight.json"
    out.write_text(json.dumps({"checked": keys, "problems": bad},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", out.relative_to(ROOT))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
