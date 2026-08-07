"""
test_prompt_liberation — 阶段 3c+3d：LLM Prompt 全面重写验证

验证 AI 解放核心：
1. 新 prompt 不再含规则驱动痕迹（side_hint / min_score / "必须 hold" / "强制" / "等待优于开仓" / "代码强制"）
2. 新 prompt 含数据驱动 + 自由推理标记（"风险底线" / "自主决定" / 数据上下文变量）
3. task_trend_agent_review 的 JSON schema 保持稳定（短线兼容：action/reduce_ratio/trend_strength 仍必需）
4. qual_layer 内联 fallback 的 conviction_delta band 从 -8..8 扩展到 -20..+20
"""

import os
import re
import sys
from pathlib import Path

import pytest

# 解析 repo 根目录（测试从 repo 根通过 python -m pytest 调用）
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "backend" / "prompts"


# ──────────────────────────────────────────────────────────────
# 辅助
# ──────────────────────────────────────────────────────────────

def _read(rel_path: str) -> str:
    path = _PROMPTS_DIR / rel_path
    assert path.exists(), f"prompt 文件不存在: {path}"
    return path.read_text(encoding="utf-8")


def _strip_frontmatter(text: str) -> str:
    """去掉 YAML frontmatter，只校验正文（frontmatter 里出现 side_hint 变量声明是允许的）。"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :]
    return text


# 被解放的 3 个 task prompt（constitution 是 layer，单独校验）
_LIBERATED_TASKS = [
    "tasks/task_trend_agent_direction.md",
    "tasks/task_swing_agent.md",
    "tasks/task_trend_agent_review.md",
]

# 禁词：规则驱动 / 闸门威胁 / 偏见残留（正文，不含 frontmatter）
# 解放性否定句（"不会被强制 hold"/"系统不会命令你必须 close"/"系统不会拒单"）允许存在，
# 扫描前先把这类"否定+禁词"片段替换为占位符，避免误伤 liberation 措辞。
_FORBIDDEN_PATTERNS = [
    r"side_hint",            # 不再把系统方向锚点注入 prompt
    r"min_score",            # 不再把外部评分门槛注入 prompt
    r"必须\s*hold",          # 命令式 hold
    r"强制\s*hold",          # 强制 hold
    r"等待优于开仓",          # 不再"等待优于开仓"教条
    r"代码强制",              # 不再用"代码强制"威胁
    r"代码会拒单",
    r"拒单",                  # 不用"拒单"威胁
    r"必须\s*close",          # review 不再命令式 close
    r"禁止同向",              # 改由风险底线第 5 条客观描述
    r"不逆势",                # swing system bias 已删
    r"不追涨杀跌",            # swing system bias 已删
]

# 解放性否定前缀：出现这些前缀 + 禁词时，视为合法的 liberation 措辞，扫描时整体抹掉。
_LIBERATION_NEGATIONS = [
    "不会被强制 hold",
    "不会被强制",
    "不会命令你",
    "不会命令",
    "不会强制",
    "不会拒单",
    "不会因为",
    "不达标不会被",
    "不被",
    "不会",
    "不会输出",
]

# 必含：数据驱动 + 自由推理 + 风险底线标记
_REQUIRED_PATTERNS_BY_TASK = {
    "tasks/task_trend_agent_direction.md": [
        r"风险底线",
        r"自主决定",
        r"quant_feature_table",
        r"deep_context",
        r"evidence_block",
        r"memory_block",
        r"cooldown_active",
        r"timing_assessment",        # 新增：中周期择时
        r"what_would_change_my_mind",  # 新增：反向假设
        r"cited_fact_ids",
    ],
    "tasks/task_swing_agent.md": [
        r"风险底线",
        r"自主决定",
        r"quant_feature_table",
        r"deep_context",
        r"evidence_block",
        r"memory_block",
        r"cooldown_active",
        r"what_would_change_my_mind",
        r"cited_fact_ids",
    ],
    "tasks/task_trend_agent_review.md": [
        r"风险底线",
        r"自主决定",
    ],
}


# ──────────────────────────────────────────────────────────────
# 测试 1：禁词扫描
# ──────────────────────────────────────────────────────────────

def _strip_liberation_negations(text: str) -> str:
    """抹掉解放性否定句（"不会被强制 hold" 等），避免误伤合法的 liberation 措辞。

    把这些片段替换为占位符后再扫描禁词，就能区分"命令式"与"否定命令式"。
    """
    out = text
    for neg in _LIBERATION_NEGATIONS:
        out = out.replace(neg, "（已抹掉解放性否定）")
    return out


@pytest.mark.parametrize("rel_path", _LIBERATED_TASKS)
def test_no_forbidden_patterns_in_liberated_tasks(rel_path: str):
    """解放后的 task prompt 正文不得包含规则驱动 / 闸门威胁 / 偏见禁词。"""
    body = _strip_frontmatter(_read(rel_path))
    body = _strip_liberation_negations(body)
    offenders = []
    for pat in _FORBIDDEN_PATTERNS:
        m = re.search(pat, body)
        if m:
            offenders.append(f"命中 /{pat}/ → ...{m.group(0)}...")
    assert not offenders, f"[{rel_path}] 正文仍含禁词:\n" + "\n".join(offenders)


def test_constitution_is_risk_floors_only():
    """constitution 必须只保留 5 条风险底线，删除 6 硬约束 / 阈值表 / 等待优于开仓 / 代码威胁。"""
    body = _strip_frontmatter(_read("layers/protocol_midlong_risk_constitution.md"))

    # 必含 5 条风险底线
    assert "风险底线" in body, "constitution 缺少 '风险底线' 章节"
    assert "1.5%" in body, "constitution 缺少单笔风险 1.5% 防破产线"
    assert "杠杆" in body, "constitution 缺少杠杆上限"
    assert "固定交易对" in body, "constitution 缺少交易对边界"
    assert "数据完整性" in body or "数据缺失" in body, "constitution 缺少数据完整性底线"
    assert "同向" in body, "constitution 缺少同向开仓上限"

    # 禁止残留：旧 6 硬约束 / 阈值表 / 命令式 / 威胁
    forbidden = [
        r"等待优于开仓",
        r"代码.{0,4}拒单",
        r"RR.{0,4}≥.{0,4}2\.0",   # RR 硬门槛
        r"RR.{0,4}≥.{0,4}3\.0",
        r"min_sl|max_sl",          # SL 区间表
        r"same_dir_losses.{0,4}≥.{0,4}2",  # 旧冷却命令
        r"\+15",                   # 旧置信度门槛偏移
    ]
    offenders = []
    for pat in forbidden:
        if re.search(pat, body):
            offenders.append(pat)
    assert not offenders, f"constitution 仍残留旧规则: {offenders}"


# ──────────────────────────────────────────────────────────────
# 测试 2：必含数据上下文 + 自由推理标记
# ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel_path", _LIBERATED_TASKS)
def test_required_patterns_present(rel_path: str):
    """解放后的 task prompt 必须含风险底线 / 自主决定 / 数据上下文变量。"""
    body = _strip_frontmatter(_read(rel_path))
    required = _REQUIRED_PATTERNS_BY_TASK[rel_path]
    missing = [p for p in required if not re.search(p, body)]
    assert not missing, f"[{rel_path}] 缺少必含模式: {missing}"


# ──────────────────────────────────────────────────────────────
# 测试 3：task_trend_agent_review JSON schema 保持稳定（短线兼容）
# ──────────────────────────────────────────────────────────────

def test_trend_review_schema_unchanged():
    """review 的 JSON schema 字段必须保持稳定（scalp 复查线程解析它）。

    必需字段（来自 manifest trend_review_json_v3 + 旧版 v3 schema）：
    action / reduce_ratio / trend_still_valid / trend_strength /
    invalidation_triggered / invalidation_evidence / reasoning
    """
    body = _strip_frontmatter(_read("tasks/task_trend_agent_review.md"))
    required_fields = [
        "action",
        "reduce_ratio",
        "trend_still_valid",
        "trend_strength",
        "invalidation_triggered",
        "invalidation_evidence",
        "reasoning",
    ]
    missing = [f for f in required_fields if f not in body]
    assert not missing, f"task_trend_agent_review schema 字段丢失: {missing}"

    # action 枚举值仍为 hold/reduce/close/tighten_trailing
    assert "hold" in body and "reduce" in body and "close" in body and "tighten_trailing" in body, \
        "review action 枚举不完整"


def test_trend_review_no_command_lines():
    """review 正文不得再含 4 条命令式约束（lines 85-88 旧版）。"""
    body = _strip_frontmatter(_read("tasks/task_trend_agent_review.md"))
    # 旧命令："invalidation_triggered=true 时必须 action=close" 等
    forbidden_commands = [
        r"invalidation_triggered=true.{0,6}必须.{0,6}close",
        r"浮盈.{0,6}≥.{0,6}TP2.{0,20}必须",
        r"trailing_atr_mult.{0,20}只能收紧",
        r"浮亏.{0,6}且.{0,20}必须.{0,6}reduce",
    ]
    offenders = [p for p in forbidden_commands if re.search(p, body)]
    assert not offenders, f"review 仍残留命令式约束: {offenders}"


# ──────────────────────────────────────────────────────────────
# 测试 4：qual_layer 内联 fallback band = -20..+20
# ──────────────────────────────────────────────────────────────

def test_qual_layer_fallback_band_is_wide():
    """qual_layer 内联 fallback 的 conviction_delta band 必须为 -20..+20（与 md 对齐）。"""
    qual_path = _REPO_ROOT / "backend" / "services" / "mlto" / "qual_layer.py"
    assert qual_path.exists(), f"qual_layer.py 不存在: {qual_path}"
    src = qual_path.read_text(encoding="utf-8")

    # 必含 -20..+20，不得含旧 -8..8
    assert "-20..+20" in src or "-20..20" in src, \
        "qual_layer fallback band 未更新为 -20..+20"
    # 旧 band 不应再出现在 fallback 字符串里（注意：其他地方可能有 -8，但 fallback 段不应有 -8..8）
    assert "-8..8" not in src, "qual_layer 仍残留旧 band '-8..8'"


# ──────────────────────────────────────────────────────────────
# 测试 5：side_hint / min_score 不再注入 trend prompt 渲染上下文
# ──────────────────────────────────────────────────────────────

def test_trend_prompt_injection_no_longer_passes_side_hint_min_score():
    """trend_agent._build_direction_prompt 的 render_agent_task 变量字典
    不应再包含 side_hint / min_score 键（它们是死变量）。"""
    trend_path = _REPO_ROOT / "backend" / "services" / "trend_agent.py"
    src = trend_path.read_text(encoding="utf-8")

    # 截取 render_agent_task("task_trend_agent_direction", {...}) 的变量字典片段
    idx = src.find('"task_trend_agent_direction"')
    assert idx != -1, "未找到 task_trend_agent_direction 渲染调用"
    snippet = src[idx: idx + 1200]

    # 不应出现作为注入键（"side_hint": 或 "min_score":）
    assert re.search(r'"side_hint"\s*:', snippet) is None, \
        "render_agent_task 仍在注入 side_hint 键（死变量）"
    assert re.search(r'"min_score"\s*:', snippet) is None, \
        "render_agent_task 仍在注入 min_score 键（死变量）"


# ──────────────────────────────────────────────────────────────
# 测试 6：system prompt 不再含顺势 / 不逆势 偏见
# ──────────────────────────────────────────────────────────────

def test_system_prompts_allow_contrarian():
    """trend/swing 的 system prompt 不得含 '顺势而为'/'不逆势'/'不追涨杀跌' 偏见。"""
    for rel in ["backend/services/trend_agent.py", "backend/services/swing_agent.py"]:
        path = _REPO_ROOT / rel
        src = path.read_text(encoding="utf-8")
        # 只校验 system content 字符串段（role: system 之后的 content）
        # 简化：在整个文件里不应再出现这些偏见短语作为指令
        assert "顺势而为" not in src, f"{rel} system prompt 仍含 '顺势而为'"
        assert "不逆势" not in src, f"{rel} system prompt 仍含 '不逆势'"
        assert "不追涨杀跌" not in src, f"{rel} system prompt 仍含 '不追涨杀跌'"


# ──────────────────────────────────────────────────────────────
# 测试 7：导入冒烟（services 仍可正常导入）
# ──────────────────────────────────────────────────────────────

def test_services_importable():
    """修改后的 service 模块仍可正常导入。"""
    # 确保仓库根在 sys.path
    root_str = str(_REPO_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from backend.services import trend_agent as ta  # noqa: F401
    from backend.services import swing_agent as sa  # noqa: F401
    from backend.services.mlto import qual_layer as ql  # noqa: F401
    assert hasattr(ta, "TrendAgent")
    assert hasattr(sa, "SwingAgent")
    assert hasattr(ql, "_build_prompt") or hasattr(ql, "update_thesis")


# ──────────────────────────────────────────────────────────────
# 测试 8：端到端渲染——新 prompt 真正被 registry 服务（非 stale DB 缓存 / 非 fallback）
# ──────────────────────────────────────────────────────────────

def test_runtime_render_serves_liberated_prompts():
    """端到端：render_agent_task 必须返回新写的 prompt 内容，而非 fallback 或 stale DB 缓存。

    回归保护：
    - manifest 版本必须 bump 到 3.1.0，否则 S1-13b 版本校验不会让旧 DB 缓存失效；
    - agent_prompt_service._trace_prompt 必须接受 extra kwarg（否则 L2 命中时渲染抛异常走 fallback）。
    """
    root_str = str(_REPO_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    # 清除 registry 单例 + manifest lru_cache，强制重新读取磁盘
    from backend.services import prompt_registry as _pr
    _pr._registry = None
    _pr._load_manifest.cache_clear()

    from backend.services.agent_prompt_service import render_agent_task

    base_vars = {
        "symbol": "BTCUSDT",
        "quant_feature_table": "[qft]",
        "atr_block": "[atr]",
        "memory_block": "[mem]",
        "recent_loss_block": "[rll]",
        "cooldown_active": "false",
        "macro_block": "[macro]",
        "deep_context": "[deep]",
        "evidence_block": "[ev]",
        "compact_report": "[cr]",
        "orchestrator": {},
        "regime": "trending",
        "long_opens_week": "0",
        "agent_constraints": "",
        "side": "long",
        "entry_price": "100",
        "mark_price": "105",
        "pnl_pct": "5",
        "hold_hours": "12",
        "leverage": "3",
    }
    for tid in ["task_trend_agent_direction", "task_swing_agent", "task_trend_agent_review"]:
        out = render_agent_task(tid, base_vars, consumer="liberation_test", fallback_text="__FALLBACK__")
        assert out.strip() != "__FALLBACK__", \
            f"{tid} 渲染走了 fallback（可能 _trace_prompt 缺 extra 参数 或 manifest 版本未 bump）"
        assert "风险底线" in out, f"{tid} 渲染结果不含 '风险底线'（可能在用 stale DB 缓存）"
        assert "自主决定" in out, f"{tid} 渲染结果不含 '自主决定'"
        assert "side_hint" not in out, f"{tid} 渲染结果仍含 side_hint"
        assert "min_score" not in out, f"{tid} 渲染结果仍含 min_score"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
