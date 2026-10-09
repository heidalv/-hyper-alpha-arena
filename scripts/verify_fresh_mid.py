"""验证 F281：新鲜盘口到底有没有生效（`_fresh_mid_hits` 是否在涨）。

不信任"改了就会生效"（F189 教训）。直接构造 runner，跑 `fetch_market`，
看 `_fresh_mid_hits` 与缓存内容。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)
LANE = os.getenv("MM_LANE_ID", "mm_asterdex")


def main():
    from backend.services.market_maker.runner import get_runner

    r = get_runner(LANE)
    if r is None:
        print("get_runner 返回 None")
        return 1
    print("=" * 84)
    print("F281 新鲜盘口验证")
    print("=" * 84)
    print(f"  runner 已构造：lane={LANE}  symbols={len(r.symbols)}")
    print(f"  _fresh_mid_enabled   = {getattr(r, '_fresh_mid_enabled', None)}")
    print(f"  _fresh_mid_refresh_s = {getattr(r, '_fresh_mid_refresh_s', None)}")

    # 跑两次 fetch_market，看缓存与命中计数
    import time
    for i in (1, 2):
        t0 = time.time()
        rows = r.fetch_market(int(time.time() * 1000) - 600_000)
        dt = (time.time() - t0) * 1000
        fb = getattr(r, "_fresh_book", {})
        hits = getattr(r, "_fresh_mid_hits", 0)
        print(f"\n  第 {i} 次 fetch_market：{dt:.0f}ms  "
              f"返回 {len(rows)} 币  新鲜缓存 {len(fb)} 币  hits={hits}")
        if i == 1 and fb:
            print("  缓存内容（新鲜盘口）：")
            for k, v in sorted(fb.items()):
                mid = 0.5 * (v[0] + v[1])
                sp = (v[1] - v[0]) / mid * 1e4 if mid > 0 else 0
                print(f"    {k:<14} bid={v[0]:<16.8g} ask={v[1]:<16.8g} "
                      f"mid={mid:<16.8g} 价差={sp:.4f}bp")

    hits = getattr(r, "_fresh_mid_hits", 0)
    err = getattr(r, "_fresh_mid_err", None)
    print("\n" + "=" * 84)
    if err:
        print(f"  _fresh_mid_err = {err}")
    if hits > 0:
        print(f"  ⇒ **F281 生效**：新鲜盘口命中 {hits} 次 ✓")
    else:
        print("  ⇒ **F281 未生效**（hits=0）—— 见上面的 err / 缓存内容 ✗")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
