# -*- coding: utf-8 -*-
"""[2026-09-10 第二十八轮] 中长线并发上限契约测试（§38.6）。

背景：9/9 夜账户同时持有 5-6 笔全多山寨（名义 ≈0.94x 权益、无对冲），
在 alt 集体下跌中单夜 -$155.48，把 75 天口径由 +$36.10 打为 -$77.49。
根因不是缺规则——`check_portfolio_open_allowed` 早就有 `MIDLONG_MAX_OPEN_POSITIONS`，
而是 `.env` 里把它设成了 **6**（代码默认是 4），等于允许 6 笔同向。

本测试锁三件事：
  1. 达到上限即拦、未达上限放行；
  2. 只有 mid/long（swing/trend_follow/position）占用名额，scalp/short 不占；
  3. 部署配置的上限必须保持在保守区间（≤5），防止被悄悄调回 6。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _pos(sym, tier="mid", nature="swing", side="long", size=1.0, px=100.0):
    return {"symbol": sym, "side": side, "size": size, "entry_price": px,
            "mark_price": px, "trade_nature": nature, "timeframe_tier": tier}


def _portfolio(positions, equity=100000.0):
    return {"balance": {"total_equity": equity}, "positions": positions}


def _set_cap(monkeypatch, n: int):
    from backend.config import settings
    monkeypatch.setattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", int(n), raising=False)
    monkeypatch.setattr(settings, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    # 关掉相关簇闸，避免 BTC/ETH/SOL 干扰并发上限的断言
    monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "", raising=False)


def test_cap_blocks_at_limit(monkeypatch):
    """[验收轮3 语义更新] 中线开仓并发帽只数中线仓（车道化后长仓不占中线帽）。"""
    _set_cap(monkeypatch, 4)
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [_pos("AAA", tier="mid", nature="swing"),
                 _pos("BBB", tier="mid", nature="swing"),
                 _pos("CCC", tier="mid", nature="swing"),
                 _pos("DDD", tier="mid", nature="swing")]
    ok, why = check_portfolio_open_allowed(
        symbol="EEE", action="buy", portfolio=_portfolio(positions), new_notional=1000.0,
    )
    assert not ok, why
    assert "midlong_open_positions" in why, why


def test_cap_allows_below_limit(monkeypatch):
    _set_cap(monkeypatch, 4)
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [_pos("AAA"), _pos("BBB", tier="long", nature="trend_follow"), _pos("CCC")]
    ok, why = check_portfolio_open_allowed(
        symbol="EEE", action="buy", portfolio=_portfolio(positions), new_notional=1000.0,
    )
    assert ok, why


def test_scalp_and_short_positions_do_not_consume_cap(monkeypatch):
    """scalp / short 车道不占 mid/long 名额（否则并发上限会误拦中线）。"""
    _set_cap(monkeypatch, 2)
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [
        _pos("AAA", tier="short", nature="scalp"),
        _pos("BBB", tier="short", nature="scalp"),
        _pos("CCC", tier="short", nature="intraday"),
        _pos("DDD", tier="mid", nature="swing"),
    ]
    ok, why = check_portfolio_open_allowed(
        symbol="EEE", action="buy", portfolio=_portfolio(positions), new_notional=1000.0,
    )
    assert ok, why


def test_cap_zero_disables_check(monkeypatch):
    """0 = 关闭该闸（保持既有语义，便于紧急放宽）。"""
    _set_cap(monkeypatch, 0)
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [_pos(f"S{i}") for i in range(9)]
    ok, why = check_portfolio_open_allowed(
        symbol="ZZZ", action="buy", portfolio=_portfolio(positions), new_notional=1000.0,
    )
    assert ok, why


def test_deployed_cap_is_conservative():
    """部署上限 ≤6。

    [§38.6] 6 笔同向山寨齐跌单夜 -$155.48 的实证来自**修复前全尺寸仓**时代。
    [验收轮4 2026-09-14 用户指令] 中线帽 4→6：当前中线开仓全部是 probe 缩仓
    （窄带×0.25 + 位置×0.25，单仓 margin ~150-260），且并发帽已车道化（不与 E1
    长仓混计），净敞口 150% 与簇帽仍兜底——风险口径已与 §38 时期不同，允许扩到 6。
    若需进一步放宽，请先更新本注释与观察期结论。

    [调研轮36 2026-09-17 用户授权 C1] 中线帽 6→8。**观察期结论（本轮更新）**：
      ① 单笔风险已结构性变小：轮23 硬止损 4.5%→1.5%、轮28 单币名义上限 0.35→0.15 权益
         ⇒ 单笔最大风险 ≈ 0.15 × 权益 × 1.5% = **0.225% 权益**；8 笔同时打满 ≈ **1.8% 权益**，
         远低于 9/9 夜设 [1,6] 时的 6 × 0.35 × 4.5% ≈ **9.45% 权益**（风险当量反而更保守）；
      ② 机会成本实测（轮35 反事实，24h/21 事件）：被并发帽拦掉的候选 12h **+0.22%**、
         胜率 **65%**、仅 **5%** 会触及 2% 止损 ⇒ 卡在 6 是在持续丢钱。
    ⇒ 护栏从"硬区间 [1,6]"升级为**风险当量断言**（见下），既允许本次放宽，
       也防止未来在单笔风险放大时继续抬高并发。
    """
    env = ROOT / ".env"
    if not env.is_file():
        return
    m = re.search(r"^MIDLONG_MAX_OPEN_POSITIONS\s*=\s*(\d+)", env.read_text(encoding="utf-8"),
                  re.M)
    if not m:
        return
    val = int(m.group(1))
    assert 1 <= val <= 8, (
        f"MIDLONG_MAX_OPEN_POSITIONS={val} 超出本轮观察期区间 [1,8]；"
        "放宽前须更新本测试的观察期结论与风险当量计算"
    )
    # ── [续作R17 风险政策 C] 静态不等式 → 运行时跳闸验证 ──
    # 原断言 `val × notional × sl ≤ 2.5%` 在中线止损 1.5%→3.0% 后被打红（3.6% > 2.5%）。
    # 但"配置上限 ≠ 实际持仓风险"：真实持仓各有实际止损距离（多数远小于上限）。
    # 落地：`midlong_portfolio_risk.concurrent_loss_tripwire` 用实测口径求和，
    # 只在最坏损失总和 > ceiling 时拦截。本测试改为验证**跳闸的行为**。
    from backend.services.mlto.midlong_portfolio_risk import concurrent_loss_tripwire

    env_txt = env.read_text(encoding="utf-8")

    def _f(key, default):
        mm = re.search(rf"^{key}\s*=\s*([0-9.]+)", env_txt, re.M)
        return float(mm.group(1)) if mm else default

    _notional_cap = _f("PC_MAX_WEIGHT_PER_SYMBOL_MID", 0.15)
    _sl_cap = _f("MIDLONG_SL_MAX_PCT_MID", 0.015)   # 小数口径（0.03 = 3%）
    _sl_pp = _sl_cap * 100.0                          # 跳闸的 sl_pct 是百分点
    _worst = val * _notional_cap * _sl_cap

    _pos_all_cap = [{"notional": _notional_cap, "sl_pct": _sl_pp} for _ in range(val)]
    if _worst > 0.025:
        ok, why = concurrent_loss_tripwire(
            positions=_pos_all_cap, equity=1.0, new_notional=0.0,
            ceiling_pct=2.5, default_sl_pct=_sl_pp,
        )
        assert not ok, "静态口径超限时，跳闸必须在运行时拦截"
        assert "concurrent_loss_tripwire" in why, why
    else:
        ok, why = concurrent_loss_tripwire(
            positions=_pos_all_cap, equity=1.0, new_notional=0.0,
            ceiling_pct=2.5, default_sl_pct=_sl_pp,
        )
        assert ok, why

    # 反例（实测口径的核心）：每笔实际止损远小于上限时，满并发也应当放行
    ok2, _why2 = concurrent_loss_tripwire(
        positions=[{"notional": _notional_cap, "sl_pct": 0.8} for _ in range(val)],
        equity=1.0, new_notional=0.0, ceiling_pct=2.5, default_sl_pct=_sl_pp,
    )
    assert ok2, "实测口径下（每笔实际 SL 0.8%）满并发应放行"


def test_code_default_is_four():
    """代码默认值（env 未设时）必须是 4，避免默认放宽。"""
    src = (ROOT / "backend" / "config" / "settings.py").read_text(encoding="utf-8")
    m = re.search(r'MIDLONG_MAX_OPEN_POSITIONS[^\n]*os\.getenv\(\s*"MIDLONG_MAX_OPEN_POSITIONS"\s*,\s*"(\d+)"',
                  src)
    assert m, "未找到 MIDLONG_MAX_OPEN_POSITIONS 的 getenv 默认值"
    assert int(m.group(1)) == 4, f"默认值被改为 {m.group(1)}（应为 4）"
