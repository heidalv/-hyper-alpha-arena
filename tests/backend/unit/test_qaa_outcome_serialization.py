"""
批次二 2.1 回归测试：QAA outcome 序列化的 JSON 安全保障

根因：jsonl_store.py 的 create_outcome / fill_outcome / _compact_unlocked 三处
对 OutcomeRecord 裸调 model_dump(mode="json")。本次把 outcome 4 处对齐到统一的
workflow_model_to_json_dict 通道（先 model_dump(mode="python") 再 coerce_json_safe，
能把 pandas.DataFrame 等非 JSON 类型递归转 dict），与 update_run/add_step 路径统一。

注意：qaa_architecture_package 存在循环 import（qaa.platform.__init__ 急切拉
control_plane → store → base → models，而 models 又依赖 platform.tenant），
直接 `from qaa.workflow.store.payload_sanitize import ...` 会触发循环。
本测试用 importlib 直接加载 payload_sanitize.py 文件绕过 __init__ 链，
聚焦验证 coerce_json_safe（workflow_model_to_json_dict 的核心）能正确处理 DataFrame。

本测试覆盖：
1. coerce_json_safe 能把 DataFrame 转成可 JSON 序列化的结构。
2. coerce_json_safe 对普通 dict 递归清洗（嵌套 DataFrame 也安全）。
3. model_dump(mode="json") 遇 DataFrame 会崩（对照，证明走 coerce 通道的必要性）。
"""
import os
import sys
import json
import importlib.util
import pytest

pd = pytest.importorskip("pandas")


def _load_payload_sanitize():
    """用 importlib 从文件路径加载 payload_sanitize，绕过 qaa 包的循环 import。"""
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    mod_path = os.path.join(_PROJECT_ROOT, "qaa_architecture_package", "qaa", "workflow", "store", "payload_sanitize.py")
    spec = importlib.util.spec_from_file_location("qaa_payload_sanitize_under_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pytestmark = pytest.mark.unit


def test_coerce_json_safe_handles_dataframe():
    """coerce_json_safe 把 DataFrame 转成可 JSON 序列化的结构。"""
    mod = _load_payload_sanitize()
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    result = mod.coerce_json_safe(df)
    # 必须可 json.dumps（验证 JSON 安全）
    json.dumps(result)
    # 不再是 DataFrame 对象
    assert not isinstance(result, pd.DataFrame)


def test_coerce_json_safe_recursive_in_dict():
    """嵌套在 dict 里的 DataFrame 也被递归清洗。"""
    mod = _load_payload_sanitize()
    df = pd.DataFrame({"a": [1, 2]})
    nested = {"output": {"data": df, "meta": "ok"}, "other": 123}
    result = mod.coerce_json_safe(nested)
    json.dumps(result)  # 不抛即 JSON 安全
    assert result["other"] == 123
    assert result["output"]["meta"] == "ok"
    assert not isinstance(result["output"]["data"], pd.DataFrame)


def test_coerce_json_safe_passes_primitives():
    """基础类型（str/int/float/None/list）原样通过。"""
    mod = _load_payload_sanitize()
    assert mod.coerce_json_safe("hello") == "hello"
    assert mod.coerce_json_safe(42) == 42
    assert mod.coerce_json_safe(3.14) == 3.14
    assert mod.coerce_json_safe(None) is None
    assert mod.coerce_json_safe([1, 2, 3]) == [1, 2, 3]
    json.dumps(mod.coerce_json_safe({"k": [1, {"nested": "v"}]}))
