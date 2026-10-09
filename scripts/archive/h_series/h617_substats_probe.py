"""h617 — 验 `h529` 的判据分支**真的能算**（只读；R209）。

`h548_criteria_audit.py` 是**静态**审计（"SPEC 有没有对应分支"）。本脚本做**动态**验证：
用与判定完全相同的方式调用 `_sub_stats(cur, key, since, until, syms)`，
对 `h463`（②，已知可用）与 `h529`（③′，新加分支）各跑一次，确认：
  1. 两者都返回 `hold_window`（腿数/平仓腿/加仓腿/每趟腿数/入场净额）；
  2. `h529` 的 `note` 是**它自己的口径说明**（不是从 h463 复制过来的 ✗）；
  3. 顺带打印 ② 窗口的实测值（判定前的参考读数 ✓）。

用法：python scripts/h617_substats_probe.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402


def main() -> int:
    print("=" * 90)
    print("h617 — h463 / h529 的 `_sub_stats` 动态验证（只读）")
    print("=" * 90)
    fails = []
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        t = dict(meta.get("h463_trial") or {})
        since = t.get("started_at")
        import datetime as dt
        until = dt.datetime.now(dt.timezone.utc).isoformat()
        print(f"  窗口：② 试跑窗 {since} → {until}")
        print(f"  币种：{syms}\n")
        for key in ("h463", "h529"):
            try:
                sub = h._sub_stats(cur, key, since, until, syms)
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ [{key}] `_sub_stats` 抛异常：{type(e).__name__}: {e}")
                fails.append(f"{key} 抛异常")
                continue
            hw = dict(sub.get("hold_window") or {})
            if not hw:
                print(f"  ✗ [{key}] 没有 hold_window（判据会静默不报 ✗）")
                fails.append(f"{key} 缺 hold_window")
                continue
            print(f"  ✓ [{key}] hold_window：腿={hw.get('legs')} "
                  f"平仓腿={hw.get('exit_legs')} 加仓腿={hw.get('add_legs')} "
                  f"腿/趟={hw.get('legs_per_trip')} 入场净={hw.get('entry_net_bp')}bp")
            note = str(hw.get("note") or "")
            print(f"      note：{note[:110]}")
            if key == "h529":
                if "h529" in note or "要求式" in note:
                    print("      ✓ note 是 h529 自己的口径说明 ✓")
                else:
                    print("      ✗ note 像是从 h463 抄来的（口径会误导 ✗）")
                    fails.append("h529 note 未区分")
    print("\n" + "-" * 90)
    if fails:
        print(f"✗ 失败 {len(fails)} 项：{fails}")
        return 1
    print("✓ 通过：h463 与 h529 的判据分支都能算，且 h529 有自己的口径说明 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
