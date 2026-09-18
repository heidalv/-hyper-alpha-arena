"""P2-6 回归：AI 选币隔离闸门的文案/注释不得与 settings 实际取值自相矛盾。

事故背景（轮66 审计 P2-6）
-------------------------
`backend/services/sub_position_manager.py` 的「AI 自动选币强制隔离」闸门：

- 注释写「只允许短线(intraday)和中线(swing)」「不影响短线(intraday)/中线(swing)：
  AUTO_COIN_ALLOWED_NATURES 本就包含这两者」，
- 拒绝文案写「只允许短线/中线交易(允许: [...])」，
- 而 `backend/config/settings.py:3009` 的实际值是 `frozenset({"scalp"})`
  （同处注释解释了理由：中线已并入长线 mid_view，auto-coin 选出的币只进短线
  scalp，由 `scalp_loop.py` 独立线程处理）。

运行时因此输出「只允许短线/中线交易(允许: ['scalp']，当前: swing)」——
一句自己打自己的话。排障时无法判断是配置没生效还是代码没生效。

同一段里还有两条静默 fail-open：
- `:422` 库兜底查询 `except Exception: pass`
- `:433` 模块导入 `except ImportError: pass`

修法（轮91）：展示名改由 `lane_semantics` 真源推导；两条 fail-open 补 warning。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPM = ROOT / "backend" / "services" / "sub_position_manager.py"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


def _gate_block() -> str:
    """截出闸门所在的那段活代码（注释已剥离，故尾部锚点必须用代码而不是注释）。"""
    live = _strip_comments(_SPM.read_text(encoding="utf-8"))
    start = live.index("from backend.services.auto_coin_selector import is_auto_coin_symbol")
    tail_anchor = "from backend.services.fee_guard import fee_guard"
    end = live.index(tail_anchor, start)
    return live[max(0, start - 400):end]


# ── 文案正确性 ────────────────────────────────────────────────


def test_display_names_come_from_lane_semantics():
    from backend.config.settings import AUTO_COIN_ALLOWED_NATURES
    from backend.services.sub_position_manager import _nature_display_names

    got = _nature_display_names(set(AUTO_COIN_ALLOWED_NATURES))
    # 当前配置是 {"scalp"} → 车道真源给的展示名是「日内」
    assert got == ["日内(scalp)"], f"文案与真源不一致: {got}"


def test_display_names_never_invent_a_lane():
    """未登记的 nature 必须原样输出，不得编一个不存在的车道名。"""
    from backend.services.sub_position_manager import _nature_display_names

    assert _nature_display_names({"unknown_thing"}) == ["unknown_thing"]
    assert _nature_display_names(set()) == ["(空集合，即不允许任何 nature)"]
    mixed = _nature_display_names({"swing", "trend_follow"})
    assert mixed == ["日内(swing)", "长线趋势(trend_follow)"], mixed


def test_reason_text_is_self_consistent():
    """拒绝文案里的车道名必须与同一句里的 nature 列表一致（这是原缺陷的形态）。"""
    from backend.config.settings import AUTO_COIN_ALLOWED_NATURES
    from backend.services.sub_position_manager import _nature_display_names

    allowed = set(AUTO_COIN_ALLOWED_NATURES)
    names = _nature_display_names(allowed)
    # 原文案写死「短线/中线」，与 ['scalp'] 对不上 —— 断言这种组合不再可能出现
    if allowed == {"scalp"}:
        assert "短线/中线" not in "".join(names)
        assert "日内" in "".join(names)


# ── 源码级守卫 ────────────────────────────────────────────────


def test_no_hardcoded_lane_names_in_gate_comments():
    """闸门注释不得再声称允许 intraday/swing（settings 里没有）。"""
    src = _SPM.read_text(encoding="utf-8")
    block = src[src.index("3b. AI 自动选币强制隔离"): src.index("手续费+滑点门卫")]
    assert "AUTO_COIN_ALLOWED_NATURES 本就包含这两者" not in block, (
        "settings.AUTO_COIN_ALLOWED_NATURES 实际是 {'scalp'}，该说法不成立"
    )
    assert "只允许短线(intraday)和中线(swing)" not in block


def test_reason_no_longer_hardcodes_short_mid_labels():
    block = _gate_block()
    assert "只允许短线/中线交易" not in block, "硬编码车道名的文案必须删掉"
    assert "_nature_display_names(" in block, "文案必须由真源推导"


def test_fail_opens_are_now_visible():
    block = _gate_block()
    assert not re.search(r"except Exception:\s*\n\s*pass", block), (
        "库兜底查询不得再静默吞异常"
    )
    assert "except Exception as _db_err:" in block
    assert "_db_err" in block.split("logger.warning", 1)[1][:200]
    assert re.search(r"except ImportError as _imp_err:", block), (
        "ImportError 分支必须绑定变量以便记录"
    )
    assert "AI 选币隔离闸门不可用" in block, "模块不可用必须留下 warning"


def test_gate_direction_unchanged():
    """修复只动文案与可见性：判定方向（nature not in allowed ⇒ 拒绝）必须保持。"""
    block = _gate_block()
    assert "if nature not in _allowed:" in block
    assert "if _is_ai_coin:" in block
    assert re.search(
        r"return self\._verdict\(False, reason, [\"']open[\"'], symbol, nature", block
    ), "拒绝仍须经 _verdict 返回，未改成 continue/return None 之类"


def test_lane_semantics_is_the_single_source():
    """展示名不得在 sub_position_manager 里重新维护一张 nature→中文 的表。"""
    src = _strip_comments(_SPM.read_text(encoding="utf-8"))
    helper = src[src.index("def _nature_display_names"): src.index("class SubPositionManager")]
    assert "lane_semantics" in helper
    assert "get_spec" in helper and "lane_for_nature" in helper


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
