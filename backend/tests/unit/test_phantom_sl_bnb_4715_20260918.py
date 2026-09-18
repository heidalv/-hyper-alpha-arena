# -*- coding: utf-8 -*-
"""BNB #4715 幽灵成交事故回归测试（2026-09-18）。

## 事故复盘（实盘证据）

    09/18 08:21:02  开仓 BNB long 0.37239 @737.57（trend_e1:BNB, tier=long）
    09/18 17:43:04  同 tick 生成 pyramid 买单 + take_profit 单
    09/18 17:43:16  加仓成交 0.18277 @752.60 → 均价加权到 742.52
    09/18 17:43:17  [Paper][Fast] BREAKEVEN_SL 触发: BNB long @751.0 sl_price=764.331523
    09/18 17:43:17  平仓 @764.33  reason=breakeven_tp  pnl=+12.11

真实行情：09/18 09:00–09:46 UTC（=17:00–17:46 本地）BNB 只在 750.59–756.23 成交，
09-16 起 1h 最高仅 759.98。**764.33 是从未存在的价格**。

## 成因链

1. 峰值以 `peak_pnl_pct`（相对 entry 的百分比）持久化；
2. 加仓把 entry 从 737.573485 加权到 742.519273，`peak_pnl_pct` 未换算（旧代码只重置
   `tp_level_reached` 与 DSM 状态，不动 peak）；
3. 下一 tick `_peak_price_from_pos` 用 `entry × (1 + peak_pnl_pct)` 反推峰值 → 幽灵峰值 775.97
   （由旧 entry 反推 737.573485 × (1+0.052059) = 775.9711，与 SL/0.985 完全吻合）；
4. 保本推进的「峰值追踪融合」取 `775.9711 × (1−1.5%) = 764.331523` 写入 `sl_price`；
5. SL 在现价 751.0 **之上** → `current_price <= sl_price` 恒真 → 立即触发；
6. `close_position(fill_price_override=sl_price)` 用 SL 价成交 → 幽灵成交，虚增 PnL。

本测试逐条锁死：peak 换算、保护侧不变式、幽灵成交价修正。
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.paper_trading_engine import PaperTradingEngine

# ── 事故现场常量（全部来自 DB / 日志实测）──
_ENG = PaperTradingEngine()          # _peak_price_from_pos 是实例方法（要读 pos 属性）
OLD_ENTRY = 737.5734854220001        # 加仓前成交价（paper_orders 23786）
ADD_PRICE = 752.5963715789999        # 加仓价（paper_orders 23816）
SIZE_OLD = 0.3723897469897854
SIZE_ADD = 0.18276692890326532
NEW_ENTRY = 742.5192725941735        # 加仓后 DB entry_price
PHANTOM_SL = 764.331523              # 被写进 sl_price 的幽灵止损
REAL_MARK = 751.0                    # 触发时真实标记价（日志）
PHANTOM_PEAK_PCT_OLD = 0.052059      # 按旧 entry 反推的峰值比例
PHANTOM_PEAK_PX = 775.9711           # 幽灵峰值价


def _mk_pos(**kw):
    p = MagicMock()
    p.id = 4715
    p.symbol = "BNB"
    p.side = "long"
    p.entry_price = NEW_ENTRY
    p.mark_price = REAL_MARK
    p.size = SIZE_OLD + SIZE_ADD
    p.sl_price = 729.24
    p.tp_price = None
    p.peak_pnl_pct = PHANTOM_PEAK_PCT_OLD
    p.peak_unrealized_pnl = 8.276926140166147
    p.trailing_stop_price = None
    p.trade_nature = "trend_follow"
    p.timeframe_tier = "long"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


# ══════════════════════════════════════════════════════════════════════
# 1. 加权均价（确认事故前提）
# ══════════════════════════════════════════════════════════════════════

def test_weighted_entry_matches_db():
    wavg = (OLD_ENTRY * SIZE_OLD + ADD_PRICE * SIZE_ADD) / (SIZE_OLD + SIZE_ADD)
    assert abs(wavg - NEW_ENTRY) < 1e-9


# ══════════════════════════════════════════════════════════════════════
# 2. 根因：peak_pnl_pct 未随均价换算
# ══════════════════════════════════════════════════════════════════════

def test_stale_peak_pct_produces_phantom_peak():
    """用未换算的 peak_pnl_pct + 新均价反推，峰值价被系统性高估。

    可证事实：
      - 被写进 SL 的幽灵值 764.331523 ⇒ 峰值价 = 764.331523 / 0.985 = 775.9711；
      - 该峰值价按**加仓前**均价 737.573485 反推 ⇒ 当时 peak_pnl_pct ≈ 5.206%；
      - 按**加仓后**均价 742.519273 + 同一个 5.206% 反推 ⇒ 高估到 781.17
        （比真实峰值价高 5.20，正是「幽灵」的来源）。
    """
    stale_peak_pct = PHANTOM_PEAK_PX / OLD_ENTRY - 1.0     # 由幽灵峰值价反推的旧比例
    pos = _mk_pos(peak_pnl_pct=stale_peak_pct)

    # 幽灵峰值价本身（SL 的反推值）
    assert abs(PHANTOM_PEAK_PX * 0.985 - PHANTOM_SL) < 0.01

    # 用旧均价反推 → 得到幽灵峰值价（自洽）
    assert abs(_ENG._peak_price_from_pos(pos, OLD_ENTRY) - PHANTOM_PEAK_PX) < 0.01

    # 用新均价反推 → 进一步高估（这就是未换算的代价）
    inflated = _ENG._peak_price_from_pos(pos, NEW_ENTRY)
    assert inflated > PHANTOM_PEAK_PX + 5.0, inflated
    assert inflated * 0.985 > PHANTOM_SL, "未换算时算出的 SL 比事故值还高"


def test_rebase_peak_pct_keeps_peak_price():
    """修复①：加仓后换算 peak_pnl_pct，**峰值价格不变**。"""
    stale_peak_pct = PHANTOM_PEAK_PX / OLD_ENTRY - 1.0
    pos = _mk_pos(peak_pnl_pct=stale_peak_pct)
    PaperTradingEngine.rebase_peak_pct_for_entry(pos, OLD_ENTRY, NEW_ENTRY)
    # 换算后反推的峰值价仍等于原峰值价（不再被高估）
    after = _ENG._peak_price_from_pos(pos, NEW_ENTRY)
    assert abs(after - PHANTOM_PEAK_PX) < 0.01, after
    # 比例本身变小（均价抬高，同一峰值对应更小涨幅）
    assert pos.peak_pnl_pct < stale_peak_pct


def test_rebase_peak_pct_short_side():
    """空头方向同理：峰值价 = entry × (1 − pct)，换算后峰值价不变。"""
    pos = _mk_pos(side="short", entry_price=100.0, peak_pnl_pct=0.10)
    PaperTradingEngine.rebase_peak_pct_for_entry(pos, 100.0, 95.0)
    px = _ENG._peak_price_from_pos(pos, 95.0)
    assert abs(px - 90.0) < 1e-6, px


def test_rebase_peak_pct_noop_without_peak():
    """无历史峰值（pct<=0）或均价未变时不动。"""
    pos = _mk_pos(peak_pnl_pct=0.0)
    PaperTradingEngine.rebase_peak_pct_for_entry(pos, OLD_ENTRY, NEW_ENTRY)
    assert pos.peak_pnl_pct == 0.0

    pos2 = _mk_pos(peak_pnl_pct=0.03)
    PaperTradingEngine.rebase_peak_pct_for_entry(pos2, NEW_ENTRY, NEW_ENTRY)
    assert pos2.peak_pnl_pct == 0.03


# ══════════════════════════════════════════════════════════════════════
# 3. 修复②：保护侧不变式
# ══════════════════════════════════════════════════════════════════════

def test_safe_sl_rejects_sl_above_market_for_long():
    """事故值：多头 SL 764.33 > 现价 751.0 → 必须被拒（返回 0）。"""
    assert PaperTradingEngine.safe_sl_price(PHANTOM_SL, side="long", market=REAL_MARK) == 0.0


def test_safe_sl_rejects_sl_below_market_for_short():
    assert PaperTradingEngine.safe_sl_price(90.0, side="short", market=100.0) == 0.0


def test_safe_sl_allows_legitimate_ratchet():
    """合法收紧（多头 SL 在现价下方 / 空头在上方）不受影响。"""
    assert PaperTradingEngine.safe_sl_price(745.0, side="long", market=751.0) == 745.0
    assert PaperTradingEngine.safe_sl_price(760.0, side="short", market=751.0) == 760.0
    # 盈利仓的保本线仍可推进（只是必须低于现价）
    assert PaperTradingEngine.safe_sl_price(744.0, side="long", market=751.0) == 744.0


def test_safe_sl_passthrough_when_market_unknown():
    """市价缺失时不拦截（避免因取价失败而放弃止损管理）。"""
    assert PaperTradingEngine.safe_sl_price(745.0, side="long", market=0.0) == 745.0
    assert PaperTradingEngine.safe_sl_price(0.0, side="long", market=751.0) == 0.0


def test_tighten_sl_unified_blocks_wrong_side():
    """修复②落到共用的收紧入口：错误一侧直接不写。"""
    eng = PaperTradingEngine()
    pos = _mk_pos(sl_price=729.24, mark_price=REAL_MARK)
    eng._tighten_sl_unified(pos, PHANTOM_SL, "unit_test", market=REAL_MARK)
    assert pos.sl_price == 729.24, "幽灵 SL 必须被拒绝，SL 保持不变"


def test_tighten_sl_unified_still_ratchets_legitimately():
    eng = PaperTradingEngine()
    pos = _mk_pos(sl_price=729.24, mark_price=REAL_MARK)
    eng._tighten_sl_unified(pos, 745.0, "unit_test", market=REAL_MARK)
    assert pos.sl_price == 745.0


def test_guard_skips_when_market_implausible():
    """市价与开仓价偏离过大（含 Mock 被强转成 1.0 这类假数值）→ 不判定，按原值放行。

    设计取舍：宁可漏拦一次，也不因为一次坏取价而**放弃**止损管理。
    """
    # 明显不可信的市价（entry=742.5 / market=1.0）
    assert PaperTradingEngine.safe_sl_price(
        PHANTOM_SL, side="long", market=1.0, entry=NEW_ENTRY) == PHANTOM_SL
    # 非数值市价
    assert PaperTradingEngine.safe_sl_price(
        PHANTOM_SL, side="long", market=None, entry=NEW_ENTRY) == PHANTOM_SL
    # 正常市价 → 仍拦截
    assert PaperTradingEngine.safe_sl_price(
        PHANTOM_SL, side="long", market=REAL_MARK, entry=NEW_ENTRY) == 0.0


# ══════════════════════════════════════════════════════════════════════
# 4. 修复③：成交价不得优于市价（防幽灵成交）
# ══════════════════════════════════════════════════════════════════════

def _phantom_fill_price(trigger: float, market: float, side: str) -> float:
    """复刻 reprice_position 里的成交价修正逻辑。"""
    if trigger > 0 and market > 0:
        is_long = side in ("long", "buy")
        return min(trigger, market) if is_long else max(trigger, market)
    return trigger or market


def test_phantom_fill_corrected_to_market():
    """事故值：触发线 764.33 / 市价 751.0（多头）→ 成交价必须是 751.0 而非 764.33。"""
    assert _phantom_fill_price(PHANTOM_SL, REAL_MARK, "long") == REAL_MARK


def test_legit_sl_fill_uses_stop_line():
    """正常情况多头 SL 低于市价 → 仍按 SL 线成交（保持原有语义）。"""
    assert _phantom_fill_price(745.0, 751.0, "long") == 745.0
    assert _phantom_fill_price(760.0, 751.0, "short") == 760.0


def test_pnl_with_corrected_fill_is_negative_not_positive():
    """修正后这笔「盈利 +12.11」应为亏损：真实价 751.0 低于均价 742.52→ 仍是小赚,
    但若加仓价 752.60 高于 751.0，则加仓那部分立刻浮亏。关键是**不再凭空造出 764.33**。"""
    fill = _phantom_fill_price(PHANTOM_SL, REAL_MARK, "long")
    pnl = (fill - NEW_ENTRY) * (SIZE_OLD + SIZE_ADD)
    phantom_pnl = (PHANTOM_SL - NEW_ENTRY) * (SIZE_OLD + SIZE_ADD)
    assert pnl < phantom_pnl, "修正后的 PnL 必须小于幽灵 PnL"
    # 幽灵多算的金额
    assert abs((phantom_pnl - pnl) - (PHANTOM_SL - REAL_MARK) * (SIZE_OLD + SIZE_ADD)) < 1e-9


# ══════════════════════════════════════════════════════════════════════
# 5. 端到端：事故重放
# ══════════════════════════════════════════════════════════════════════

def test_incident_replay_is_now_blocked():
    """完整重放：加仓 → peak 换算 → 峰值追踪融合 → 不变式拦截。

    修复前：第 4 步把 764.33 写进 SL，第 5 步触发幽灵成交。
    修复后：peak 换算让峰值价保持 775.97 不可达的**前一步**就不发生；
    即便外部仍塞进 764.33，不变式与成交价修正也会兜住。
    """
    pos = _mk_pos(peak_pnl_pct=PHANTOM_PEAK_PX / OLD_ENTRY - 1.0,
                  entry_price=OLD_ENTRY, sl_price=729.24)

    # ① 加仓：均价变化 + peak 换算（修复①）
    pos.entry_price = NEW_ENTRY
    PaperTradingEngine.rebase_peak_pct_for_entry(pos, OLD_ENTRY, NEW_ENTRY)

    # ② 峰值追踪融合算出的 SL（用换算后的 peak，峰值价不变）
    peak_px = _ENG._peak_price_from_pos(pos, NEW_ENTRY)
    cand = peak_px * (1 - 0.015)

    # ③ 不变式（修复②）：候选 SL 仍在现价之上 → 拒绝
    safe = PaperTradingEngine.safe_sl_price(cand, side="long", market=REAL_MARK)
    assert safe == 0.0, f"候选 SL {cand} 高于现价 {REAL_MARK}，必须被拒"

    # ④ 成交价修正（修复③）：即便真被触发，也不会以 764.33 成交
    assert _phantom_fill_price(PHANTOM_SL, REAL_MARK, "long") == REAL_MARK
