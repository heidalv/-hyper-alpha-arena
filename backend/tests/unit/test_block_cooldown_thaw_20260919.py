# -*- coding: utf-8 -*-
"""轮115 同因拦截冷却的「解冻」语义（2026-09-19）。

## 用户反馈

「冷却是冷却，但是设计的冷却好像从来都没有解冻过啊。只冻不解，并且单交易对冷却，
但是弄个全局冻结不解冻。」

## 现场证据（`reports/_probe115b.txt`，从 `[BlockCooldown]` 日志逐行提取）

| 键 | 各次装配时打印的连续计数 | 相邻装配间隔 | 冻结估算 |
|---|---|---|---|
| `ASTER:mid` | **5 → 6 → 7**（从不回落） | 45.3 / 31.8 分钟 | 90 分钟 |
| `BNB:long` | 5 → … → **25** | — | ≈ 10.5 小时 |
| `SOL:long` | 5 → … → **21** | — | ≈ 8.5 小时 |
| `ASTER:long` | 5 → … → **16** | — | ≈ 6 小时 |

机制（`_record_proposal_block` 旧实现）：计数到 5 就装配 30 分钟冷却，**但不归零** ⇒
窗口一过、只要再一次同因拦截（`count` 变 6 ≥ 5）就**立刻重新装配** ——
等效「永远冻结、每个窗口只放行一次尝试」。12 个 (symbol,tier) 计数 ≥5，
合起来覆盖整个可交易集合 ⇒ 看起来就是「全局冻结」。

## 本文件钉住的三件事

1. **装配即归零**：解冻后重新拥有完整预算（不是"一次尝试就再冻"）。
2. **成功即解冻**：`clear_proposal_block` 由 `evaluate_and_execute_proposal` 在成功时调用
   （此前**没有任何成功路径**清状态）。
3. **确定性拒绝不冷却**：`long/short_template_source_block` 只取决于"策略前缀 + 车道 + 配置"，
   重试不可能成功（轮37/轮40 的策略决定）。
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.full_auto_trading_service import FullAutoTradingService as _SVC  # noqa: E402


def _stub(tmp_path=None, *, skip_codes=None):
    """不跑 __init__ 的裸实例：只装 `_record_proposal_block` 需要的属性。"""
    s = _SVC.__new__(_SVC)
    s._proposal_block_streaks = {}
    s._proposal_block_cooldowns = {}
    s._block_skip_logged = {}
    if tmp_path is not None:
        s._BLOCK_STREAK_FILE = str(tmp_path)
        _orig_save = _SVC._block_streaks_save
        s._block_streaks_save = lambda: _orig_save(s)   # 绑定到 stub
    else:
        s._block_streaks_save = lambda: None            # 不落盘
    if skip_codes is not None:
        s._BLOCK_COOLDOWN_SKIP_CODES = skip_codes
    return s


# ══════════════════════════════════════════════════════════════════════
# ① 装配即归零：这是「只冻不解」的直接解药
# ══════════════════════════════════════════════════════════════════════

def test_count_resets_when_cooldown_is_armed():
    s = _stub()
    for i in range(4):
        assert s._record_proposal_block("ASTER", "mid", "v5gate") is False, i
    assert s._proposal_block_streaks["ASTER:mid"]["count"] == 4
    assert s._record_proposal_block("ASTER", "mid", "v5gate") is True, "第 5 次必须装配冷却"
    assert s._proposal_block_streaks["ASTER:mid"]["count"] == 0, \
        "装配后计数必须归零（旧实现停在 5 ⇒ 解冻后一次尝试就再冻 30 分钟）"


def test_second_window_needs_a_full_budget():
    """核心回归：窗口过期后**一次**尝试不得立刻重新装配（旧实现会）。"""
    s = _stub()
    for _ in range(5):
        s._record_proposal_block("ASTER", "mid", "size_below_floor")
    assert "ASTER:mid" in s._proposal_block_cooldowns
    # 冷却过期
    s._proposal_block_cooldowns["ASTER:mid"]["until"] = time.time() - 1
    assert s._proposal_block_cooldown_active("ASTER", "mid") is None, "过期即放行"
    # 解冻后第 1 次同因拦截：不得立刻再冻
    assert s._record_proposal_block("ASTER", "mid", "size_below_floor") is False, \
        "解冻后重新拥有完整预算 ⇒ 第 1 次不装甲"
    for _ in range(3):
        assert s._record_proposal_block("ASTER", "mid", "size_below_floor") is False
    assert s._record_proposal_block("ASTER", "mid", "size_below_floor") is True, "第 5 次才再装配"


def test_old_ratchet_is_reproducible_by_rollback_flag(monkeypatch):
    """回滚开关必须真的回到旧行为（对照实验，证明差异来自本改动）。"""
    import backend.config.settings as S
    monkeypatch.setattr(S, "PROPOSAL_BLOCK_COOLDOWN_RESET_ON_ARM", False)
    s = _stub()
    for _ in range(5):
        s._record_proposal_block("ASTER", "mid", "v5gate")
    assert s._proposal_block_streaks["ASTER:mid"]["count"] == 5, "旧行为：计数停在 5"
    s._proposal_block_cooldowns["ASTER:mid"]["until"] = time.time() - 1
    s._proposal_block_cooldown_active("ASTER", "mid")
    assert s._record_proposal_block("ASTER", "mid", "v5gate") is True, \
        "旧行为：解冻后一次尝试立刻重新冻结（这正是用户看到的『只冻不解』）"


# ══════════════════════════════════════════════════════════════════════
# ② 确定性拒绝不冷却
# ══════════════════════════════════════════════════════════════════════

def test_deterministic_reject_never_arms():
    s = _stub()
    for i in range(30):
        assert s._record_proposal_block("BNB", "long", "long_template_source_block") is False, i
    assert "BNB:long" not in s._proposal_block_cooldowns, "配置类拒绝不该冷却"
    assert "BNB:long" not in s._proposal_block_streaks


def test_deterministic_list_covers_both_template_codes():
    assert "long_template_source_block" in _SVC._BLOCK_COOLDOWN_SKIP_CODES
    assert "short_template_source_block" in _SVC._BLOCK_COOLDOWN_SKIP_CODES


def test_skip_list_empty_restores_old_behaviour(monkeypatch):
    s = _stub(skip_codes=())
    for _ in range(5):
        s._record_proposal_block("BNB", "long", "long_template_source_block")
    assert "BNB:long" in s._proposal_block_cooldowns


def test_transient_reasons_still_arm(monkeypatch):
    """WLFI 类瞬态回环仍必须被冷却（本改动不能把当初的修复弄回去）。"""
    s = _stub()
    for _ in range(5):
        got = s._record_proposal_block("WLFI", "mid", "decision_price_stale")
    assert got is True and "WLFI:mid" in s._proposal_block_cooldowns


# ══════════════════════════════════════════════════════════════════════
# ③ 过期清理 + 成功解冻 + 全局读数
# ══════════════════════════════════════════════════════════════════════

def test_expired_state_is_pruned_on_save(tmp_path):
    p = tmp_path / "streaks.json"
    p.write_text(json.dumps({
        "streaks": {"OLD:mid": {"code": "v5gate", "count": 9, "ts": time.time() - 5 * 3600},
                    "NEW:mid": {"code": "v5gate", "count": 2, "ts": time.time()}},
        "cooldowns": {"OLD:mid": {"code": "v5gate", "until": time.time() - 3600},
                      "NEW:mid": {"code": "v5gate", "until": time.time() + 600}},
    }, ensure_ascii=False), encoding="utf-8")
    s = _stub(p)
    s._block_streaks_load()
    s._block_streaks_save()
    out = json.loads(p.read_text(encoding="utf-8"))
    assert "OLD:mid" not in out["cooldowns"], "过期冷却必须清掉（现场 12 条里 10 条是残留）"
    assert "OLD:mid" not in out["streaks"], "超 2h 未更新的计数必须清掉"
    assert "NEW:mid" in out["cooldowns"] and "NEW:mid" in out["streaks"]


def test_expiry_logs_release(capsys):
    s = _stub()
    s._proposal_block_cooldowns["ASTER:mid"] = {"code": "size_below_floor", "until": time.time() - 1}
    assert s._proposal_block_cooldown_active("ASTER", "mid") is None
    # 记录器不一定往 stdout 走，这里只要求状态被清掉且接口返回 None
    assert "ASTER:mid" not in s._proposal_block_cooldowns


def test_success_clears_state():
    s = _stub()
    s._proposal_block_streaks["ASTER:mid"] = {"code": "size_below_floor", "count": 3, "ts": time.time()}
    s._proposal_block_cooldowns["ASTER:mid"] = {"code": "size_below_floor", "until": time.time() + 600}
    assert s._clear_proposal_block("ASTER", "mid") is True
    assert "ASTER:mid" not in s._proposal_block_streaks
    assert "ASTER:mid" not in s._proposal_block_cooldowns
    assert s._clear_proposal_block("ASTER", "mid") is False, "已清空则返回 False"


def test_success_path_actually_calls_clear():
    """接线验证：内层成功 ⇒ `clear_proposal_block` 必须被调用。"""
    from backend.services.full_auto import proposal_execution as PE

    calls = []

    class _Host:
        block_cooldown_active = staticmethod(lambda s, t: None)
        record_proposal_block = staticmethod(lambda *a: None)
        clear_proposal_block = staticmethod(lambda s, t: calls.append((s, t)))

    class _P:
        symbol = "ASTER"
        tier = "mid"

    _orig = PE._evaluate_and_execute_proposal_inner
    PE._evaluate_and_execute_proposal_inner = lambda **_kw: True
    try:
        ok = PE.evaluate_and_execute_proposal(
            db=None, session=None, proposal=_P(), market_summary={}, host=_Host())
    finally:
        PE._evaluate_and_execute_proposal_inner = _orig
    assert ok is True
    assert calls == [("ASTER", "mid")], calls


def test_host_wiring_is_explicit():
    src = io.open(os.path.join(_ROOT, "backend/services/full_auto/proposal_execution.py"),
                  encoding="utf-8").read()
    assert "clear_proposal_block=svc._clear_proposal_block" in src, \
        "真实服务必须把成功解冻接到 host 上（否则只是 dataclass 默认空函数）"


def test_cooldown_summary_reports_global_view():
    s = _stub()
    s._proposal_block_cooldowns = {
        "ASTER:mid": {"code": "size_below_floor", "until": time.time() + 600},
        "BNB:long": {"code": "long_template_source_block", "until": time.time() + 600},
        "ZEC:mid": {"code": "v5gate", "until": time.time() - 60},
    }
    txt = s._cooldown_summary()
    assert "2 个" in txt and "ASTER:mid" in txt and "BNB:long" in txt, txt
    assert "ZEC" not in txt, "过期的不该算进全局读数"


# ══════════════════════════════════════════════════════════════════════
# ④ 开关登记
# ══════════════════════════════════════════════════════════════════════

def test_flag_is_registered_and_documented():
    from backend.config.env_registry import KNOWN_FLAGS
    assert "PROPOSAL_BLOCK_COOLDOWN_RESET_ON_ARM" in KNOWN_FLAGS
    src = io.open(os.path.join(_ROOT, "backend/config/settings.py"), encoding="utf-8").read()
    i = src.index("PROPOSAL_BLOCK_COOLDOWN_RESET_ON_ARM")
    assert "5→6→7" in src[i - 900:i + 600], "开关旁边必须留下现场证据（5→6→7 棘轮）"
