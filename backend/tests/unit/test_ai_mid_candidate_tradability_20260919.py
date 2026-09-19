# -*- coding: utf-8 -*-
"""轮118：AI 中线候选的**可交易性过滤**（ZEC/AVAX/SYN 无策略却占名额）。

现场（`reports/_probe118h.txt`）：扫描宇宙 12 个币 = 固定 9 + AI 3（AVAX/SYN/ZEC），
**AI 那三个全都没有 mid 独立策略**；其中 ZEC 是唯一 `recommend_open=1` 的币 ⇒
每 3 分钟进执行层 → `strategy_detached` → 30 分钟冷却（实测 24 轮 `候选=1 成交=0`），
表现出来就是"中线整条车道冻住"。

修：候选返回前按「在会话标的内（执行层会自动建策略）**或**已有 active mid 策略」过滤；
查询异常 fail-open（候选过滤不是风控闸）。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


def test_helper_exists_and_is_wired():
    """[轮123 更新] 过滤仍在，但**默认关闭**（轮118 的判据过紧，把整条 AI 车道清零）。"""
    src = _src("backend/services/auto_coin_selector.py")
    assert "def _filter_tradable_mid_candidates(" in src
    assert "_filter_tradable_mid_candidates(db, session_id, picked)" in src, "仍可在开关下启用"
    i = src.index("轮123 2026-09-19 改为默认关闭")
    seg = src[i:i + 1500]
    assert 'MIDLONG_AI_CANDIDATE_TRADABLE_FILTER", "false"' in seg, "默认必须是 false"
    assert "扫描侧" in seg, "必须写明保护改由扫描侧承担（补建 + active + 可绑定）"
    assert "候选=0" in seg or "picked=['SYN','ZEC','DOGE'] → []" in seg, "留下现场证据"


def test_filter_keeps_session_symbols_and_strategy_symbols(tmp_path):
    from backend.services.auto_coin_selector import _filter_tradable_mid_candidates

    class _Row:
        def __init__(self, v):
            self._v = v

        def __getitem__(self, i):
            return self._v

    class _Db:
        def execute(self, stmt, params=None):
            s = str(stmt)
            if "full_auto_sessions" in s:
                return type("R", (), {"fetchone": staticmethod(
                    lambda: _Row(["BTC", "ASTER", "UNI"]))})()
            return type("R", (), {"fetchall": staticmethod(
                lambda: [("BNB",), ("ZEC",)])})()

    out = _filter_tradable_mid_candidates(_Db(), "fa_x", ["BTC", "ZEC", "AVAX", "BNB"])
    assert out == ["BTC", "ZEC", "BNB"], out


def test_filter_fails_open_on_db_error():
    from backend.services.auto_coin_selector import _filter_tradable_mid_candidates

    class _Bad:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert _filter_tradable_mid_candidates(_Bad(), "fa_x", ["AVAX"]) == ["AVAX"]


def test_sweep_side_also_skips_zombies():
    """两道防线：即使候选漏进来，扫描入口也会跳过（轮118 第一处修复）。"""
    src = _src("backend/services/mlto/brain.py")
    assert "sweep_skip:no_strategy" in src
