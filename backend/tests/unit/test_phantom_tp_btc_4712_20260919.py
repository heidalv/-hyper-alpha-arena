# -*- coding: utf-8 -*-
"""BTC #4712 幽灵止盈事故回归测试（2026-09-19，轮104）。

## 事故复盘（DB / 日志实证）

    09/18 21:44     开仓 BTC long（trend_e1:BTC, tier=long, nature=trend_follow）
    09/19 01:50:11.307  滚仓买单 23846 buy 0.00165536 @80968.54 + 同时生成
                        take_profit 单 23847 sell @65689.43
    09/19 01:50:15.684  秒级快路径：现价 80808.3 ≥ TP 65689.43 → 立即成立
    09/19 01:50:15.748  market sell 23848 成交 @65689.43  reason=tp
    09/19 01:50:15.865  持仓 #4712 status=closed close_price=65689.42793
                        close_reason=tp upnl=-41.4485

真实市价 80808.3，成交价 65689.43 **低于市价 19%** —— 一个从未存在的价格。
这是一笔**本应 +8.51 的浮盈仓**（(80808.3−78492.80)×0.00367590），被系统按
"止盈"平掉并凭空记为 −41.45。

## 成因链（本轮已逐环修复）

1. `position_memory_manager._calc_tp_sl` 只用 `side == "buy"` 判多空；
2. 滚仓路径（`midlong_position_manager._exec_pyramid` → `evaluate_pyramid`）
   传进来的是 `_pos_direction()` 归一后的 **"long"**（不是 "buy"）；
3. 于是多头仓走 `else`（**空头分支**）：
      sl = price × (1 + sl_base)   → 止损跑到开仓价**上方** 2.92%
      tp = price × (1 − tp_base)   → 止盈跑到开仓价**下方** 16.31%
   **可证等式**：`80788.191962 / 1.03 == 65689.42793 / 0.8375 == 78435.1378`
   （同一个参考价分别套了两条**空头**公式，误差 3.2e-7）；
   参考价 78435.1378 是 plan 用的 `new_avg`（按决策时 mark 80808.3 加权），
   与成交后 DB entry 78492.80372 差 57.66 —— 正是"成交价 80968.54 高于 mark"。
4. 该 TP/SL 经 `place_order(add_type="pyramid")` 直接写入持仓
   （`existing.tp_price = order.tp_price`，绕过了 `update_position_tp_sl`）；
5. 秒级快路径 `reprice_position` 的 `现价 >= TP` 判定恒真 → 4.4 秒后成交；
6. 幽灵成交价修正逻辑（轮64 为 SL 加的 `min(触发线, 市价)`）**对多头 TP 恰好失效**：
   `min(65689.43, 80808.3) = 65689.43` —— "修正"反而确认了幽灵价。

之所以潜伏至今：滚仓（pyramid）此前几乎从不触发（近 30 天长线平均加仓 0.06 次），
轮99/102 恢复"长线=滚仓盈利引擎"后它才第一次真正跑起来。

本测试锁死四道防线：① `_calc_tp_sl` 词表归一；② 写入侧 `safe_tp_price`；
③ 触发侧 `tp_direction_illegal`；④ `evaluate_dca` 同类词表漏洞。
"""
import ast
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.paper_trading_engine import PaperTradingEngine
from backend.services.position_memory_manager import (
    MemoryInsight,
    PositionMemoryManager,
)

# ── 事故现场常量（全部来自 DB / 日志实测，不得手改）──
PHANTOM_TP = 65689.42793            # 订单 23847/23848 的价格；被当作"止盈价"成交
INCIDENT_SL = 80788.191962          # 同一次写入的 sl_price
DB_ENTRY_AFTER_ADD = 78492.80372019952   # 滚仓后 DB entry_price
TOTAL_SIZE = 0.0036758964629474136  # 滚仓后 size
ADD_SIZE = 0.00165536               # 订单 23846 数量
OLD_SIZE = TOTAL_SIZE - ADD_SIZE
ADD_FILL = 80968.54                 # 订单 23846 成交价
MARK_AT_TRIGGER = 80808.3           # 触发时标记价
PLAN_REF_PRICE = 78435.1378         # 由两条空头公式反解出的同一参考价
SL_BASE = 0.03                      # tier=long 的止损封顶（MIDLONG_MAX_SL_PCT_LONG）
TP_BASE = 0.065 * 2.5               # trend_follow: sl_base_min × rr_low_wr
TREND_E1_ENGINE = "backend/services/paper_trading_engine.py"
PMM_PATH = "backend/services/position_memory_manager.py"

_ENG = PaperTradingEngine()
_PMM = PositionMemoryManager()


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _mk_pos(**kw):
    p = MagicMock()
    p.id = 4712
    p.symbol = "BTC"
    p.side = "long"
    p.entry_price = DB_ENTRY_AFTER_ADD
    p.mark_price = MARK_AT_TRIGGER
    p.size = TOTAL_SIZE
    p.tp_price = PHANTOM_TP
    p.sl_price = INCIDENT_SL
    p.trade_nature = "trend_follow"
    p.timeframe_tier = "long"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


# ══════════════════════════════════════════════════════════════════════
# 1. 事故算术：两个价格出自同一个参考价 × 两条**空头**公式
# ══════════════════════════════════════════════════════════════════════

def test_both_prices_came_from_the_same_reference_via_short_formulas():
    """核心证据：sl/1.03 与 tp/0.8375 反解出**同一个**参考价（误差 3.2e-7）。

    多头分支给不出这两个数（长线多头应为 sl=ref×0.97 / tp=ref×1.1625）。
    """
    ref_from_sl = INCIDENT_SL / (1 + SL_BASE)
    ref_from_tp = PHANTOM_TP / (1 - TP_BASE)
    assert abs(ref_from_sl - ref_from_tp) < 1e-4, (ref_from_sl, ref_from_tp)
    assert abs(ref_from_sl - PLAN_REF_PRICE) < 0.01
    # 空头公式确实产出事故值
    assert abs(PLAN_REF_PRICE * (1 + SL_BASE) - INCIDENT_SL) < 0.01
    assert abs(PLAN_REF_PRICE * (1 - TP_BASE) - PHANTOM_TP) < 0.02


def test_long_branch_would_never_produce_the_incident_values():
    """反证：多头分支的公式在事故当刻不可能产出这两个价（差异 4700 / 25000）。"""
    long_sl = PLAN_REF_PRICE * (1 - SL_BASE)
    long_tp = PLAN_REF_PRICE * (1 + TP_BASE)
    assert abs(long_sl - INCIDENT_SL) > 4000
    assert abs(long_tp - PHANTOM_TP) > 20000
    # 多头分支下 TP 一定在开仓价上方、SL 在下方 —— 与事故现场正好相反
    assert long_tp > PLAN_REF_PRICE > long_sl > 0


def test_incident_directions_are_inverted_for_a_long():
    assert PHANTOM_TP < DB_ENTRY_AFTER_ADD, "多头 TP 却低于开仓价（本事故的核心异常）"
    assert INCIDENT_SL > DB_ENTRY_AFTER_ADD, "多头 SL 却高于开仓价（同一次错误写入）"
    # 这笔仓在触发时其实是浮盈的
    true_upnl = (MARK_AT_TRIGGER - DB_ENTRY_AFTER_ADD) * TOTAL_SIZE
    assert true_upnl > 8.0, true_upnl


def test_weighted_entry_matches_db():
    """确认滚仓均价（本测试后续都以 DB entry 为参照）。"""
    old_entry = (DB_ENTRY_AFTER_ADD * TOTAL_SIZE - ADD_FILL * ADD_SIZE) / OLD_SIZE
    wavg = (old_entry * OLD_SIZE + ADD_FILL * ADD_SIZE) / TOTAL_SIZE
    assert abs(wavg - DB_ENTRY_AFTER_ADD) < 1e-6
    # 事故参考价（按 mark 加权）低于成交后均价，差 57.66 —— 见模块 docstring 第 3 条
    assert 50 < DB_ENTRY_AFTER_ADD - PLAN_REF_PRICE < 70


# ══════════════════════════════════════════════════════════════════════
# 2. 防线①：`_calc_tp_sl` 的 side 词表归一（根因）
# ══════════════════════════════════════════════════════════════════════

def _calc(side, price=1000.0, leverage=10, vol=0.015, tier="long", win_rate=0.4):
    return _PMM._calc_tp_sl(
        side, price, leverage, vol, 0, 0,
        MemoryInsight(symbol_win_rate=win_rate), tier=tier,
    )


@pytest.mark.parametrize("alias", ["long", "buy", "LONG", "Buy", " long "])
def test_calc_tp_sl_long_aliases_all_produce_long_side_prices(alias):
    """`long` 与 `buy` 必须完全等价 —— 事故就是它们被当成两个方向。"""
    tp, sl = _calc(alias)
    assert tp > 1000.0 > sl > 0, (alias, tp, sl)
    assert (tp, sl) == _calc("buy")


@pytest.mark.parametrize("alias", ["short", "sell", "SHORT", "Sell"])
def test_calc_tp_sl_short_aliases_all_produce_short_side_prices(alias):
    tp, sl = _calc(alias)
    assert tp < 1000.0 < sl, (alias, tp, sl)
    assert (tp, sl) == _calc("sell")
    # 事故值形态：TP 在下方、SL 在上方 —— 这正是被套到多头仓上的那一对
    assert sl > 1000.0 > tp


def test_calc_tp_sl_long_never_inverts_across_inputs():
    """横扫参数空间：多头 TP 永远在开仓价上方、SL 永远在下方。

    这条性质是本轮修复的**不变量** —— 只要它成立，#4712 那种写入就不可能再发生。
    """
    for tier in ("long", "mid", "short", "swing", "trend_follow", "position", "scalp"):
        for lev in (1, 3, 4, 8, 12, 20, 50):
            for vol in (0.002, 0.015, 0.05, 0.12):
                for wr in (0.2, 0.5, 0.9):
                    tp, sl = _calc("long", leverage=lev, vol=vol, tier=tier, win_rate=wr)
                    assert tp > 1000.0, (tier, lev, vol, wr, tp)
                    assert 0 < sl < 1000.0, (tier, lev, vol, wr, sl)


def test_calc_tp_sl_short_never_inverts_across_inputs():
    for tier in ("long", "mid", "short", "swing"):
        for lev in (1, 4, 12, 20):
            for vol in (0.002, 0.015, 0.05):
                tp, sl = _calc("short", leverage=lev, vol=vol, tier=tier)
                assert tp < 1000.0, (tier, lev, vol, tp)
                assert sl > 1000.0, (tier, lev, vol, sl)


def test_calc_tp_sl_source_has_no_raw_buy_comparison():
    """源码级棘轮：`_calc_tp_sl` 内不得再出现 `side == "buy"` 这类裸比较。

    用 AST 而不是文本匹配 —— 函数 docstring 里为了记录历史**引用**了旧代码，
    文本匹配会被自己的注释误伤。
    """
    tree = ast.parse(open(os.path.join(_repo_root(), PMM_PATH), encoding="utf-8").read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_calc_tp_sl")
    raw = [
        (x.lineno, ast.unparse(x))
        for x in ast.walk(fn)
        if isinstance(x, ast.Compare)
        for op, c in zip(x.ops, x.comparators)
        if isinstance(op, ast.Eq) and isinstance(c, ast.Constant) and c.value in ("buy", "sell")
    ]
    assert raw == [], f"_calc_tp_sl 又有裸方向比较，请改用归一后的布尔量: {raw}"


# ══════════════════════════════════════════════════════════════════════
# 3. 防线②：写入侧不变式 safe_tp_price
# ══════════════════════════════════════════════════════════════════════

def test_safe_tp_rejects_incident_value_for_long():
    """事故值：多头 TP 65689.43 < 开仓价 78492.80 → 必须被拒（返回 0）。"""
    assert PaperTradingEngine.safe_tp_price(
        PHANTOM_TP, side="long", market=MARK_AT_TRIGGER,
        entry=DB_ENTRY_AFTER_ADD) == 0.0


def test_safe_tp_rejects_tp_above_entry_for_short():
    assert PaperTradingEngine.safe_tp_price(
        1100.0, side="short", market=1000.0, entry=1000.0) == 0.0


def test_safe_tp_allows_legitimate_targets():
    # 多头 TP 在开仓价上方 → 放行（哪怕现价已越过 TP，那是正常触发）
    assert PaperTradingEngine.safe_tp_price(
        90000.0, side="long", market=MARK_AT_TRIGGER,
        entry=DB_ENTRY_AFTER_ADD) == 90000.0
    # 现价已越过 TP 的**正常触发**不得被误判
    assert PaperTradingEngine.safe_tp_price(
        80000.0, side="long", market=MARK_AT_TRIGGER,
        entry=DB_ENTRY_AFTER_ADD) == 80000.0
    # 空头 TP 在开仓价下方
    assert PaperTradingEngine.safe_tp_price(
        900.0, side="short", market=950.0, entry=950.0) == 900.0
    # buy/sell 同义
    assert PaperTradingEngine.safe_tp_price(
        90000.0, side="buy", market=MARK_AT_TRIGGER,
        entry=DB_ENTRY_AFTER_ADD) == 90000.0


def test_safe_tp_uses_entry_not_market_as_the_side_boundary():
    """判据是 entry：多头 TP 只要在开仓价之上就合法，不需要高于现价。

    否则「先把 TP 挪到现价下方锁利」这类合法收紧会被误拦
    （锁利型 TP 是正常工具；只有**反方向**的 TP 才是事故）。
    """
    lock_tp = DB_ENTRY_AFTER_ADD + 1.0          # 略高于成本、远低于现价
    assert lock_tp < MARK_AT_TRIGGER
    assert PaperTradingEngine.safe_tp_price(
        lock_tp, side="long", market=MARK_AT_TRIGGER,
        entry=DB_ENTRY_AFTER_ADD) == lock_tp


def test_safe_tp_rejects_zero_and_negative():
    assert PaperTradingEngine.safe_tp_price(
        0.0, side="long", market=100.0, entry=100.0) == 0.0
    assert PaperTradingEngine.safe_tp_price(
        -5.0, side="long", market=100.0, entry=100.0) == 0.0
    assert PaperTradingEngine.safe_tp_price(
        None, side="long", market=100.0, entry=100.0) == 0.0


def test_safe_tp_passthrough_when_entry_or_market_unknown():
    """信息不足时按原值放行 —— 不因一次取价失败而放弃止盈管理。"""
    assert PaperTradingEngine.safe_tp_price(
        PHANTOM_TP, side="long", market=0.0, entry=0.0) == PHANTOM_TP
    assert PaperTradingEngine.safe_tp_price(
        PHANTOM_TP, side="long", market=None, entry=None) == PHANTOM_TP
    # 现价与开仓价偏离 50% 以上 → 视为坏取价，不做方向判定
    assert PaperTradingEngine.safe_tp_price(
        PHANTOM_TP, side="long", market=1.0, entry=DB_ENTRY_AFTER_ADD) == PHANTOM_TP


def test_safe_tp_falls_back_to_market_when_entry_missing():
    """没有开仓价时用现价兜底：多头 TP 在市价下方视为非法。"""
    assert PaperTradingEngine.safe_tp_price(
        500.0, side="long", market=1000.0, entry=0.0) == 0.0
    assert PaperTradingEngine.safe_tp_price(
        1500.0, side="long", market=1000.0, entry=0.0) == 1500.0
    assert PaperTradingEngine.safe_tp_price(
        1500.0, side="short", market=1000.0, entry=0.0) == 0.0


# ══════════════════════════════════════════════════════════════════════
# 4. 防线③：触发侧 tp_direction_illegal
# ══════════════════════════════════════════════════════════════════════

def test_trigger_guard_blocks_the_incident():
    pos = _mk_pos()
    assert _ENG.tp_direction_illegal(pos, PHANTOM_TP, MARK_AT_TRIGGER) is True


def test_trigger_guard_allows_legit_tp():
    pos = _mk_pos(tp_price=90000.0)
    assert _ENG.tp_direction_illegal(pos, 90000.0, MARK_AT_TRIGGER) is False


def test_trigger_guard_fail_open_without_entry():
    pos = _mk_pos(entry_price=0)
    assert _ENG.tp_direction_illegal(pos, PHANTOM_TP, MARK_AT_TRIGGER) is False


def test_trigger_guard_blocks_on_short_too():
    pos = _mk_pos(side="short", entry_price=1000.0, tp_price=1200.0)
    assert _ENG.tp_direction_illegal(pos, 1200.0, 1100.0) is True
    assert _ENG.tp_direction_illegal(pos, 900.0, 1100.0) is False


def test_incident_trigger_condition_would_be_true_but_for_the_guard():
    """重放秒级快路径的判定：不加闸 → 必然成交；加闸 → 拒绝。"""
    current_price = MARK_AT_TRIGGER
    hit_tp = (pos_side_long := True) and current_price >= PHANTOM_TP
    assert hit_tp is True, "事故的触发条件是恒真的（现价 80808 ≥ TP 65689）"
    assert _ENG.tp_direction_illegal(_mk_pos(), PHANTOM_TP, current_price) is True


# ══════════════════════════════════════════════════════════════════════
# 5. 源码级棘轮：四道防线都还在
# ══════════════════════════════════════════════════════════════════════

def _calls_in(path: str):
    tree = ast.parse(open(os.path.join(_repo_root(), path), encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name in ("safe_tp_price", "tp_direction_illegal"):
                out.append((name, node.lineno))
    return out


def test_engine_wires_both_tp_guards():
    """写入侧 4 处 + 触发侧 4 处 + 定义内部 1 次自用。

    数字是**棘轮**：将来改动必须同步改这里，避免闸门被悄悄摘掉。
    """
    calls = _calls_in(TREND_E1_ENGINE)
    by_name = {}
    for name, lineno in calls:
        by_name.setdefault(name, []).append(lineno)
    assert len(by_name.get("safe_tp_price", [])) == 5, by_name
    assert len(by_name.get("tp_direction_illegal", [])) == 4, by_name


def test_engine_tp_guard_sites_are_the_four_known_paths():
    """逐点确认：下单收口、建仓、加仓合并、AI改TP、秒级快路径、官方TP、时限兜底、v2。"""
    src = open(os.path.join(_repo_root(), TREND_E1_ENGINE), encoding="utf-8").read()
    for marker in (
        "下单 TP 被止盈侧不变式拦截",
        "建仓TP被止盈侧不变式拦截",
        "加仓TP被止盈侧不变式拦截",
        "AI调整TP 被止盈侧不变式拦截",
        "TP 触发被止盈侧不变式拦截",
    ):
        assert marker in src, f"缺少写入/触发侧留痕: {marker}"


def test_update_position_tp_sl_rejects_inverted_tp_and_keeps_sl():
    """`update_position_tp_sl` 收到反向 TP：拒绝写 TP，但**不得**吞掉同一次的 SL 调整。"""
    import inspect
    src = inspect.getsource(PaperTradingEngine.update_position_tp_sl)
    assert "safe_tp_price" in src
    # 拦截分支里不能提前 return（否则同一次调用的 SL 会被丢掉）
    block = src.index("AI调整TP 被止盈侧不变式拦截")
    tail = src[block:block + 400]
    assert "return" not in tail.split("else:")[0], "TP 拦截分支不得提前 return"


def test_evaluate_dca_normalizes_side_vocabulary():
    """`evaluate_dca` 的同类漏洞：`side == "buy"` 会让 long 仓按空头判定。

    两处后果：`expected_bias` 反向（要求 bearish 才允许补仓）、
    `pos_side` 反向（同方向总敞口统计的是反方向保证金）。
    """
    tree = ast.parse(open(os.path.join(_repo_root(), PMM_PATH), encoding="utf-8").read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "evaluate_dca")
    raw = [
        (x.lineno, ast.unparse(x))
        for x in ast.walk(fn)
        if isinstance(x, ast.Compare)
        for op, c in zip(x.ops, x.comparators)
        if isinstance(op, ast.Eq) and isinstance(c, ast.Constant) and c.value in ("buy", "sell")
    ]
    assert raw == [], f"evaluate_dca 又有裸方向比较: {raw}"
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "_side_is_long" in names, "缺少归一后的方向布尔量"


# ══════════════════════════════════════════════════════════════════════
# 6. 端到端重放：修好后这笔滚仓不会再自杀
# ══════════════════════════════════════════════════════════════════════

def test_replay_pyramid_now_produces_sane_tp_sl():
    """重放：滚仓 → `_calc_tp_sl(side="long", price=new_avg)` → 方向正确。"""
    new_avg = PLAN_REF_PRICE
    tp, sl = _PMM._calc_tp_sl(
        "long", new_avg, 10, 0.015, 0, 0,
        MemoryInsight(symbol_win_rate=0.4), tier="long",
    )
    assert tp > new_avg, "多头 TP 必须在上方"
    assert 0 < sl < new_avg, "多头 SL 必须在下方"
    # 修复后的 TP 距开仓价 +16.25%（原事故是 −16.31%）
    assert abs(tp / new_avg - 1 - TP_BASE) < 0.01
    # 关键：现价 80808.3 不再满足 `现价 >= TP`
    assert not (MARK_AT_TRIGGER >= tp), f"修复后仍会被秒级硬 TP 判定打中: tp={tp}"
    # 若沿用事故值则必然被打中（对照组）
    assert MARK_AT_TRIGGER >= PHANTOM_TP


def test_replay_write_and_trigger_gates_both_hold():
    """四道防线联合重放：写入被拒、触发被拒、TP 保持原值、SL 调整不受影响。"""
    # ① 写入侧
    assert PaperTradingEngine.safe_tp_price(
        PHANTOM_TP, side="long", market=MARK_AT_TRIGGER,
        entry=DB_ENTRY_AFTER_ADD) == 0.0
    # ② 触发侧
    assert _ENG.tp_direction_illegal(_mk_pos(), PHANTOM_TP, MARK_AT_TRIGGER) is True
    # ③ 存量脏数据（假设历史行仍写着 65689）也不会被成交价修正"救回来"
    assert min(PHANTOM_TP, MARK_AT_TRIGGER) == PHANTOM_TP  # 旧的 min() 修正对多头 TP 无效
    # ④ 同一个 TP 若来自正确分支则一切照旧
    good_tp = DB_ENTRY_AFTER_ADD * (1 + TP_BASE)
    assert PaperTradingEngine.safe_tp_price(
        good_tp, side="long", market=MARK_AT_TRIGGER,
        entry=DB_ENTRY_AFTER_ADD) == pytest.approx(good_tp)


# ══════════════════════════════════════════════════════════════════════
# 7. 加仓合并路径：长线车道不得被写入固定 TP
# ══════════════════════════════════════════════════════════════════════

def _decide(pos, order_tp, market):
    return _ENG.add_order_tp_decision(pos, order_tp, market)


def test_add_on_trend_lane_clears_tp_even_when_the_tp_is_valid():
    """长线车道：一个**方向正确**的 TP 也必须清空。

    修好方向后 `_calc_tp_sl` 给出 `new_avg × 1.1625`（+16.25%），
    会被 Layer-0 硬 TP 判定整仓平掉 —— 与车道契约（tp_pct=null、出场=规则失效/Chandelier、
    盈利靠滚仓）冲突。所以判据是**车道**，不是"TP 是否合法"。
    """
    pos = _mk_pos(timeframe_tier="long", trade_nature="trend_follow",
                  tp_price=None, entry_price=1000.0)
    good_tp = 1162.5
    val, action = _decide(pos, good_tp, 1000.0)
    assert action == "trend_lane_clear", action
    assert val is None
    # 逆天的旧值也一样清
    val2, action2 = _decide(pos, PHANTOM_TP, MARK_AT_TRIGGER)
    assert (val2, action2) == (None, "trend_lane_clear")


def test_add_on_mid_lane_keeps_a_valid_tp():
    pos = _mk_pos(timeframe_tier="mid", trade_nature="swing", entry_price=1000.0)
    val, action = _decide(pos, 1030.0, 1000.0)
    assert action == "write"
    assert val == pytest.approx(1030.0)


def test_add_on_mid_lane_rejects_inverted_tp():
    """事故值原样送进中车道的加仓路径 → 拒绝写入（保留原 TP）。"""
    pos = _mk_pos(timeframe_tier="mid", trade_nature="swing",
                  entry_price=DB_ENTRY_AFTER_ADD, tp_price=90000.0)
    val, action = _decide(pos, PHANTOM_TP, MARK_AT_TRIGGER)
    assert action == "inverted_reject", action
    assert val is None


def test_add_decision_keep_when_no_tp_or_no_position():
    pos = _mk_pos(timeframe_tier="mid", trade_nature="swing")
    assert _decide(pos, None, 1000.0) == (None, "keep")
    assert _decide(pos, 0, 1000.0) == (None, "keep")
    assert _decide(None, 1030.0, 1000.0) == (None, "keep")


def test_add_decision_fails_open_when_market_unknown():
    """取价不可信（market=0）→ 不判方向，照写（不因一次取价失败丢掉止盈）。"""
    pos = _mk_pos(timeframe_tier="mid", trade_nature="swing", entry_price=1000.0)
    val, action = _decide(pos, 1030.0, 0.0)
    assert action == "write"
    assert val == pytest.approx(1030.0)


def test_fast_path_skips_fixed_tp_for_trend_lane():
    """秒级快路径（事故的行刑者）必须按车道跳过固定 TP。

    与 `_run_v2_protection` 的既有跳过保持一致；Layer-0 failsafe 仍在慢速 tick。
    """
    src = open(os.path.join(_repo_root(), TREND_E1_ENGINE), encoding="utf-8").read()
    body = src[src.index("def reprice_position("):]
    tp_block = body[body.index("if not hit and pos.tp_price"):]
    tp_block = tp_block[: tp_block.index("if hit:")]
    assert "_is_trend_lane_member" in tp_block, "快路径未按车道跳过固定 TP"
    i_lane = tp_block.index("_is_trend_lane_member")
    i_hit = tp_block.index("current_price >= float(pos.tp_price)")
    assert i_lane < i_hit, "车道判定必须在 TP 命中判定之前"


def test_merge_path_uses_the_shared_decision():
    src = open(os.path.join(_repo_root(), TREND_E1_ENGINE), encoding="utf-8").read()
    assert "self.add_order_tp_decision(existing, order.tp_price" in src


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))

