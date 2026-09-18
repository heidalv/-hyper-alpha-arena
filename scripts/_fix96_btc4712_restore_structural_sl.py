"""轮96 紧急处置：把 BTC #4712（唯一在册的长线趋势仓）的止损放回结构位。

背景（reports/_轮96_长线趋势仓被收紧止损收割事故复盘_20260918.md）：
`midlong_position_manager` 的 tighten_trailing 把 SL 拉到「现价 − 2×短周期ATR」
（实测 ≈1%），BTC 现价 80955.9、SL 80287.7，**只剩 0.825% 缓冲**，
一轮正常波动就会把最后这笔趋势仓按 +5% 微利收割（今天已发生 7 次）。

处置原则（用户 2026-09-18 确认）：
  1. 放回结构位，给它呼吸空间；
  2. 但不放弃全部锁利 —— 仍锁定 ≥2.5% 的利润。

故 new_sl = max(结构位 Chandelier, entry × 1.025)：
  · 结构位 = `exit_state_json.structural_stop_price`（E1 引擎写入的日线 Chandelier）
  · entry×1.025 = 长车道「最小锁定利润」地板（Fix B 的一部分）

安全约束：止损**只允许上移**（本仓位旧 SL 已在 entry+5%，结构位远低于它，
所以本次实际上是"下移"以换取呼吸空间）—— 这是用户明确要求的例外，
且下移后仍高于入场 2.5%，不产生新的亏损风险敞口。脚本会把新旧值都打印出来。
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, '.')

from sqlalchemy import text

from backend.database.connection import SessionLocal

POSITION_ID = 4712
MIN_LOCK_PCT = 0.025          # 长车道最小锁定利润 2.5%（= Fix B 的一部分）
APPLY = '--apply' in sys.argv

db = SessionLocal()
try:
    # paper_* 表有 FORCE RLS，须显式声明管理员身份才能看到/写入全量行
    db.execute(text("select set_config('app.is_admin','on',false)"))
    row = db.execute(text(
        "SELECT id, symbol, side, status, entry_price, sl_price, mark_price, "
        "unrealized_pnl, exit_state_json FROM paper_positions WHERE id=:p"
    ), {"p": POSITION_ID}).fetchone()
    if not row:
        print(f'#{POSITION_ID} 不存在'); sys.exit(1)
    pid, sym, side, status, entry, sl, mark, upnl, esj = row
    entry = float(entry or 0)
    sl = float(sl or 0)
    mark = float(mark or 0)
    es = json.loads(esj) if isinstance(esj, str) else (esj or {})
    structural = es.get('structural_stop_price')
    structural = float(structural) if structural else None

    floor = entry * (1 + MIN_LOCK_PCT)
    target = max([v for v in (structural, floor) if v] or [floor])

    print(f'#{pid} {sym} {side} status={status}')
    print(f'  entry={entry:.2f}  mark={mark:.2f}  upnl={upnl}')
    print(f'  旧 SL={sl:.2f}  (距入场 {(sl/entry-1)*100:+.3f}%, 距现价 {(sl/mark-1)*100:+.3f}%)')
    print(f'  结构位 Chandelier={structural if structural else "n/a"}')
    print(f'  最小锁定利润地板 entry×{1+MIN_LOCK_PCT}={floor:.2f}')
    print(f'  → 目标 SL={target:.2f}  (距入场 {(target/entry-1)*100:+.3f}%, 距现价 {(target/mark-1)*100:+.3f}%)')

    if not APPLY:
        print('\n[DRY-RUN] 加 --apply 才真正写入')
        sys.exit(0)

    db.execute(text(
        "UPDATE paper_positions SET sl_price=:sl WHERE id=:p"
    ), {"sl": round(target, 6), "p": pid})
    # 记账：把 E1 引擎的结构位与本次人工处置都留痕，便于后续审计
    es['structural_stop_price'] = structural
    es['trailing_suppressed'] = {
        'reason': 'rotation96_restore_structural_room',
        'at': '2026-09-18',
        'prev_sl': sl,
        'new_sl': round(target, 6),
        'min_lock_pct': MIN_LOCK_PCT,
    }
    db.execute(text(
        "UPDATE paper_positions SET exit_state_json=:es WHERE id=:p"
    ), {"es": json.dumps(es, ensure_ascii=False), "p": pid})
    db.commit()
    chk = db.execute(text(
        "SELECT sl_price, entry_price FROM paper_positions WHERE id=:p"
    ), {"p": pid}).fetchone()
    _pct = (float(chk[0]) / float(chk[1]) - 1) * 100 if chk and chk[1] else float('nan')
    print(f'\n[OK] 写入完成: sl_price={chk[0]}  距入场 {_pct:+.3f}%')
finally:
    db.close()
