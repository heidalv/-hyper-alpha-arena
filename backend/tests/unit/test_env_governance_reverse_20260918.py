"""P2-14 回归：环境变量治理必须能看见「代码读但从未登记」这一个方向。

事故背景（轮66 审计 P2-14）
-------------------------
`backend/config/env_registry.py` 的 `validate_strict()` 只扫 `os.environ`：
「环境里有、`KNOWN_FLAGS` 里没有」会被报出来。反方向 ——
**「代码读了、名单里没有、`.env` 里也没有」** —— 它完全看不见。

而后者才是本项目历史上真正咬人的那一类：`V5_DAILY_TRADE_CAP_ENABLED` 当初
"配了却不生效"，根因就是读取名与写入名对不上，且没有任何一处能把两个方向对上。

实测（2026-09-18，本仓）：代码读取入口命中 1851 个名字、`KNOWN_FLAGS` 1639 个，
其中 **280 个「代码读但未登记」**，里面 **18 个是 `*_ENABLED/_DISABLED` 形态的开关，
连 `.env` 都没声明** —— 这些开关在配置文件里不可发现，只能靠读代码才知道它存在。

修法（轮94）：新增反向校验（`find_read_but_unregistered_flags` /
`find_undeclared_switch_flags` / `env_governance_report` / CLI `--audit`），
并把两项数量各钉一条**棘轮基线**（只许降不许升）。
同时把扫描入口集合从「只认 os.getenv 一族」扩到本仓自建的
`env_int/env_float/_env_bool/...` 与 `os.environ["X"]` —— 覆盖面不足会让这个新方向的
报告本身失真（实测漏掉 `env_int` 时，`AGENT_ANOMALY_*` 一整片被误判）。

刻意**不**做的事：「`.env` 声明但没人读」的死键方向**不**在此重复实现 ——
仓库已有权威工具与白名单门禁（`backend/scripts/audit_config_effective.py` +
`test_config_dead_keys_20260910.py::DEAD_ALLOWLIST`）。同一件事两套口径正是轮63
花大力气消除的缺陷类型。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import env_registry as er  # noqa: E402


def _write_tree(tmp: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp


# 合成 flag 名一律**运行时拼接**，不要在源码里写完整字面量：
# 本模块的扫描器会扫 `backend/**`（含本测试文件），写死的字面量会变成
# "代码读但未登记" 的一项，把棘轮基线测试自己顶红。
_SYN_PREFIX = "KLINE_ZZ_"


def _syn(name: str) -> str:
    return _SYN_PREFIX + name


# 通用词缀也运行时拼接：`audit_config_effective` 的"后缀拼接读取"启发式会把
# 「含 `"_XXX"` 字面量 + 含 `getenv(x + y)`」的文件里所有 `*_XXX` 键豁免掉，
# 本文件一旦写死这个字面量，就会把全仓所有 `*_ENABLED` 键从死键清单里抹掉
# （实测直接顶红 test_env_registry_visibility_20260910）。
_GENERIC_SUFFIX = "_" + "ENABLED"


def _getenv(name: str) -> str:
    return f'import os\nX = os.getenv("{name}", "0")\n'


# ── 扫描器：入口覆盖面（这是新方向的准确性前提）────────────────


def test_scanner_sees_all_repo_env_helper_styles(tmp_path):
    """`env_int` / `_env_bool` / `os.environ["X"]` 这些本仓写法都必须被识别。"""
    root = _write_tree(tmp_path, {
        "a.py": (
            'import os\n'
            'A1 = os.getenv("AA_FIRST_FLAG", "1")\n'
            'A2 = os.environ.get("AA_SECOND_FLAG", "1")\n'
            'A3 = os.environ["AA_THIRD_FLAG"]\n'
            'A4 = env_int("AA_FOURTH_FLAG", 1)\n'
            'A5 = _env_bool("AA_FIFTH_FLAG", False)\n'
            'A6 = env_float("AA_SIXTH_FLAG", 1.0)\n'
        ),
    })
    found = er.scan_codebase_for_flags(str(root))
    for name in ("AA_FIRST_FLAG", "AA_SECOND_FLAG", "AA_THIRD_FLAG",
                 "AA_FOURTH_FLAG", "AA_FIFTH_FLAG", "AA_SIXTH_FLAG"):
        assert name in found, f"{name} 未被识别 —— 扫描入口集合又漏了"


def test_dynamic_fragments_are_captured(tmp_path):
    root = _write_tree(tmp_path, {
        "a.py": f'import os\nX = os.getenv(f"AA_DYN_{{suffix}}{_GENERIC_SUFFIX}")\n',
    })
    frags = er.scan_codebase_for_dynamic_fragments(str(root))
    assert "AA_DYN_" in frags
    assert _GENERIC_SUFFIX in frags


# ── 反向校验：读但未登记 ─────────────────────────────────────


def test_read_but_unregistered_is_detected(tmp_path):
    """核心断言：代码读了、名单里没有的名字必须被报出来。"""
    want = _syn("NEVER_REGISTERED_FLAG")
    root = _write_tree(tmp_path, {"a.py": _getenv(want)})
    hits = er.find_read_but_unregistered_flags(str(root))
    assert want in hits


def test_registered_flag_is_not_reported(tmp_path, monkeypatch):
    """已登记的名字不得出现在缺口清单里（否则清单无法执行）。"""
    want = _syn("KNOWN_FLAG")
    root = _write_tree(tmp_path, {"a.py": _getenv(want)})
    monkeypatch.setattr(er, "KNOWN_FLAGS", frozenset({want}))
    assert want not in er.find_read_but_unregistered_flags(str(root))


def test_dynamic_piece_is_not_reported_as_a_real_name(tmp_path, monkeypatch):
    """`os.getenv("KLINE_RETENTION_DAYS_" + x)` 留下的前缀片段不是真名字，必须排除。"""
    prefix = _syn("PREFIX_")
    root = _write_tree(tmp_path, {
        "a.py": f'import os\nX = os.getenv("{prefix}" + name)\n',
    })
    monkeypatch.setattr(er, "KNOWN_FLAGS", frozenset())
    hits = er.find_read_but_unregistered_flags(str(root))
    assert prefix not in hits, "拼接前缀被当成了真实 flag 名"


def test_generic_suffix_fragment_must_not_suppress_whole_class(tmp_path, monkeypatch):
    """回归：`_ENABLED` 这种通用词缀不得豁免掉整类开关。

    实测该 bug 会让「未声明的开关」从 18 个变 0 —— 正好抹掉本项要报的东西。
    """
    root = _write_tree(tmp_path, {
        "a.py": f'import os\nX = os.getenv(f"AA_SOMETHING_{{k}}{_GENERIC_SUFFIX}")\n',
    })
    monkeypatch.setattr(er, "KNOWN_FLAGS", frozenset())
    frags = er.scan_codebase_for_dynamic_fragments(str(root))
    assert _GENERIC_SUFFIX in frags, "通用词缀仍会被提取（用于其它判据）"
    real = _syn("X_ENABLED")
    assert not er._is_dynamic_read(real, frags), (
        "通用词缀不得让任意 *_ENABLED 名字免检"
    )
    # 而"核心含内部下划线"的片段仍然有效
    assert er._is_dynamic_read("KLINE_RETENTION_DAYS_1M", {"KLINE_RETENTION_DAYS_"})


def test_non_system_prefix_is_ignored(tmp_path):
    """不匹配系统前缀的（第三方库自己的 env）不纳入，避免噪音。"""
    root = _write_tree(tmp_path, {"a.py": 'import os\nX = os.getenv("SOME_LIBRARY_THING", "0")\n'})
    assert er.find_read_but_unregistered_flags(str(root)) == []


# ── 未声明的开关（真正的可发现性缺口）────────────────────────


def test_undeclared_switch_is_reported(tmp_path, monkeypatch):
    want = _syn("FEATURE_ENABLED")
    root = _write_tree(tmp_path, {"a.py": _getenv(want)})
    monkeypatch.setattr(er, "KNOWN_FLAGS", frozenset())
    monkeypatch.setattr(er, "read_declared_env_keys", lambda root=None: {})
    assert want in er.find_undeclared_switch_flags(str(root))


def test_declared_switch_is_not_an_undeclared_switch(tmp_path, monkeypatch):
    want = _syn("FEATURE_ENABLED")
    root = _write_tree(tmp_path, {"a.py": _getenv(want)})
    monkeypatch.setattr(er, "KNOWN_FLAGS", frozenset())
    monkeypatch.setattr(er, "read_declared_env_keys", lambda root=None: {want: ".env"})
    assert want not in er.find_undeclared_switch_flags(str(root))


def test_non_switch_names_are_not_counted_as_switches(tmp_path, monkeypatch):
    """只有 `*_ENABLED/_DISABLED` 才算"开关"；普通参数不算。"""
    want = _syn("SOME_LIMIT")
    root = _write_tree(tmp_path, {"a.py": _getenv(want)})
    monkeypatch.setattr(er, "KNOWN_FLAGS", frozenset())
    monkeypatch.setattr(er, "read_declared_env_keys", lambda root=None: {})
    assert want not in er.find_undeclared_switch_flags(str(root))


# ── .env 解析 ────────────────────────────────────────────────


def test_declared_keys_parser_handles_comments_and_export(tmp_path):
    _write_tree(tmp_path, {
        ".env": (
            "# 注释行\n"
            "AA_ONE=1\n"
            "export AA_TWO=2  \n"
            "  AA_THREE = 3\n"
            "not a key line\n"
            "# AA_COMMENTED=4\n"
        ),
    })
    keys = er.read_declared_env_keys(str(tmp_path))
    assert {"AA_ONE", "AA_TWO", "AA_THREE"} <= set(keys)
    assert "AA_COMMENTED" not in keys
    assert keys["AA_ONE"] == ".env"


# ── 棘轮基线（真实仓库）──────────────────────────────────────


def test_read_but_unregistered_does_not_grow():
    """棘轮：读但未登记的数量不得增长。

    两种合法成因都会让本测试变红，处理方式不同：
      · 新加了 flag 没登记 → 补登 `KNOWN_FLAGS`（这正是本门禁的目的）；
      · 扫描器覆盖面被拓宽（看见更多名字）→ 请**下调不了就上调基线**，
        并在注释里写明是扫描器变化而非新增债务。
    """
    rep = er.env_governance_report()
    cur = len(rep["read_but_unregistered"])
    base = rep["baseline"]["read_but_unregistered"]
    assert cur <= base, (
        f"「代码读但未登记」增至 {cur}（基线 {base}）。"
        f"新增项示例: {rep['read_but_unregistered'][-10:]}"
    )


def test_undeclared_switches_do_not_grow():
    rep = er.env_governance_report()
    cur = len(rep["undeclared_switches"])
    base = rep["baseline"]["undeclared_switches"]
    assert cur <= base, (
        f"「未声明的开关」增至 {cur}（基线 {base}）：{rep['undeclared_switches']}"
    )


def test_baselines_are_plausible():
    """基线必须接近实测（防止有人把基线写成一个永远通过的大数）。"""
    rep = er.env_governance_report()
    for key in ("read_but_unregistered", "undeclared_switches"):
        cur = len(rep[key])
        base = rep["baseline"][key]
        assert base - cur <= max(5, int(base * 0.05)), (
            f"{key} 基线 {base} 远高于实测 {cur} —— 请把基线收紧到实测值"
        )


# ── 单一真源：不重复实现死键检测 ─────────────────────────────


def test_dead_key_direction_is_not_reimplemented():
    """死键方向必须指向既有权威工具，不得在本模块另起一套口径。"""
    src = (ROOT / "backend" / "config" / "env_registry.py").read_text(encoding="utf-8")
    assert "audit_config_effective" in src, "必须指向既有权威工具"
    assert "test_config_dead_keys_20260910" in src, "必须指向既有门禁测试"
    assert er.find_declared_but_unread_keys() == [], (
        "该函数已废弃，必须返回空并只保留说明（避免两套口径）"
    )


# ── 启动期接入（默认关闭，不得拖慢启动）──────────────────────


def test_startup_report_is_off_by_default(monkeypatch, caplog):
    import logging

    monkeypatch.delenv("ENV_GOVERNANCE_REPORT", raising=False)
    with caplog.at_level(logging.INFO, logger="backend.config.env_registry"):
        er.log_governance_report_if_enabled()
    assert not caplog.records, "默认不得扫盘（全仓静态扫描约 1.2s）"


def test_startup_report_runs_when_enabled(monkeypatch):
    monkeypatch.setenv("ENV_GOVERNANCE_REPORT", "true")
    called = {}

    def _fake(root=None):
        called["hit"] = True
        return {
            "code_read": 1, "registered": 1, "declared": 1,
            "read_but_unregistered": [], "undeclared_switches": [],
            "baseline": {"read_but_unregistered": 0, "undeclared_switches": 0},
        }

    monkeypatch.setattr(er, "env_governance_report", _fake)
    er.log_governance_report_if_enabled()
    assert called.get("hit"), "开关打开时必须真的体检"


def test_new_flag_is_registered():
    """本模块自己新增的 env 开关必须登记（否则就是自己踩自己定义的坑）。"""
    assert "ENV_GOVERNANCE_REPORT" in er.KNOWN_FLAGS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
