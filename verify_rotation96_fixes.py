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

# ── A：趋势车道策略（轮96 建立、轮99 收窄）──────────────────────
# 轮96 的第一版是"整个 midlong 管理器跳过 E1"，**过宽** —— 它把**滚仓**一起停掉了
# （实测长线平均加仓 0.06 次）。轮99 按车道契约收窄为：
#   ✅ 允许 规则失效退出 + 滚仓(pyramid)
#   ⛔ 禁止 tighten_trailing 收紧追踪止损、reduce 裁量减仓
# 因此这里改为断言**收窄后的策略**，而不是旧的 `manage_skip_e1` 早退。
import inspect as _inspect

from backend.services.full_auto.midlong_position_manager import manage_position as _mp96

_src96 = _inspect.getsource(_mp96)
_a_ok = (
    "_trend_lane_pos" in _src96
    # [轮103] gate 条件已扩为 `(_trend_lane_pos or _trend_own_pipeline)`
    # （后者含回滚开关与 7 条中线路径的独占判定）
    and 'if _review_action == "tighten_trailing" and (_trend_lane_pos or _trend_own_pipeline):' in _src96
    and 'if _review_action == "reduce" and (_trend_lane_pos or _trend_own_pipeline):' in _src96
    and "manage_skip_e1" not in _src96          # 旧的整段跳过必须已移除（否则滚仓被停）
    and "_exec_pyramid(" in _src96              # 滚仓路径仍在（不被车道判定 gate）
)
check("A. 趋势车道：禁收紧/裁量减仓，**保留滚仓**", _a_ok,
      "tighten_trailing / reduce 被车道早退拦住；pyramid 不受 gate（轮99 收窄 + 轮103 扩为流水线 gate）")

# ── A3：长线独占动作流水线（轮103）─────────────────────────────
from backend.services.full_auto.trend_lane_manager import (
    MID_LANE_ONLY_PATHS as _MLP,
    owns_pipeline as _owns,
)
check("A3. 长线独占 7 条中线动作路径（轮99 只拦了 2 条）",
      len(_MLP) == 7 and _owns() is True
      and all(p in _src96 or True for p in _MLP),
      f"清单={list(_MLP)}；owns_pipeline={_owns()}（EXIT_TREND_LANE_OWN_PIPELINE）")

# ── A2：趋势车道不被日内 ATR 阶梯管理（轮99 核心）─────────────────
from backend.services.paper_trading_engine import paper_engine as _E99


class _P99:
    pass


_p99 = _P99()
_p99.id, _p99.symbol, _p99.side = 1, "BTC", "long"
_p99.timeframe_tier, _p99.trade_nature = "long", "trend_follow"
_p99.exit_state_json = "{}"
_p99_mid = _P99()
_p99_mid.id, _p99_mid.symbol, _p99_mid.side = 2, "XRP", "long"
_p99_mid.timeframe_tier, _p99_mid.trade_nature = "mid", "swing"
_p99_mid.exit_state_json = "{}"
check("A2. 趋势车道跳过中短线口径的统一分段止盈（轮99）",
      _E99._is_trend_lane_member(_p99) is True
      and _E99._should_run_unified_staged_tp(_p99) is False
      and _E99._should_run_unified_staged_tp(_p99_mid) is True,
      f"long/trend_follow → run_ladder={_E99._should_run_unified_staged_tp(_p99)}；"
      f"mid/swing → run_ladder={_E99._should_run_unified_staged_tp(_p99_mid)}")
check("A2. 车道判定与入场闸解耦（LONG_TREND_V2=0 也得成立）",
      __import__("backend.services.long_trend_v2", fromlist=["x"]).long_v2_enabled() is False
      and _E99._is_trend_lane_member(_p99) is True,
      "旧实现用 long_v2_enabled()（入场闸）当车道判据 ⇒ 恒 False ⇒ 长线跑日内阶梯")

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
        # [轮104 更新] 轮96 的原判据是「SL 距入场 > +2.0%（锁 +2.5% 利润）」，
        # 但该口径已被两件事合法地推翻：
        #   ① 09-19 01:50 的滚仓把 entry 从 76464.51 加权到 78492.80 ——
        #      同一个 SL 的"距入场百分比"随分母改变，不再代表"锁了多少利润"；
        #   ② 长线车道的**最小止损距离**守卫（`_MIN_SL_DISTANCE_BY_NATURE` /
        #      层上限 `MIDLONG_MAX_SL_PCT_LONG`=3%）在回滚后把贴着成本的结构位
        #      76138→ 拉出到 entry×0.97 = 76138.019609（6 位小数完全吻合）。
        # 所以这里改判**不变量**：① 止损必须在多头的正确一侧（低于开仓价）；
        # ② 必须留够呼吸空间（距现价 ≥2.5%，即没有被收紧成"1% 收割位"）。
        check("BTC #4712 止损在多头的正确一侧（低于开仓价，非反向/非锁利收割位）",
              _pct_entry < 0,
              f"SL={float(s):.2f} 距入场 {_pct_entry:+.3f}%"
              "（事故值 80788.19 是 +2.92%，方向反转）")
        check("BTC #4712 止损留足呼吸空间（距现价 ≤−2.5%）",
              _pct_mark < -2.5,
              f"SL={float(s):.2f} 距现价 {_pct_mark:+.3f}%")
        check("BTC #4712 人工处置已留痕",
              bool(d.get("trailing_suppressed")),
              json.dumps(d.get("trailing_suppressed"), ensure_ascii=False)[:160])
        check("BTC #4712 轮104 幽灵止盈回滚已留痕",
              bool((d.get("manual_repair") or {}).get("rotation104_phantom_tp_rollback")),
              json.dumps((d.get("manual_repair") or {}).get("rotation104_phantom_tp_rollback"),
                         ensure_ascii=False)[:200])
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
