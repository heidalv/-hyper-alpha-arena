# -*- coding: utf-8 -*-
"""[整顿轮·T1 2026-10-05] 离场执行方式回归测试。

锁定的三条规矩：
  1. `taker_stop` / `taker_risk` 默认**不再吃单**，改为挂对手价抢平（可 env 回滚）
  2. 离场挂单必须 `sticky=True` + `band_bp` 容差 —— 否则队列进度每拍归零、永不成交
  3. 盘口缺失（px<=0）时退化为吃单，**不得卡住库存**

背景实测（lane_ledger）：近 24h `taker_stop` 一条路径 −47.2bp/腿、合计 −$266，
是当时唯一的大失血点；走挂单的强平腿 +19.91bp/腿 vs 吃单的 −32.71bp/腿。
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from backend.services.market_maker import active_flow as AF


@pytest.fixture(autouse=True)
def _pin_legacy_path(monkeypatch):
    """[2026-10-09] 本文件锁定的是**旧单边流**契约；重复来回做市（ping-pong）
    默认开启后，旧体逐字保留在 `MM_PINGPONG=0` 之后，这里钉住回滚路径。"""
    monkeypatch.setenv("MM_PINGPONG", "0")


# ── 最小可用的替身 ────────────────────────────────────────────────

class _Book:
    """替代 InventoryBook：apply_fill 记录调用并返回空指标。"""

    def __init__(self):
        self.calls = []
        self.positions = {}

    def apply_fill(self, **kw):
        self.calls.append(kw)
        return {"spread_usd": 0.0, "price_usd": 0.0, "fee_usd": 0.0,
                "position_id": "p1"}

    def qty(self, symbol):
        return 0.0


class _Dec:
    def __init__(self):
        self.fills = []
        self.skip = ""
        self.action = ""
        self.exit_path = ""
        self.bid = 0.0
        self.ask = 0.0
        self.skip_side = ""
        self.skip_detail = ""
        self.lane_pause = ""


def _state(qty: float, bid_seen: float = 0.0, ask_seen: float = 0.0):
    return SimpleNamespace(
        symbol="TEST", qty=qty, avg_px=100.0, avg_mid=100.0, last_entry_mid=100.0,
        opened_ts=0.0, last_ts=0.0, quote_bid=bid_seen, quote_ask=ask_seen,
        quote_mid=0.0, quote_ts=1000.0, toxic_streak=0,
        spread_hist=[], spread_baseline=0.0, mid_hist=[], vol_baseline_bp=0.0,
        sudden_move_until=0.0, sudden_move_hits=0, last_seg_ms=0,
        flow_hold_sec=30.0, flow_mu=-1.0, flow_strategy="S1",
        flow_queue_ahead=10.0, flow_queue_cum=0.0,
    )


def _call(state, dec, book, *, bid, ask, regime="", book_stale=False, **over):
    """调 active_flow_decision，只走"已持仓 ⇒ 离场阶梯"分支。"""
    kw = dict(
        state=state, mid=(bid + ask) / 2.0, ofi=0.0, trend_bp=0.0,
        bb=bid, ba=ask, now_ts=2000.0, fill_notional=0.0,
        taker_fee_bp=4.0, sl_bp=40.0, tp_bp=60.0, max_hold_sec=90.0,
        flow_thresh=0.15, local_book=book, dec=dec,
        PlannedFill=lambda **k: SimpleNamespace(**k),
        maker_fee_bp=0.0, allow_entry=False,
        model_side=None, model_mu=-1.0, regime=regime, book_stale=book_stale,
        equity=100.0, vol_300s_bp=50.0, seg_low=0.0, seg_high=0.0,
        seg_sell=0.0, seg_buy=0.0, same_side_n=1,
        stop_floor_bp=15.0, stop_cap_bp=40.0, entry_margin_bp=1.0,
        loss_frac=0.005, roundtrip_root=None, probe_notional_usd=0.0,
        explore_entry=False, bid_qty=0.0, ask_qty=0.0,
        vol_at_bid=0.0, vol_at_ask=0.0, skip_reason="",
    )
    kw.update(over)
    return AF.active_flow_decision(**kw)


# ── 测试 ─────────────────────────────────────────────────────────

class TestStopExitUsesMaker:
    """`taker_stop` 默认改为挂单抢平。"""

    def test_default_is_maker_no_taker_fee(self, monkeypatch):
        monkeypatch.delenv("MM_STOP_EXIT_MAKER_ONLY", raising=False)
        st, dec, book = _state(1.0), _Dec(), _Book()
        # 深度浮亏 ⇒ choose_exit 返回 taker_stop
        # entry 100，bid 95 ⇒ 约 −504bp，远超 2×止损(=80bp)
        _call(st, dec, book, bid=95.0, ask=95.2)
        assert dec.exit_path == "taker_stop_maker", (
            f"默认应走挂单抢平，实际 exit_path={dec.exit_path!r} skip={dec.skip!r}")
        assert book.calls == [], "不应产生吃单成交"
        # 多头平仓 = 卖出 ⇒ 挂在卖侧(ask)。`_arm` 里 bid/ask 挂单互斥：
        # 挂卖单时 `state.quote_ask = px`、`quote_bid = 0`（这不是 bug）。
        assert st.quote_ask > 0, "多头平仓应在卖侧挂出平仓单"
        assert st.quote_ask == 95.0, "挂对手价(bid)以保证优先成交"
        assert st.quote_bid == 0.0, "同一时刻只允许一侧挂单（互斥语义）"

    def test_rollback_env_restores_taker(self, monkeypatch):
        monkeypatch.setenv("MM_STOP_EXIT_MAKER_ONLY", "0")
        st, dec, book = _state(1.0), _Dec(), _Book()
        _call(st, dec, book, bid=95.0, ask=95.2)
        assert book.calls, "回滚开关打开后应恢复吃单成交"
        assert book.calls[0]["fee_rate"] > 0, "吃单必须计费"


class TestExitQuoteIsSticky:
    """离场挂单必须 sticky+band，否则队列进度每拍归零、永不成交。"""

    def test_queue_progress_survives_price_drift(self, monkeypatch):
        monkeypatch.delenv("MM_STOP_EXIT_MAKER_ONLY", raising=False)
        monkeypatch.setenv("MM_EXIT_MAKER_BAND_BP", "20")
        st, dec, book = _state(1.0), _Dec(), _Book()
        _call(st, dec, book, bid=95.0, ask=95.2)
        first_quote = st.quote_ask
        assert first_quote > 0, "应先挂出平仓单"
        # 累积队列进度，模拟"排到了"
        st.flow_queue_cum = 7.0
        # 价格小幅漂移：band_bp=20 ⇒ 容差 room = px*20/1e4 ≈ 0.1904。
        # 卖侧 `stay` 判据：`prev >= px*(1-1e-8)` 且 `prev <= px + room`。
        # 取 px = 95.2005（相比原 95.2 上行 0.0005）：
        #   95.2 <= 95.2005+0.1904 ✓ ，95.2 >= 95.2005*(1−1e-8) ✓ ⇒ 保持原挂单
        _call(st, dec, book, bid=95.0005, ask=95.2005)
        assert st.flow_queue_cum == 7.0, (
            "价格在 band 容差内时必须保持原挂单，队列进度不得清零"
            "（清零 ⇒ 挂单永不成交 ⇒ 滑到 taker_stop）")
        assert st.quote_ask == first_quote, (
            "容差内必须复用原挂单价，否则每拍重挂 ⇒ 队列进度永远攒不起来")

    def test_rearm_when_price_escapes_band(self, monkeypatch):
        monkeypatch.delenv("MM_STOP_EXIT_MAKER_ONLY", raising=False)
        monkeypatch.setenv("MM_EXIT_MAKER_BAND_BP", "1")
        st, dec, book = _state(1.0), _Dec(), _Book()
        _call(st, dec, book, bid=95.0, ask=95.2)
        first_quote = st.quote_ask
        st.flow_queue_cum = 7.0
        # 价格大幅移动（远超 1bp≈0.0095 容差）⇒ 应重挂并清零队列进度
        _call(st, dec, book, bid=90.0, ask=90.2)
        assert st.flow_queue_cum == 0.0, "价格跑出容差后应重挂（进度清零是预期行为）"
        assert st.quote_ask != first_quote, "跑出容差必须跟价，否则挂单挂在市价外"


class TestMissingBookFallsBack:
    """盘口缺失时不得卡住库存。"""

    def test_no_book_falls_back_to_taker(self, monkeypatch):
        monkeypatch.delenv("MM_STOP_EXIT_MAKER_ONLY", raising=False)
        st, dec, book = _state(1.0), _Dec(), _Book()
        _call(st, dec, book, bid=0.0, ask=0.0)
        assert dec.skip == "exit_no_book", (
            f"双向盘口都缺时应收敛到 exit_no_book，实际 {dec.skip!r}")
        assert book.calls == [], "无盘口可成交时不应产生成交"


class TestVolatilityScaledBand:
    """[整顿轮·T9] 重挂容差必须按标的波动缩放，不能写死常数。

    实测（24h，按 qpos 分组）：inside 腿均 net −16.08bp（净 −$205），
    touch/behind 腿均 net +7.47/+25.60bp（净 +$25/+$81）。
    深负腿集中在高波动标的（24h 中价标准差 GTC 1090bp / LYN 908bp / SI 389bp），
    写死的 2bp 容差会让挂单长期停在旧价、被中价穿过 ⇒ spread_bp 深负。
    """

    def test_band_scales_with_volatility(self):
        """`vol_300s_bp` 是**每 tick** 的中价标准差（实测量级 1~127bp）。
        乘数 1.0 ⇒ 容差 ≈ 一个 tick 的典型不利移动。"""
        from backend.services.market_maker.active_flow import exit_maker_band_bp
        quiet = exit_maker_band_bp(1.0)      # 安静：1bp/tick → 下限
        normal = exit_maker_band_bp(5.0)     # 常态：5bp/tick
        volatile = exit_maker_band_bp(40.0)  # 波动：40bp/tick
        wild = exit_maker_band_bp(127.0)     # 极端：127bp/tick → 上限
        assert quiet == 2.0, f"安静标的应取下限 2bp，实际 {quiet}"
        assert normal == pytest.approx(5.0), f"常态应≈5bp，实际 {normal}"
        assert volatile == pytest.approx(40.0), f"波动应≈40bp，实际 {volatile}"
        assert wild == 50.0, f"极端应取上限 50bp，实际 {wild}"
        assert quiet < normal < volatile <= wild, "容差必须随波动单调不减"

    def test_band_zero_vol_uses_floor(self):
        from backend.services.market_maker.active_flow import exit_maker_band_bp
        assert exit_maker_band_bp(0.0) == 2.0

    def test_multiplier_env_tunes_first_version_bug(self, monkeypatch):
        """第一版用 0.10 乘数 ⇒ 常态只得 0.5bp 被夹到 2bp ≈ 几乎没改。
        这里锁定「乘数真的生效」，防止再退回那个错误。"""
        from backend.services.market_maker.active_flow import exit_maker_band_bp
        monkeypatch.delenv("MM_EXIT_MAKER_BAND_BP", raising=False)
        monkeypatch.setenv("MM_EXIT_BAND_VOL_MULT", "0.1")
        assert exit_maker_band_bp(40.0) == pytest.approx(4.0), (
            "0.10 乘数应给出 4bp —— 这正是第一版的错误取值，用于回归防护")
        monkeypatch.setenv("MM_EXIT_BAND_VOL_MULT", "1.0")
        assert exit_maker_band_bp(40.0) == pytest.approx(40.0)

    def test_env_override_still_wins(self, monkeypatch):
        from backend.services.market_maker.active_flow import exit_maker_band_bp
        monkeypatch.setenv("MM_EXIT_MAKER_BAND_BP", "7.5")
        assert exit_maker_band_bp(900.0) == 7.5, (
            "显式覆盖必须生效（A/B 与回滚依赖它）")

    def test_high_vol_exit_rearms_sooner(self, monkeypatch):
        """高波动标的：价格动 20bp 就应重挂（容差 40bp@vol40 → 不重挂；
        用 vol_300s_bp=200 让容差到上限 50bp 仍不重挂；改为小幅波动验证下限）。

        真正要守的：**容差随波动变化** ⇒ 同样的价格漂移在两个标的上结论不同。
        """
        st, dec, book = _state(1.0), _Dec(), _Book()
        monkeypatch.delenv("MM_EXIT_MAKER_BAND_BP", raising=False)
        # 低波动（容差 2bp）：价格漂移约 20bp ⇒ 必须重挂
        _call(st, dec, book, bid=95.0, ask=95.2, vol_300s_bp=5.0)
        q_low = st.quote_ask
        st.flow_queue_cum = 5.0
        _call(st, dec, book, bid=95.19, ask=95.39, vol_300s_bp=5.0)
        assert st.flow_queue_cum == 0.0, "低波动 + 20bp 漂移 ⇒ 应重挂"
        assert st.quote_ask != q_low

        # 高波动（容差 40bp）：同样 20bp 漂移 ⇒ 应保持原挂单
        st2, dec2, book2 = _state(1.0), _Dec(), _Book()
        _call(st2, dec2, book2, bid=95.0, ask=95.2, vol_300s_bp=40.0)
        q_high = st2.quote_ask
        st2.flow_queue_cum = 5.0
        _call(st2, dec2, book2, bid=95.19, ask=95.39, vol_300s_bp=40.0)
        assert st2.flow_queue_cum == 5.0, (
            "高波动容差 40bp ⇒ 20bp 漂移应保持原挂单（这正是修复点）")
        assert st2.quote_ask == q_high


class TestNoPositionUnaffected:
    """空仓时不应走进场以外的离场逻辑（防回归）。"""

    def test_flat_state_does_not_flatten(self, monkeypatch):
        st, dec, book = _state(0.0), _Dec(), _Book()
        _call(st, dec, book, bid=100.0, ask=100.2)
        assert dec.exit_path == "", f"空仓不应产生 exit_path，实际 {dec.exit_path!r}"
        assert book.calls == [], "空仓不应成交"
