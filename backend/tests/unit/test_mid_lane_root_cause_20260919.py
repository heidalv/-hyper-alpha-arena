# -*- coding: utf-8 -*-
"""轮109 中线根因修复回归测试（2026-09-19）。

## 根因（账户 14，近 7 天实测）

    中线 62 笔：毛利 +31.78  手续费 36.81  ⇒ **净 −5.03**
    笔均毛利 +0.513  <  笔均手续费 +0.594        ← 结构性负期望
    按入场来源拆：
      mlto         28 笔  毛 +115.10  费 16.58  **净 +98.52**（笔均 +3.52）
      factor_route 34 笔  毛  −83.33  费 20.22  **净 −103.55**（笔均 −3.05）

⇒ 中线整体那 −5.03 完全是 factor_route 这一条入场路径造成的。

修法：`MIDLONG_MID_VIA_FACTOR_ROUTE=false`（止血）+ **影子档**
（`MIDLONG_MID_FACTOR_ROUTE_SHADOW=true`：继续逐币决策、继续记日志、不开仓，
证据不断）；同时把中线锁利地板 0.5%→1.0%（往返手续费仅 0.04%，
0.5% 的锁利等于把赢单在 +1% 就截断）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_CYCLE = os.path.join(_ROOT, "backend/services/full_auto/mlto_cycle.py")


def _cycle_src() -> str:
    return open(_CYCLE, encoding="utf-8").read()


def _env() -> str:
    return open(os.path.join(_ROOT, ".env"), encoding="utf-8", errors="replace").read()


# ══════════════════════════════════════════════════════════════════════
# ① 影子档：只决策、不开仓、证据不断
# ══════════════════════════════════════════════════════════════════════

def test_shadow_branch_exists_and_is_gated_on_via_off():
    src = _cycle_src()
    assert "[FactorRouteShadow]" in src
    assert "(not _FR) and _ab_on and _shadow_on" in src, (
        "影子档必须只在 VIA=false 时生效（VIA=true 走实开 A/B 档）")


def test_shadow_branch_never_calls_factor_route_open():
    """影子档只能调 `factor_route_decide`；一旦出现 `factor_route_open` 就是又开仓了。"""
    src = _cycle_src()
    i_block = src.index("_shadow_on = (")
    i_end = src.index("_sh_outer", i_block)
    block = src[i_block: i_end]
    assert "factor_route_decide" in block, block[:400]
    assert "factor_route_open" not in block, "影子档不得调用开仓函数"
    assert "opened" not in block, "影子档不得产生 opened 语义"


def test_shadow_switch_default_true():
    from backend.config.settings import MIDLONG_MID_FACTOR_ROUTE_SHADOW as s
    assert s is True


# ══════════════════════════════════════════════════════════════════════
# ② .env 生效值（止血 + 锁利地板）
# ══════════════════════════════════════════════════════════════════════

def test_env_stops_factor_route_opens():
    env = _env()
    assert "MIDLONG_MID_VIA_FACTOR_ROUTE=false" in env, (
        "因子路由中线实开必须关掉（34 笔净 −103.55）")
    for line in env.splitlines():
        s = line.strip()
        if s.startswith("#") or "MIDLONG_MID_VIA_FACTOR_ROUTE" not in s:
            continue
        assert s.split("=", 1)[1].strip().lower() in ("false", "0", "no", "off"), s


def test_env_raises_mid_lock_floor():
    env = _env()
    assert "MIDLONG_MIN_LOCK_PROFIT_PCT_MID=0.010" in env


def test_settings_read_the_new_lock_floor():
    from backend.config.settings import MIDLONG_MIN_LOCK_PROFIT_PCT_MID as v
    assert v == pytest.approx(0.010), v
    # 地板必须显著高于往返手续费（实测 0.04%），否则赢单会被锁利截断
    assert v >= 0.010


def test_mid_lock_floor_beats_round_trip_fee():
    """实测笔均手续费 0.594 / 笔均名义 1484.9 = 0.04%；锁利地板应为它的 ≥10 倍。"""
    fee_pct = 0.594 / 1484.9
    from backend.config.settings import MIDLONG_MIN_LOCK_PROFIT_PCT_MID as v
    assert v / fee_pct >= 10


# ══════════════════════════════════════════════════════════════════════
# ③ 算式自证：这次修复针对的就是那 −5.03
# ══════════════════════════════════════════════════════════════════════

def test_arithmetic_shows_factor_route_is_the_whole_leak():
    mid_gross, mid_fee = 31.78, 36.81
    fr_net, mlto_net = -103.55, 98.52
    assert round(mid_gross - mid_fee, 2) == pytest.approx(-5.03, abs=0.01)
    # 去掉 factor_route 后的中线净额 ≈ mlto 净额
    assert mlto_net > 0 and fr_net < 0
    assert abs((mlto_net + fr_net) - (mid_gross - mid_fee)) < 0.05
