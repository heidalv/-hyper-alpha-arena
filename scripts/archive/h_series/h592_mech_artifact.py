"""h592 — 机制指标（`legs_per_trip`）的**未平往返伪影**量化（只读，R120）。

问题：试跑窗还没结束时，**正在进行中的往返**只累加"加仓腿"、却还没有"出场腿" ✗
⇒ `legs_per_trip = 腿数 ÷ 出场腿数` 被结构性抬高 ✗（R66 已定性指出）。
本脚本用**截尾**把它量化：对 [since, now−X]（X = 0/10/20/30 分钟）各算一次，
截掉窗尾即截掉"还在进行中的那几趟" ⇒ 数值会**下降并趋于真实** ✓。

同时给出**参照窗**的同口径值（参照窗是已结束的历史窗 ⇒ 无此伪影 ✓）。
若截尾后试跑窗仍明显高于同体制参照 ⇒ 说明信号**不是**伪影 ✓。

用法：python scripts/h592_mech_artifact.py [--trial h463]
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

REF = 3.6263          # 同体制参照（0.5 体制 + 45s，h572 的 N3 窗）
TZ = dt.timezone(dt.timedelta(hours=8))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="h463")
    a = ap.parse_args()
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        since = dict(meta.get(f"{a.trial}_trial") or {}).get("started_at")
        if not since:
            print("✗ 缺 started_at")
            return 1
        now = dt.datetime.now(dt.timezone.utc)
        print("=" * 92)
        print(f"{a.trial}：机制指标随截尾的变化（截掉窗尾 X 分钟 = 截掉进行中的往返）")
        print("=" * 92)
        print(f"  {'截尾':<8}{'腿':>6}{'出场腿':>8}{'加仓腿':>8}{'腿/趟':>9}{'÷ 同体制参照':>14}")
        base = None
        for x in (0, 10, 20, 30):
            until = (now - dt.timedelta(minutes=x)).isoformat()
            sub = h._sub_stats(cur, a.trial, since, until, syms)
            hw = dict(sub.get("hold_window") or {})
            lpt = hw.get("legs_per_trip")
            ratio = (lpt / REF) if lpt else None
            if x == 0:
                base = lpt
            print(f"  {('now' if x == 0 else f'-{x}min'):<8}{hw.get('legs', 0):>6}"
                  f"{hw.get('exit_legs', 0):>8}{hw.get('add_legs', 0):>8}"
                  f"{(f'{lpt:.2f}' if lpt else '-'):>9}"
                  f"{(f'{ratio:.2f}×' if ratio else '-'):>14}")
        print("-" * 92)
        print(f"  同体制参照（已结束窗，无伪影）= {REF}")
        print("⇒ 判读：若截尾后仍 >1.3× 参照 ⇒ 信号不是伪影（90s 加仓窗确在起作用 ✓）；"
              "若截尾后塌到 ≈1× ⇒ 之前的高值是未平往返造成的 ✗。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
