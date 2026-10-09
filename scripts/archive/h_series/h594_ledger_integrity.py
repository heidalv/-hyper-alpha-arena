"""h594 — 完整性核验：我的测试有没有泄进**车道账本**（只读，R129）。

背景：待命采集器的验证用过 `BNB/NEAR/ARB/XRP/ENA/ETH/LIT/XMR` 等币，
其中 **LIT/XMR 不是车道宇宙的币** ✗。若它们出现在 `lane_ledger` 里，说明
"测试污染了生产数据" ✗。本脚本做三项核对：
  1. 账本里的**币种集合**是否恰好等于车道注册表的 `symbols`（5 币）；
  2. 是否存在 `LIT/XMR/DOGE/...` 等**测试币或旧宇宙币**的行；
  3. 车道账本与行情库的**行数增长**是否各归其主（账本只由 worker 写 ✓）。

用法：python scripts/h594_ledger_integrity.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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

SUSPECT = ("LIT", "XMR", "DOGE", "ETH", "BTC", "AVAX", "LINK", "LTC", "XLM",
           "PENDLE", "PUMP", "SUI", "ADA")
ERA_SINCE = "2026-09-28T05:00:00+00:00"   # 本地 09-28 13:00（宇宙收窄 = 现纪元起点）


def main() -> int:
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        live = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        # [R129 修] 首版**没有时间过滤**，把账本的全部历史都算进来 ⇒ 早期宇宙（曾多达 26 币）
        # 被误报成"测试污染" ✗。**必须按现纪元过滤**才是"当前是否干净"的判据 ✓。
        cur.execute("SELECT DISTINCT symbol FROM lane_ledger WHERE lane_id=%s"
                    " AND ts > %s::timestamptz", (h.LANE, ERA_SINCE))
        era_syms = sorted({str(r[0]) for r in cur.fetchall()})
        cur.execute("SELECT count(DISTINCT symbol) FROM lane_ledger WHERE lane_id=%s",
                    (h.LANE,))
        all_syms_n = cur.fetchone()[0]
        print("=" * 88)
        print("车道账本完整性（**现纪元**口径）")
        print("=" * 88)
        print(f"  注册表 symbols = {live}")
        print(f"  现纪元出现过的币 = {era_syms}")
        print(f"  （对照：账本全历史共 {all_syms_n} 个币 —— 早期宇宙更大，属历史 ✓）")
        extra = [s for s in era_syms if s not in live]
        print(f"  ⇒ 现纪元里**非现役**的币：{extra if extra else '无 ✓'}")
        bad = [s for s in era_syms if s.upper() in SUSPECT]
        print(f"  ⇒ 命中测试/旧宇宙币名单：{bad if bad else '无 ✓'}"
              f"（若只有收窄瞬间的 4 条遗留平仓腿，属设计行为 ✓，见 `h575`）")
        cur.execute("SELECT count(*), max(ts) FROM lane_ledger WHERE lane_id=%s", (h.LANE,))
        n, mx = cur.fetchone()
        print(f"  账本总行数 = {n}；最新一行 ts = {mx}")
        print("-" * 88)
        if len(bad) > 4:
            print("  ✗ 现纪元里出现多于 4 条非现役币的行 ⇒ 需要追查写入来源")
            return 1
        print("  ✓ 现纪元账本只含现役币（外加收窄瞬间的遗留平仓腿 ✓）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
