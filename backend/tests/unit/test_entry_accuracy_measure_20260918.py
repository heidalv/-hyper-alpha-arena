# -*- coding: utf-8 -*-
"""[F363 2026-09-18] 开仓准确率**测量器**的口径与统计不变量。

为什么锁这两件事：
1. **统计必须对**：判定完全依赖 `two_prop_z` / `required_n_per_group`。
   算错一次，就会得出"提升显著"或"还需 3 天"这种会误导决策的结论。
2. **口径不许漂**：本测量器存在的意义就是"换口径不再能换出结论"，
   因此终点/切分/α/功效/边界敏感性必须**预注册在文件里**，并有测试防止被悄悄改动。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

_SCRIPT = ROOT / "scripts" / "measure_entry_accuracy.py"


def _load():
    spec = importlib.util.spec_from_file_location("_measure_entry_accuracy", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ───────────────────── ① 统计正确性 ─────────────────────

def test_two_prop_z_matches_hand_computation():
    """k/n = 40/100 vs 60/100：pooled z = 2.8284，双侧 p ≈ 0.00468。"""
    z, p, p1, p2 = _load().two_prop_z(40, 100, 60, 100)
    assert p1 == pytest.approx(0.40) and p2 == pytest.approx(0.60)
    assert z == pytest.approx(2.8284, abs=1e-3)
    assert p == pytest.approx(0.00468, abs=5e-4)


def test_two_prop_z_identical_is_not_significant():
    z, p, _, _ = _load().two_prop_z(40, 100, 40, 100)
    assert abs(z) < 1e-9 and p == pytest.approx(1.0)


def test_two_prop_z_handles_empty_group():
    z, p, _, _ = _load().two_prop_z(5, 10, 0, 0)
    assert p == 1.0, "空组不得给出显著（否则会把'没数据'读成'有效'）"


def test_required_n_is_in_expected_range():
    """40% → 50%（10pp）在 α=0.05/80% 功效下每组约 400 笔（教科书 407 附近）。"""
    n = _load().required_n_per_group(0.40, 0.50)
    assert 350 <= n <= 480, n


def test_required_n_grows_as_effect_shrinks():
    m = _load()
    assert m.required_n_per_group(0.40, 0.45) > m.required_n_per_group(0.40, 0.55)
    assert m.required_n_per_group(0.40, 0.42) > m.required_n_per_group(0.40, 0.50)


def test_required_n_falls_back_when_post_not_better():
    """POST 不如 PRE 时不能算出 0/负数样本，应按 +5pp 兜底。"""
    m = _load()
    assert m.required_n_per_group(0.50, 0.40) == m.required_n_per_group(0.50, 0.55)


def test_welch_ci_sign_and_width():
    import math
    m = _load()
    t, (lo, hi) = m.welch_ci([1.0, 2.0, 3.0, 4.0], [2.0, 3.0, 4.0, 5.0])
    assert t > 0 and lo < 1.0 < hi, (t, lo, hi)
    # nan != nan，必须逐元素用 isnan 判（我第一版直接比较元组，被自己的用例抓个正着）
    t0, (lo0, hi0) = m.welch_ci([1.0], [2.0])
    assert t0 == 0.0 and math.isnan(lo0) and math.isnan(hi0)


# ───────────────────── ② 预注册口径不许漂 ─────────────────────

def test_protocol_is_preregistered_in_docstring():
    doc = _load().__doc__ or ""
    for kw in ("主终点", "trade_facts", "α=0.05", "80%", "边界敏感性", "只读"):
        assert kw in doc, f"预注册口径缺少声明: {kw}"


def test_default_cut_matches_upgrade_window():
    assert _load().DEFAULT_CUT.startswith("2026-09-16"), "默认切分点应锚定架构升级窗口"


def test_script_is_read_only_and_import_safe():
    code = _SCRIPT.read_text(encoding="utf-8")
    for bad in ("INSERT ", "UPDATE ", "DELETE ", "os.remove(", "shutil.rmtree("):
        assert bad not in code, f"测量器不得包含 {bad}"
    # 必须有 RLS 放行，否则 alpha_arena 的 FORCE RLS 会读出 0 行（本次复查踩过两次）
    assert "SET app.is_admin='on'" in code, "未放行 RLS 会得到 0 行假读数"
    # import 时不得接管 stdout（否则测试进程捕获崩）
    assert 'if __name__ == "__main__":' in code
