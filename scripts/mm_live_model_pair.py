# -*- coding: utf-8 -*-
"""[F298 2026-09-16] 实盘 ↔ 模型同窗口对拍（一条命令，可重复跑）。

F196 纪律：先证明"实盘行为 = 模型行为"，再谈盈亏。此前这套对拍要手工拼三条命令
（查账本 / 跑 mm_clean_sweep / 对字段），容易漏项也难复核 ⇒ 收成一个脚本：
  · 实盘侧：`lane_ledger` 同窗口成交数/名义/已实现（排除 reconcile 行）+ `/shadow` 计数；
  · 模型侧：调用 `mm_clean_sweep.py --since --until --no-robust`（在位配置）；
  · 并排打印，并给出**行为一致性**判据：挂宽/σ/skip 分布可比、成交数同量级。

用法：python scripts/mm_live_model_pair.py --since 2026-09-16T13:55:00+08:00 [--until now]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CST = timezone(timedelta(hours=8))


def live_side(since_iso: str):
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    t0 = datetime.fromisoformat(since_iso)
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT COUNT(*) n, COALESCE(SUM(notional),0) notional,"
                " COALESCE(SUM(points_usd),0) pts FROM lane_ledger"
                " WHERE lane_id='mm_asterdex' AND ts >= :t"
                "   AND COALESCE((meta_json->>'source'),'') <> 'reconcile'"),
                {"t": t0}).mappings().first()
    sh = {}
    try:
        sh = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:8000/api/trading/lanes/mm_asterdex/shadow", timeout=20).read())
    except Exception as e:
        sh = {"error": str(e)}
    return {"ledger": dict(rows or {}), "shadow": sh}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="")
    a = ap.parse_args()
    until = a.until or datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    lv = live_side(a.since)
    sh = lv["shadow"] or {}
    print(f"窗口 {a.since} → {until}")
    print(f"[实盘] 账本: n={lv['ledger'].get('n')} 名义={float(lv['ledger'].get('notional') or 0):.2f}$ "
          f"已实现={float(lv['ledger'].get('pts') or 0):+.4f}$")
    print(f"[实盘] /shadow: ticker={sh.get('ticker')} symbols={sh.get('symbols')} "
          f"ticks={sh.get('ticks')} fills={sh.get('fills')} base={sh.get('avg_base_bp')} "
          f"width={json.dumps(sh.get('avg_width_bp'), ensure_ascii=False)} "
          f"σ_all={sh.get('avg_sigma_all')} skip={json.dumps(sh.get('skip_counts'), ensure_ascii=False)}")
    out = ROOT / "logs" / "f298_model.json"
    cmd = [sys.executable, str(ROOT / "scripts" / "mm_clean_sweep.py"),
           "--since", a.since, "--until", until, "--no-robust", "--json-out", str(out)]
    print("[模型] 跑在位配置:", " ".join(cmd[-6:]))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace")
        for ln in (r.stdout or "").strip().splitlines()[-6:]:
            print("   " + ln)
        if r.returncode != 0:
            print("   模型返回码:", r.returncode, (r.stderr or "")[-300:])
    except Exception as e:
        print("   模型运行失败:", e)
    print("\n判据（F196）：① 挂宽/σ/skip 可逐项对照；② 成交数同量级（差额需能归因）；"
          "③ 方向一致。任一不成立 ⇒ 先查链路（数据龄/心跳/宇宙），不要解释成策略问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
