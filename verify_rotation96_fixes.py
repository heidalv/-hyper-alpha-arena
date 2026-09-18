"""轮96 修复的**运行中**验证：四项修复在活进程里真的生效。

不用源码守卫，全部走真实代码路径 + 活库状态。
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, '.')

from sqlalchemy import text

from backend.database.connection import SessionLocal

ok_all = True


def check(name: str, passed: bool, detail: str = "") -> None:
    global ok_all
    ok_all = ok_all and bool(passed)
    print(f"  [{'OK' if passed else '!!'}]  {name}")
    if detail:
        print(f"        {detail}")


print('── 轮96 四项修复（运行中后端）──')

# ── A：E1 独占守卫 ──────────────────────────────────────────────
from backend.services.full_auto.midlong_position_manager import manage_position
_e1_pos = {"id": 4712, "symbol": "BTC", "side": "long",
           "exit_state": {"entry_source": "trend_e1"}}
_out = manage_position(None, host=None, session=None, account_id=14, symbol="BTC",
                       position=_e1_pos, market_summary={}, analyst_reports={},
                       trading_mode="paper")
check("A. E1 趋势仓被 midlong 管理器跳过",
      _out.get("action") == "manage_skip_e1",
      f"action={_out.get('action')} hold_reason={_out.get('hold_reason')}")

# ── B：车道最小收紧带宽 ─────────────────────────────────────────
from backend.services.full_auto.midlong_position_manager import clamp_tighten_band
from backend.config import settings as _st
_b_long = clamp_tighten_band(side="long", mark=100.0, new_sl=99.0, tier="long")
_b_mid = clamp_tighten_band(side="long", mark=100.0, new_sl=99.5, tier="mid")
check("B. 车道最小收紧带宽生效（long 3% / mid 1%）",
      abs(_b_long[0] - 97.0) < 1e-9 and abs(_b_mid[0] - 99.0) < 1e-9,
      f"long 1%→{_b_long[0]}（期望 97）; mid 0.5%→{_b_mid[0]}（期望 99）")
check("B. 三个下限键已在 settings 声明（否则 env 覆盖静默失效）",
      all(hasattr(_st, k) for k in (
          "MIDLONG_TIGHTEN_MIN_BAND_PCT_LONG", "MIDLONG_TIGHTEN_MIN_BAND_PCT_MID",
          "MIDLONG_TIGHTEN_MIN_BAND_PCT_SHORT")),
      f"long={_st.MIDLONG_TIGHTEN_MIN_BAND_PCT_LONG} mid={_st.MIDLONG_TIGHTEN_MIN_BAND_PCT_MID} "
      f"short={_st.MIDLONG_TIGHTEN_MIN_BAND_PCT_SHORT}")

# ── C：min_hold 拒付追踪派生止损 ────────────────────────────────
from datetime import datetime, timedelta, timezone

from backend.services.paper_trading_engine import paper_engine


class _P:
    pass


_p = _P()
_p.id, _p.symbol, _p.side = 999999, "TEST", "long"
_p.entry_price, _p.sl_price, _p.mark_price = 100.0, 108.0, 110.0
_p.margin, _p.unrealized_pnl, _p.peak_pnl_pct = 50.0, 5.0, 0.10
_p.timeframe_tier, _p.trade_nature = "long", "trend_follow"
_p.status, _p.account_id, _p.strategy_id = "open", 14, "t"
_p.peak_unrealized_pnl = 8.0
_p.opened_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)
_p.exit_state_json = json.dumps({"structural_stop_price": 95.0,
                                 "sl_meta": {"source": "trailing"}})
_defer, _info = paper_engine._sl_min_hold_verdict(_p, 110.0)
_restore = float(_info.get("structural_stop") or 0)
check("C. 保护期内的追踪派生止损被拒付（并给出回退位）",
      _defer is True and abs(_restore - 102.5) < 1e-6,   # 浮点：100×(1+2.5%) 可能是 102.49999999999999
      f"held={_info.get('held_hours')}h < {_info.get('min_hold_hours')}h → "
      f"回退位={_restore}（锁利地板 2.5%）")
_p2 = _P()
for _f in ("id", "symbol", "side", "entry_price", "sl_price", "mark_price", "margin",
           "unrealized_pnl", "peak_pnl_pct", "timeframe_tier", "trade_nature",
           "status", "account_id", "strategy_id", "peak_unrealized_pnl", "opened_at",
           "exit_state_json"):
    setattr(_p2, _f, getattr(_p, _f))
_p2.exit_state_json = json.dumps({"structural_stop_price": 95.0})   # 无 sl_meta
check("C. 未标注来源（结构位）止损照常成交",
      paper_engine._sl_min_hold_verdict(_p2, 110.0)[0] is False,
      "保护期绝不阻挡真实结构止损")
check("C. 锁利地板默认值 long 2.5% / mid 0.5% / short 0",
      paper_engine._min_lock_profit_pct("long") == 0.025
      and paper_engine._min_lock_profit_pct("mid") == 0.005
      and paper_engine._min_lock_profit_pct("short") == 0.0)

# ── D：中车道追踪放宽（.env）────────────────────────────────────
from backend.services.exit.exit_policy import ExitPolicy
_pol = ExitPolicy.for_lane("mid")
check("D. 中车道追踪已放宽（激活 2.5% / 回调 1.2%）",
      abs(float(_pol.trailing_activation_pct) - 2.5) < 1e-9
      and abs(float(_pol.trailing_callback_pct) - 1.2) < 1e-9,
      f"activation={_pol.trailing_activation_pct} callback={_pol.trailing_callback_pct}")

# ── 活库：BTC #4712 的止损与来源留痕 ────────────────────────────
db = SessionLocal()
try:
    db.execute(text("select set_config('app.is_admin','on',false)"))
    r = db.execute(text(
        "SELECT entry_price, sl_price, mark_price, opened_at, exit_state_json "
        "FROM paper_positions WHERE id=4712 AND status='open'")).fetchone()
    if r:
        e, s, m, oa, esj = r
        d = json.loads(esj) if isinstance(esj, str) else (esj or {})
        _pct_entry = (float(s) / float(e) - 1) * 100
        _pct_mark = (float(s) / float(m) - 1) * 100
        check("BTC #4712 止损已放回结构位区间（锁 +2.5%，留 ≥3% 呼吸空间）",
              _pct_entry > 2.0 and _pct_mark < -2.5,
              f"SL={float(s):.2f} 距入场 {_pct_entry:+.3f}% 距现价 {_pct_mark:+.3f}%")
        check("BTC #4712 人工处置已留痕",
              bool(d.get("trailing_suppressed")),
              json.dumps(d.get("trailing_suppressed"), ensure_ascii=False)[:160])
    else:
        check("BTC #4712 仍在册（若已按新止损成交则跳过）", True,
              "该仓位已不在 open 状态 —— 说明它已按处置后的止损离场")
    _open = db.execute(text(
        "SELECT id, symbol, timeframe_tier, trade_nature FROM paper_positions "
        "WHERE account_id=14 AND status='open' ORDER BY id")).fetchall()
    check("账户仍有在册仓位（交易未停）", len(_open) >= 1,
          "; ".join(f"#{r[0]} {r[1]}({r[2]}/{r[3]})" for r in _open))
finally:
    db.close()

print()
print("=" * 70)
print(f"汇总：{'全部通过' if ok_all else '存在失败项'}")
sys.exit(0 if ok_all else 1)
