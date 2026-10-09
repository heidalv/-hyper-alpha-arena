"""h544：**验证**判定框架新增的字典参数支持（不部署任何东西）。

h527 的 SPEC 值是 `{"NEAR": 0.35, ...}` 这类字典，而框架原先 `float(t)` 会直接
TypeError ⇒ 无法通过「预注册 → 试跑 → 判定 → 自动回滚」链路部署逐币参数。
`h543` 补了 `_coerce_param` / `_current_of`，本脚本用**不落库**的方式验收它们：

  1. 数值/数值字符串仍转 float（既有 SPEC 行为逐字不变）；
  2. 字典原样通过且内部值逐个 float；
  3. 字典里出现非数值 ⇒ 明确报错（不静默吞掉）；
  4. `_current_of`：字典原样返回、缺失回退 default、非法回退 default；
  5. `SPECS["h527"]` 能被解析、`to`/`rollback_to` 形状正确；
  6. `--judge --dry-run` 对 h527 可执行（判定路径不炸）。

用法：python scripts/h544_verify_dict_params.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.h425_repair_trial import (  # noqa: E402
    SPECS, _coerce_param, _current_of,
)


def main() -> int:
    fails: list[str] = []

    def check(name: str, got, want) -> None:
        ok = got == want
        print(f"  {'✓' if ok else '✗'} {name}: {got!r}")
        if not ok:
            fails.append(f"{name}: got {got!r} want {want!r}")

    print("[1] 数值 / 数值字符串仍转 float（既有 SPEC 行为不变）")
    check("_coerce_param(45.0)", _coerce_param(45.0), 45.0)
    check("_coerce_param('0.9')", _coerce_param("0.9"), 0.9)
    check("_coerce_param({})", _coerce_param({}), {})

    print("\n[2] 字典原样通过、内部值逐个 float")
    check("_coerce_param({'NEAR':0.35})", _coerce_param({"NEAR": 0.35}), {"NEAR": 0.35})
    check("_coerce_param({'A':'0.5'})", _coerce_param({"A": "0.5"}), {"A": 0.5})

    print("\n[3] 字典里非数值 ⇒ 明确报错（不静默）")
    try:
        _coerce_param({"NEAR": "abc"})
        print("  ✗ 未报错")
        fails.append("dict non-numeric should raise")
    except SystemExit as e:
        print(f"  ✓ 按预期报错：{str(e)[:60]}")

    print("\n[4] _current_of：字典原样 / 缺失与非法回退 default")
    check("_current_of({'x':{'a':2}},'x')", _current_of({"x": {"a": 2}}, "x"), {"a": 2})
    check("_current_of({},'y',0.0)", _current_of({}, "y", 0.0), 0.0)
    check("_current_of({'y':'abc'},'y',3.0)", _current_of({"y": "abc"}, "y", 3.0), 3.0)
    check("_current_of({'y':'2.5'},'y')", _current_of({"y": "2.5"}, "y"), 2.5)

    print("\n[5] SPECS['h527'] 形状")
    s = SPECS.get("h527")
    if not s:
        print("  ✗ 缺 h527 SPEC")
        fails.append("missing h527 spec")
    else:
        check("field", s["field"], "limits.per_symbol_size_mult")
        check("to", s["to"], {"BNB": 0.5, "NEAR": 0.35, "ARB": 0.35, "ENA": 0.35})
        check("rollback_to", s["rollback_to"], {})
        check("meta_key", s["meta_key"], "h527_trial")
        check("task", s["task"], "DSH_HFT_H527_JUDGE")
        # 走一遍框架真实的展开代码路径（do_deploy 里那句）
        fields = [(f.split(".")[-1], _coerce_param(t), _coerce_param(rb))
                  for f, t, rb in (s.get("fields") or
                                   [(s["field"], s["to"], s["rollback_to"])])]
        check("展开后的 fields", fields,
              [("per_symbol_size_mult",
                {"BNB": 0.5, "NEAR": 0.35, "ARB": 0.35, "ENA": 0.35}, {})])

    print("\n[6] h527 判定路径 dry-run（不落库）")
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "h425_repair_trial.py"),
                        "--trial", "h527", "--judge", "--dry-run"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    out = (p.stdout or "") + (p.stderr or "")
    head = out.strip().splitlines()[:3]
    for line in head:
        print(f"    {line}")
    if "Traceback" in out or p.returncode not in (0, 1):
        print("  ✗ 判定路径异常")
        fails.append("h527 judge dry-run failed")
    else:
        print("  ✓ 判定路径可执行（未落库）")

    print("\n[7] h527 机制口径在真实库上可跑（watch/control）")
    try:
        import datetime as _dt
        import psycopg
        from scripts.h425_repair_trial import LANE, _sub_stats, read_env_dsn
        now = _dt.datetime.now(_dt.timezone.utc)
        since = now - _dt.timedelta(hours=3)
        with psycopg.connect(read_env_dsn(), autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json->'symbols' FROM lane_registry "
                            "WHERE lane_id=%s", (LANE,))
                row = cur.fetchone()
                syms = [str(x) for x in (row[0] or [])] if row else []
                o = _sub_stats(cur, "h527", since.isoformat(), now.isoformat(), syms)
        watch = o.get("watch") or []
        print(f"    watch={watch}（应含 BNB）、腿数合计={o.get('watch_legs')}、"
              f"止损USD={o.get('watch_stop_usd')}、P50中位={o.get('watch_notional_p50_med')}")
        ctrl = o.get("control") or {}
        print(f"    control={list(ctrl)}（应为 XRP，且本次未改动）")
        if "BNB" not in watch:
            fails.append("h527 watch 未包含 BNB（R50 修订丢失）")
            print("    ✗ watch 缺 BNB")
        elif not ctrl:
            fails.append("h527 control 缺失")
            print("    ✗ control 缺失")
        else:
            print("    ✓ watch/control 结构正确")
    except Exception as exc:  # noqa: BLE001
        fails.append(f"h527 机制口径取数失败: {type(exc).__name__}")
        print(f"    ✗ 取数失败：{type(exc).__name__}: {str(exc)[:70]}")

    print("\n" + "=" * 70)
    if fails:
        print(f"✗ {len(fails)} 项未通过：{fails}")
        return 1
    print("✓ 全部通过：字典参数可经由框架部署，且既有数值 SPEC 行为未变。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
