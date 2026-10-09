# -*- coding: utf-8 -*-
"""[h691 2026-10-01] 方向封锁的**反事实评估**:被封锁的那一侧,如果挂了会赚还是亏?

为什么需要它:方向卡(`judge_direction`,markout 差于 −2bp 就封该侧)用的是
**我们自己成交的 markout** ⇒ 天然带选择偏差:它只看得到"成交了的腿",看不到
"被它封掉的机会"。要判定"封对了没有",必须记录封锁时刻的反事实:
  · 记录侧:`runner` 在封锁时写 `data/dir_block_shadow.jsonl`(ts/symbol/side/mid);
  · 本脚本:对每条记录,取 t+30/60/120s 的中价(盘口表),算**假设挂单**的 markout:
      被封 buy  ⇒ 我们没买;若买了,收益 = (mid_{t+h} − mid_t)/mid_t(做多方向)
      被封 sell ⇒ 若卖了,收益 = −(mid_{t+h} − mid_t)/mid_t(做空方向)
    再看"如果没封"的平均收益:负 ⇒ 封对了;正 ⇒ 封错了(封掉了赚钱机会)。
输出:分来源(card / fusion)、分侧、分币的统计。
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SHADOW = ROOT / "data" / "dir_block_shadow.jsonl"
HORIZONS = (30, 60, 120)


def main() -> int:
    from backend.services.market_maker.attribution import _market_dsn
    import psycopg

    if not SHADOW.exists():
        print("尚无反事实记录(文件不存在)。封锁发生后会自动累积。")
        return 0
    recs = []
    for line in SHADOW.read_text(encoding="utf-8").splitlines():
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    if not recs:
        print("尚无反事实记录。")
        return 0
    now_ms = int(time.time() * 1000)
    agg: dict = defaultdict(lambda: defaultdict(list))
    done = 0
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        for r in recs:
            t_ms = int(float(r["ts"]) * 1000)
            if t_ms + max(HORIZONS) * 1000 > now_ms:
                continue        # 还没到观察窗
            sym = str(r["symbol"]).upper() + "USDT"
            side = str(r.get("blocked_side") or "")
            sign = 1.0 if side == "buy" else -1.0
            for h in HORIZONS:
                cur.execute(
                    "SELECT (bid_px+ask_px)/2.0 FROM asterdex_book_ticker"
                    " WHERE symbol=%s AND event_ts_ms >= %s AND bid_px>0 AND ask_px>bid_px"
                    " ORDER BY event_ts_ms LIMIT 1", (sym, t_ms + h * 1000))
                row = cur.fetchone()
                if not row or not row[0]:
                    continue
                mid_later = float(row[0])
                mid0 = float(r.get("mid") or 0.0)
                if mid0 <= 0:
                    continue
                ret_bp = (mid_later - mid0) / mid0 * 1e4 * sign
                agg[(str(r.get("src") or "card"), side)][h].append(ret_bp)
                done += 1
    print(f"记录 {len(recs)} 条,可评估 {done} 个 (记录×期限) 观测\n")
    print(f"{'来源':<8}{'被封侧':<6}{'期限':>6}{'样本':>7}{'若不封平均收益':>16}{'t':>8}")
    for (src, side), byh in sorted(agg.items()):
        for h in HORIZONS:
            vals = byh.get(h) or []
            if len(vals) < 10:
                continue
            m = sum(vals) / len(vals)
            var = sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1)
            se = math.sqrt(var / len(vals)) if var > 0 else 0.0
            t = m / se if se > 0 else 0.0
            verdict = "封对了(机会是亏的)" if m < 0 else "⚠封错了(机会是赚的)"
            print(f"{src:<8}{side:<6}{h:>6}{len(vals):>7}{m:>+16.3f}{t:>+8.2f}   {verdict}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
