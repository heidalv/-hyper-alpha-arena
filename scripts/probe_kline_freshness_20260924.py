# -*- coding: utf-8 -*-
"""K 线块**新鲜度**体检 + 因子块**真实入参**追踪。

已确认（上一探针）：`build_kline_block` / `deep_kline_text` 都**有内容**（真 OHLCV + 指标）。
本轮回答两个更要命的问题：
  A) 喂给模型的那一段 K 线，**最后一行是不是最新 bar**？各周期分别差多少小时？
     （提示词里塞了过期数据是"分析准确率低"的典型原因之一）
  B) 因子块 `_build_factor_signals_prompt_block(market_envs)` 在**真实调用点**拿到的
     market_envs 到底有没有因子字段？若常年为空 ⇒ 等于提示词里没有因子。
只读。
"""
from __future__ import annotations

import io
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env", override=False)

import psycopg  # noqa: E402

MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
BAR_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})\s+O=", re.M)
SECS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
import datetime as dt  # noqa: E402

UTC = dt.timezone.utc


def main() -> int:
    from backend.services.agent_deep_context import build_kline_block

    now = time.time()
    print("=" * 100)
    print("【A】K 线块新鲜度（块内最后一行 vs 现在；UTC 口径）")
    with psycopg.connect(MARKET, autocommit=True) as c:
        cur = c.cursor()
        for sym in ("BTC", "ETH", "SOL"):
            blk = build_kline_block(sym, ["1h", "4h", "1d", "1w"], count=30) or ""
            # 按 "### <period> K线" 切段
            parts = re.split(r"###\s+(\S+)\s+K线", blk)
            print("\n  %s（块长 %d 字符）" % (sym, len(blk)))
            print("    %-5s %-18s %-18s %10s %10s" % ("周期", "块内首行", "块内末行", "末行滞后h", "DB最新bar"))
            for i in range(1, len(parts) - 1, 2):
                per = parts[i]
                seg = parts[i + 1]
                hits = BAR_RE.findall(seg)
                if not hits:
                    print("    %-5s **段内没有解析到 bar 行** (len=%d)" % (per, len(seg)))
                    continue
                def ts(h):
                    return dt.datetime(int(h[0][:4]), int(h[0][5:7]), int(h[0][8:10]),
                                       int(h[1]), int(h[2]), tzinfo=UTC).timestamp()
                t_first, t_last = ts(hits[0]), ts(hits[-1])
                cur.execute(
                    """select max(timestamp) from crypto_klines where symbol=%s and exchange='binance'
                       and period=%s and environment='mainnet'""", (sym, per))
                db_max = cur.fetchone()[0]
                db_max = int(db_max) if db_max else 0
                lag_h = (now - t_last) / 3600.0
                db_lag = (now - db_max) / 3600.0 if db_max else float("nan")
                print("    %-5s %-18s %-18s %9.2fh %8.2fh前"
                      % (per,
                         dt.datetime.fromtimestamp(t_first, UTC).strftime("%m-%d %H:%M"),
                         dt.datetime.fromtimestamp(t_last, UTC).strftime("%m-%d %H:%M"),
                         lag_h, db_lag))
                if lag_h > (SECS.get(per, 3600) / 3600.0) * 3:
                    print("         ⚠ 末行明显滞后（>3 个周期）—— 喂给模型的是**过期 K 线**")

    print("\n" + "=" * 100)
    print("【B】因子块的真实入参：market_envs 里有没有因子字段")
    import inspect  # noqa: E402
    from backend.services import trading_analysts as TA

    src = inspect.getsource(TA.MasterController._build_factor_signals_prompt_block)
    print("  ── 函数源码（前 40 行）──")
    for ln in src.splitlines()[:40]:
        print("  | " + ln[:170])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
