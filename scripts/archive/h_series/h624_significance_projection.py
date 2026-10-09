"""h624 — 把"今晚会不会跨过显著性"算出来（只读；R228）。

动机：13:26 的现场读数显示 Welch **p=0.1275、Δ=−1.621bp（n=254/749）**，已在朝 0.10 逼近 ✗。
与其猜"今晚会不会显著"，不如**按当前 Δ 与离散度外推**到判定时的腿数（≈840），
给出**预期 p** 与**三种结局的概率量级** ✓ —— 这直接决定今晚要不要人工收尾（因为我的
13:20 污染会把"负显著"降级为 INCONCLUSIVE ✗，见文档顶部横幅）。

方法（全部从账本重算，不依赖任何人打印过的数 ✓）：
  试跑窗 = [since, now]；基线窗 = [since−24h, since−12h]（判定同口径 ✓）
  ⇒ Welch t = (mean_t − mean_b) / sqrt(var_t/n_t + var_b/n_b)（`net_bp` 逐腿，不做任何截尾 ✗）
  ⇒ 把 n_t 放大到判定时的预计腿数，其它不变 ⇒ 投影 t 与 p ✓
  ⚠️ 这是**一阶外推**（假设 Δ 与离散度不变）⇒ 只用于"今晚大概会怎样"，不作判据 ✓。

用法：python scripts/h624_significance_projection.py [--target-legs 840]
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import math
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


def _vals(cur, t0: str, t1: str, syms: list) -> list:
    cur.execute("""
        SELECT COALESCE(net_bp,0)::float8 FROM lane_ledger
        WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz
          AND symbol = ANY(%s)""", (h.LANE, t0, t1, syms))
    return [float(r[0]) for r in cur.fetchall()]


def _welch(a: list, b: list) -> tuple:
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return (float("nan"),) * 4
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    t = (ma - mb) / se if se else float("nan")
    return ma, mb, se, t


def _p_two_sided(t: float, df: int) -> float:
    """t 分布双尾 p（用正态近似 + 小样本修正；只作量级判断 ✓）。"""
    if not (t == t):
        return float("nan")
    z = abs(t)
    # 正态双尾
    p = math.erfc(z / math.sqrt(2))
    # 小样本：按 df 放大（t 分布尾部更厚）
    if df > 0:
        p *= (1.0 + (z * z + 1.0) / (4.0 * df))
    return min(1.0, p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-legs", type=int, default=840)
    a = ap.parse_args()
    print("=" * 92)
    print("h624 — 今晚会不会跨过显著性？（只读外推）")
    print("=" * 92)
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        t = dict(meta.get("h463_trial") or {})
        since = t.get("started_at")
        now = dt.datetime.now(dt.timezone.utc)
        s0 = dt.datetime.fromisoformat(since)
        b0 = (s0 - dt.timedelta(hours=24)).isoformat()
        b1 = (s0 - dt.timedelta(hours=12)).isoformat()
        tr = _vals(cur, since, now.isoformat(), syms)
        ba = _vals(cur, b0, b1, syms)
        ma, mb, se, tt = _welch(tr, ba)
        df = len(tr) + len(ba) - 2
        p = _p_two_sided(tt, df)
        print(f"  试跑窗 n={len(tr)}  均值={ma:+.3f}bp")
        print(f"  基线窗 n={len(ba)}  均值={mb:+.3f}bp")
        print(f"  ⇒ Δ={ma - mb:+.3f}bp  se={se:.3f}bp  t={tt:+.2f}  p≈{p:.4f}"
              f"（α=0.10 ⇒ {'**当前已显著** ✗' if p <= 0.10 else '当前不显著'}）")
        # 外推：Δ 与 se 的"每腿信息量"不变 ⇒ se ∝ sqrt(1/n_t + 1/n_b)
        k = math.sqrt((1.0 / a.target_legs + 1.0 / len(ba)) / (1.0 / len(tr) + 1.0 / len(ba)))
        t2 = tt / k
        p2 = _p_two_sided(t2, a.target_legs + len(ba) - 2)
        print(f"\n  外推到判定时（试跑 n→{a.target_legs}，Δ 与离散度不变）：")
        print(f"    t→{t2:+.2f}  **p≈{p2:.4f}**"
              f" ⇒ {'**很可能会显著（且 Δ<0 ⇒ 本应如实 ROLLBACK ✗）**' if p2 <= 0.10 else '大概率仍不显著 ⇒ INCONCLUSIVE'}")
    print("\n" + "-" * 92)
    print("  判读：① 若外推 p ≤ 0.10 且 Δ<0 ⇒ 今晚**本该**给出 ROLLBACK；")
    print("        ② 但我的 13:20 污染会让它降级为 INCONCLUSIVE ✗ ⇒ 别把它读成「90s 没问题」✗；")
    print("        ③ 一阶外推，仅供决策参考（不作判据 ✓）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
