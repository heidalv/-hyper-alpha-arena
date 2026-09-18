# -*- coding: utf-8 -*-
"""[轮104] BTC #4712 幽灵止盈事故 —— 账本回滚脚本（默认 dry-run）。

## 事故

    2026-09-19 01:50:11.307  滚仓 buy 0.00165536 @80968.54 成交（订单 23846）
                             + 同 tick 写入 tp=65689.43 / sl=80788.19
    2026-09-19 01:50:15.684  秒级快路径：现价 80808.3 ≥ tp 65689.43 → 命中
    2026-09-19 01:50:15.748  订单 23848 sell market 成交 @65689.43（幽灵价）
    2026-09-19 01:50:15.865  持仓 #4712 status=closed pnl=-47.064（订单口径）

TP 65689.43 是 `_calc_tp_sl` 用**空头分支**算出来的（多头仓传了 side="long"，
旧代码只认 side=="buy"），落在开仓价**下方** 16.31%；成交价从未在市场上出现过。

## 处置（用户已确认：回滚）

    · 订单 23848 → status='cancelled'，pnl/fee/filled_price/filled_at 清空
      （账本口径：这笔幽灵平仓**没有发生过**）；
    · 持仓 #4712 → status='open'，清空 close_*，恢复事故前的结构止损，
      **TP 置空**（长线车道 tp_pct=null，出场=规则失效/Chandelier）；
    · 消痕写入 exit_state_json.manual_repair（可审计）；
    · `_recalc_balance` 按公式重算权益（余额是订单账本 + 持仓的派生量，不手工加减）。

## 用法

    python scripts/_fix104_btc4712_rollback.py            # dry-run（默认）
    python scripts/_fix104_btc4712_rollback.py --apply    # 落库
"""
import argparse
import json
import sys
from datetime import datetime

sys.path.insert(0, '.')

from sqlalchemy import text

from backend.database.connection import SessionLocal

POSITION_ID = 4712
PHANTOM_ORDER_ID = 23848
ACCOUNT_ID = 14
REPAIR_TAG = "rotation104_phantom_tp_rollback"

# 事故前的止损（`exit_state_json.trailing_suppressed.prev_sl`，轮96 恢复的结构位）
EXPECTED_PRE_SL = 78376.122701
EXPECTED_CLOSE_REASON = "tp"
EXPECTED_PHANTOM_PX = 65689.42793
EXPECTED_ENTRY = 78492.80372019952
EXPECTED_SIZE = 0.0036758964629474136


def _die(msg: str) -> None:
    print(f"[FATAL] {msg}")
    sys.exit(2)


def annotate_exit_events(db, apply: bool) -> int:
    """给事故留下的两条 exit 事件打上「已被回滚」标记。

    为什么必须做：`26299 hard_line_close` 写的是 `stop_kind=profit_lock`
    （把幽灵 TP 当成"锁利离场"），`26298 final_trade_outcome` 写的是 `final_pnl=-47.06`。
    仓位既然已恢复为 open，这两行就会与持仓现状矛盾 —— 后续任何按
    `position_exit_events` 统计离场/离场来源的分析都会把这起幽灵平仓算成真实交易。
    处理：**保留行**（审计线索不能删）并在 metadata 里标注回滚。
    """
    n = 0
    for eid in (26298, 26299):
        row = db.execute(text(
            "SELECT metadata_json FROM position_exit_events WHERE id=:i"), {"i": eid}).fetchone()
        if not row:
            print(f"  [warn] exit event {eid} 不存在，跳过")
            continue
        md = {}
        try:
            md = json.loads(row[0]) if row[0] else {}
        except Exception:       # noqa: BLE001
            md = {"_raw": str(row[0])[:2000]}
        if REPAIR_TAG in md:
            print(f"  exit event {eid} 已标注，跳过")
            continue
        md[REPAIR_TAG] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "note": "该离场为幽灵止盈所致，账本已回滚；持仓 #4712 已恢复为 open",
            "phantom_order": PHANTOM_ORDER_ID,
        }
        print(f"  exit event {eid} → 追加 metadata.{REPAIR_TAG}")
        if apply:
            db.execute(text("UPDATE position_exit_events SET metadata_json=:md WHERE id=:i"),
                       {"md": json.dumps(md, ensure_ascii=False), "i": eid})
        n += 1
    if apply and n:
        db.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正落库（默认只打印计划）")
    ap.add_argument("--annotate-exit-events", action="store_true",
                    help="只给事故的 exit 事件打回滚标记（仓位已恢复后单独补做）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        db.execute(text("select set_config('app.is_admin','on',false)"))

        if args.annotate_exit_events:
            print("=== 给事故 exit 事件标注回滚 ===")
            annotate_exit_events(db, args.apply)
            if not args.apply:
                print("\n[dry-run] 未落库。加 --apply 执行。")
            else:
                print("\n[OK] 标注完成")
            return 0

        pos = db.execute(text("""
            SELECT status, side, entry_price, size, margin, leverage, sl_price, tp_price,
                   mark_price, close_price, close_reason, closed_at, exit_state_json
            FROM paper_positions WHERE id=:pid"""), {"pid": POSITION_ID}).fetchone()
        if not pos:
            _die(f"找不到持仓 #{POSITION_ID}")
        (st, side, entry, size, margin, lev, sl, tp, mark, cpx, creason, cat, es_raw) = pos

        order = db.execute(text("""
            SELECT status, side, pnl, fee, filled_price FROM paper_orders WHERE id=:oid"""),
            {"oid": PHANTOM_ORDER_ID}).fetchone()
        if not order:
            _die(f"找不到订单 #{PHANTOM_ORDER_ID}")

        bal = db.execute(text("""
            SELECT initial_balance, total_equity, available_balance, frozen_margin,
                   realized_pnl, total_fee_paid
            FROM paper_balances WHERE account_id=:aid"""), {"aid": ACCOUNT_ID}).fetchone()

        print("=== 修复前 ===")
        print(f"  持仓 #{POSITION_ID}: status={st} side={side} entry={entry} size={size} "
              f"margin={margin} lev={lev}")
        print(f"    sl={sl}  tp={tp}  close_price={cpx}  close_reason={creason}  closed_at={cat}")
        print(f"  订单 #{PHANTOM_ORDER_ID}: status={order[0]} side={order[1]} "
              f"pnl={order[2]} fee={order[3]} filled_price={order[4]}")
        print(f"  账户 {ACCOUNT_ID}: equity={bal[1]} avail={bal[2]} frozen={bal[3]} "
              f"realized={bal[4]} fee={bal[5]}")

        # ── 失败即停：数据与预期不符时绝不改动（本脚本只能修"这一起"事故）──
        if str(st) != "closed":
            _die(f"持仓状态不是 closed（={st}）—— 事故状态已变化，请人工确认")
        if str(creason) != EXPECTED_CLOSE_REASON:
            _die(f"close_reason 不是 {EXPECTED_CLOSE_REASON}（={creason}）")
        if abs(float(cpx or 0) - EXPECTED_PHANTOM_PX) > 0.01:
            _die(f"close_price {cpx} 与幽灵价 {EXPECTED_PHANTOM_PX} 不符")
        if str(order[0]) != "filled" or float(order[2] or 0) >= 0:
            _die(f"订单 #{PHANTOM_ORDER_ID} 状态/盈亏不符（status={order[0]} pnl={order[2]}）")
        if abs(float(entry) - EXPECTED_ENTRY) > 0.01:
            _die(f"entry {entry} 与事故值 {EXPECTED_ENTRY} 不符")
        if abs(float(size) - EXPECTED_SIZE) > 1e-9:
            _die(f"size {size} 与事故值 {EXPECTED_SIZE} 不符")
        if abs(float(sl or 0) - 80788.191962) > 0.01:
            _die(f"sl {sl} 与事故写入值不符（期望 80788.191962）")

        es = json.loads(es_raw) if isinstance(es_raw, str) else (es_raw or {})
        pre_sl = float((es.get("trailing_suppressed") or {}).get("prev_sl") or EXPECTED_PRE_SL)
        if abs(pre_sl - EXPECTED_PRE_SL) > 0.01:
            _die(f"事故前止损 {pre_sl} 与预期 {EXPECTED_PRE_SL} 不符")

        # 当前市价（用于重算浮盈；取不到就退回事故时的 mark）
        try:
            from backend.services.paper_trading_engine import paper_engine
            _ex = paper_engine._resolve_account_exchange(db, ACCOUNT_ID)
            cur_px = float(paper_engine._get_mark_price("BTC", _ex) or 0)
        except Exception as e:      # noqa: BLE001
            print(f"  [warn] 取 BTC 现价失败({e})，退回事故 mark {mark}")
            cur_px = float(mark or 0)
        if cur_px <= 0:
            cur_px = float(mark or 0)

        # 恢复后的止损必须仍在多头正确一侧（< 现价），否则不恢复持仓
        if pre_sl >= cur_px:
            _die(f"事故前止损 {pre_sl} 已在现价 {cur_px} 之上 —— 恢复后会立刻被扫，"
                 f"请人工决定新的结构位")
        upnl = (cur_px - float(entry)) * float(size)
        print(f"\n=== 计划（BTC 现价 {cur_px}）===")
        print(f"  订单 #{PHANTOM_ORDER_ID}: filled → cancelled，pnl {order[2]} → NULL，"
              f"fee {order[3]} → 0")
        print(f"  持仓 #{POSITION_ID}: closed → open，sl {sl} → {pre_sl}，tp {tp} → NULL，"
              f"close_* 清空，mark {mark} → {cur_px}，upnl → {upnl:+.4f}")
        es.setdefault("manual_repair", {})
        es["manual_repair"][REPAIR_TAG] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "reason": "phantom_tp_from_short_branch_on_long_position",
            "phantom_tp": EXPECTED_PHANTOM_PX,
            "phantom_order": PHANTOM_ORDER_ID,
            "restored_sl": pre_sl,
            "prev_sl": float(sl or 0),
            "prev_close_price": float(cpx or 0),
            "prev_close_reason": str(creason),
            "prev_order_pnl": float(order[2] or 0),
            "mark_at_repair": cur_px,
        }
        print(f"  exit_state_json.manual_repair.{REPAIR_TAG} 写入留痕")

        if not args.apply:
            print("\n[dry-run] 未落库。加 --apply 执行。")
            return 0

        print("\n=== 落库 ===")
        db.execute(text("""
            UPDATE paper_orders
               SET status='cancelled', pnl=NULL, fee=0, filled_price=NULL, filled_at=NULL,
                   close_reason=:tag
             WHERE id=:oid"""), {"tag": REPAIR_TAG, "oid": PHANTOM_ORDER_ID})
        db.execute(text("""
            UPDATE paper_positions
               SET status='open', sl_price=:sl, tp_price=NULL, close_price=NULL,
                   close_reason=NULL, closed_at=NULL, mark_price=:px,
                   unrealized_pnl=:upnl, exit_state_json=:es, updated_at=now()
             WHERE id=:pid"""),
            {"sl": pre_sl, "px": cur_px, "upnl": upnl,
             "es": json.dumps(es, ensure_ascii=False), "pid": POSITION_ID})
        db.flush()

        from backend.database.models import PaperBalance
        _bal = db.query(PaperBalance).filter(PaperBalance.account_id == ACCOUNT_ID).first()
        from backend.services.paper_trading_engine import paper_engine
        paper_engine._recalc_balance(db, _bal)
        db.commit()

        row = db.execute(text("""
            SELECT status, sl_price, tp_price, close_price, close_reason, mark_price,
                   unrealized_pnl, margin FROM paper_positions WHERE id=:pid"""),
            {"pid": POSITION_ID}).fetchone()
        bal2 = db.execute(text("""
            SELECT total_equity, available_balance, frozen_margin, realized_pnl, total_fee_paid
            FROM paper_balances WHERE account_id=:aid"""), {"aid": ACCOUNT_ID}).fetchone()
        print(f"  持仓 #{POSITION_ID}: status={row[0]} sl={row[1]} tp={row[2]} "
              f"close_price={row[3]} close_reason={row[4]} mark={row[5]} upnl={row[6]} margin={row[7]}")
        print(f"  账户 {ACCOUNT_ID}: equity={bal2[0]} avail={bal2[1]} frozen={bal2[2]} "
              f"realized={bal2[3]} fee={bal2[4]}")
        print("\n[OK] 回滚完成")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
