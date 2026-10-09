# -*- coding: utf-8 -*-
r"""[整顿轮·T17 2026-10-05] `maker_risk` 必须挂**被动侧**(pass)，不再挂对手价(touch)。

病根（实测，`lane_ledger`，T9 之后 `maker_risk` 全部腿）：

    ts        symbol     notional  spread_bp
    16:07:44  BTW            15.0     -64.42
    16:21:06  PLAY          988.5      +0.00
    16:48:28  PLAY          984.8      -5.19
    16:53:31  AAVE          977.8      -6.47
    17:13:22  LYN            15.1     -10.73
    17:27:05  MARSCOIN     1009.1     -15.87
    19:15:36  QNT          1656.0     -21.80   <- 单腿 -$3.60

⇒ 5/7 条成交在中价错误一侧，均 net_bp ≈ -17。

机理：挂 `bid`(touch) 时只有主动卖单打到 `bid` 才成交
⇒ **成交即代表行情在往下走**。
反证：那个 QNT 在 19:15:36 砍在 250.98/mid 250.435(-21.8bp)；
**34 秒后** 19:16:10 中价回到 250.9，同一仓位赚回 +$2.01。

修法：改挂 `ask`(pass) —— 需要行情**回升**才成交。
回滚：`MM_RISK_EXIT_PASSIVE=0` 恢复旧的 touch 行为。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.market_maker import active_flow as AF


@pytest.fixture(autouse=True)
def _pin_legacy_path(monkeypatch):
    """[2026-10-09] 本文件锁定的是**旧单边流**契约；ping-pong 默认开启后，
    旧体逐字保留在 `MM_PINGPONG=0` 之后，这里钉住回滚路径。"""
    monkeypatch.setenv("MM_PINGPONG", "0")


class _Book:
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
        self.fills = []; self.skip = ""; self.action = ""; self.exit_path = ""
        self.bid = 0.0; self.ask = 0.0; self.skip_side = ""; self.skip_detail = ""
        self.lane_pause = ""


def _state(qty):
    return SimpleNamespace(
        symbol="TEST", qty=qty, avg_px=100.0, avg_mid=100.0, last_entry_mid=100.0,
        opened_ts=0.0, last_ts=0.0, quote_bid=0.0, quote_ask=0.0, quote_mid=0.0,
        quote_ts=1000.0, toxic_streak=0, spread_hist=[], spread_baseline=0.0,
        mid_hist=[], vol_baseline_bp=0.0, sudden_move_until=0.0,
        sudden_move_hits=0, last_seg_ms=0, flow_hold_sec=30.0, flow_mu=-1.0,
        flow_strategy="S1", flow_queue_ahead=0.0, flow_queue_cum=0.0)


def _call(state, dec, book, *, bid, ask, **over):
    kw = dict(
        state=state, mid=(bid + ask) / 2.0, ofi=0.0, trend_bp=0.0, bb=bid, ba=ask,
        now_ts=2000.0, fill_notional=0.0, taker_fee_bp=4.0, sl_bp=40.0,
        tp_bp=60.0, max_hold_sec=90.0, flow_thresh=0.15, local_book=book,
        dec=dec, PlannedFill=lambda **k: SimpleNamespace(**k), maker_fee_bp=0.0,
        allow_entry=False, model_side=None, model_mu=-1.0, regime="",
        book_stale=False, equity=100.0, vol_300s_bp=50.0, seg_low=0.0,
        seg_high=0.0, seg_sell=0.0, seg_buy=0.0, same_side_n=1,
        stop_floor_bp=15.0, stop_cap_bp=40.0, entry_margin_bp=1.0,
        loss_frac=0.005, roundtrip_root=None, probe_notional_usd=0.0,
        explore_entry=False, bid_qty=0.0, ask_qty=0.0, vol_at_bid=0.0,
        vol_at_ask=0.0, skip_reason="")
    kw.update(over)
    return AF.active_flow_decision(**kw)


def _maker_risk_setup(book, *, bid=95.0):
    """构造走 `maker_risk` 的局面。

    `choose_exit` 的顺序（flow_rules.py）：
      no_book / taker_risk      ← 最先
      taker_stop                ← 浮亏 ≤ -stop*2
      maker_take                ← 浮盈
      maker_time                ← **`opened_ts <= 0` 时直接返回**  ← 必须给有效值
      maker_risk                ← 浮亏 ≤ -max(5, 0.5*stop)
    所以：① `opened_ts` 必须 > 0（否则被 maker_time 截走）
          ② 浮亏要落在 0.5*stop 与 2*stop 之间，才命中 maker_risk 而非 taker_stop
    entry=100, stop=clamp(2*50,15,40)=40 ⇒ 区间 -20bp ~ -80bp
    取 bid=99.5 ⇒ 浮亏 ≈ (99.5-100)/100*1e4 - 4 = -54bp ✓
    """
    st = _state(1.0)
    st.opened_ts = 1900.0        # now_ts=2000 ⇒ age=100s > MIN_HOLD_SEC
    return st, _Dec(), book


BID_RISK = 99.5      # 浮亏约 -54bp ⇒ maker_risk（而非 taker_stop）
ASK_RISK = 99.7


class TestRiskExitSide:
    def test_default_is_passive_side_for_long(self, monkeypatch):
        """多头平仓 ⇒ 必须挂卖侧(ask)，不是买侧(bid)。"""
        monkeypatch.delenv("MM_RISK_EXIT_PASSIVE", raising=False)
        st, dec, book = _maker_risk_setup(_Book())
        _call(st, dec, book, bid=BID_RISK, ask=ASK_RISK)
        assert dec.exit_path.startswith("maker_risk"), (
            f"应走 maker_risk，实际 {dec.exit_path!r}（skip={dec.skip!r}）")
        assert st.quote_ask > 0, "多头平仓必须在卖侧(ask)挂单（被动侧）"
        assert st.quote_ask > (BID_RISK + ASK_RISK) / 2.0, (
            f"pass 侧应挂在 mid 之上，实际 {st.quote_ask} vs mid "
            f"{(BID_RISK + ASK_RISK) / 2.0}")
        assert st.quote_bid == 0.0, "同一时刻只允许一侧挂单"

    def test_rollback_restores_touch(self, monkeypatch):
        monkeypatch.setenv("MM_RISK_EXIT_PASSIVE", "0")
        st, dec, book = _maker_risk_setup(_Book())
        _call(st, dec, book, bid=BID_RISK, ask=ASK_RISK)
        assert dec.exit_path.startswith("maker_risk"), f"实际 {dec.exit_path!r}"
        assert st.quote_ask == pytest.approx(BID_RISK), (
            f"回滚应挂对手价(bid={BID_RISK})，实际 {st.quote_ask}")

    def test_source_documents_switch(self):
        src = AF.__file__
        text = open(src, encoding="utf-8", errors="replace").read()
        assert "MM_RISK_EXIT_PASSIVE" in text, "必须提供回滚开关"
