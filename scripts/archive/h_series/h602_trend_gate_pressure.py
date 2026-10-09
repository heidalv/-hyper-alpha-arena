"""h602 — 量化"趋势闸拦了多少"（只读，R164）。

动机（R163b）：走平行情下 `trend_only_flat` 会在几分钟内猛增 ✗，把腿量压到 60/h 地板以下，
而判定的 `_market_activity` **只看活跃度/波动、看不出"走平"** ✗ ⇒ 频率失败会被记成
"frequency_below_mandate"，与"参数把腿掐死"**无法区分** ✗。
本脚本把 worker 日志里的 `skip` 计数器**逐条解析成时间序列**，给出：
  · `trend_only_flat` / `trend_down` 的**每小时增量**（= 趋势闸的拦截速率）✓
  · 同期 `fills` 增量 ⇒ 实际成交速率 ✓
  · 两者的相关性（趋势闸拦得越猛，成交越少 ✓）
⇒ 这是今晚判读频率结果时**区分"市场"与"参数"**的直接证据 ✓。

用法：python scripts/h602_trend_gate_pressure.py [--hours 3]
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "mm_lane_worker.log"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    a = ap.parse_args()
    if not LOG.exists():
        print(f"✗ 找不到 {LOG}")
        return 1
    cut = dt.datetime.now() - dt.timedelta(hours=a.hours)
    rows = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if "skip={" not in line or "ticks=" not in line:
            continue
        m_ts = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        m_f = re.search(r"fills=(\d+)", line)
        m_skip = re.search(r"skip=\{([^}]*)\}", line)
        if not (m_ts and m_f and m_skip):
            continue
        try:
            ts = dt.datetime.strptime(m_ts.group(1), "%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            continue
        if ts < cut:
            continue
        d = {}
        for part in m_skip.group(1).split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                try:
                    d[k.strip().strip("'\"")] = int(v)
                except ValueError:
                    pass
        rows.append((ts, int(m_f.group(1)), d))
    if len(rows) < 2:
        print(f"✗ 近 {a.hours}h 日志行不足（{len(rows)} 行）")
        return 1
    print("=" * 96)
    print(f"趋势闸压力（近 {a.hours}h，共 {len(rows)} 个采样点，每点约 5 分钟）")
    print("=" * 96)
    print(f"  {'时间':<10}{'Δtrend_only_flat':>18}{'Δtrend_down':>14}{'Δfills':>9}"
          f"{'拦/成交比':>12}")
    t0, f0, d0 = rows[0]
    tot_block = tot_fill = 0
    for ts, f, d in rows[1:]:
        dflat = d.get("trend_only_flat", 0) - d0.get("trend_only_flat", 0)
        ddown = d.get("trend_down", 0) - d0.get("trend_down", 0)
        dfill = f - f0
        ratio = (dflat / dfill) if dfill else float("inf")
        tot_block += dflat
        tot_fill += max(dfill, 0)
        print(f"  {ts:%H:%M}     {dflat:>18}{ddown:>14}{dfill:>9}"
              f"{(f'{ratio:.2f}' if dfill else '∞(零成交)'):>12}")
        t0, f0, d0 = ts, f, d
    span_h = (rows[-1][0] - rows[0][0]).total_seconds() / 3600.0
    if span_h > 0:
        print("-" * 96)
        print(f"  区间 {span_h:.2f}h：趋势闸拦 {tot_block} 次（{tot_block/span_h:.0f}/h）、"
              f"成交 {tot_fill} 次（{tot_fill/span_h:.0f}/h）")
        print(f"  ⇒ 拦/成交 = {(tot_block/tot_fill) if tot_fill else float('inf'):.1f} 倍")
    print("=" * 96)
    print("用途：今晚若 ② 因频率判负，用本表说明'短fall 来自趋势闸（市场走平）'还是"
          "'成交本身变少' ✓ —— 这是框架自己算不出来的那一层 ✓。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
