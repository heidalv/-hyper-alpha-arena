"""
MVP 连通性测试脚本 — 验证「交易机 → GPU 算力机」整条推理链路。

用法（在交易机上、项目根目录执行）：
    python -m backend.services.local_llm.connectivity_check --base-url http://192.168.1.100:8000/v1
    python -m backend.services.local_llm.connectivity_check --base-url http://192.168.1.100:11434/v1 --api-key ollama

本脚本只做读操作（调一次 /v1/models 和一次 /v1/chat/completions），
不写库、不调 Governor、不下单——是纯验证，可安全反复运行。

通过标准：4 项检查全 PASS（网络可达 / 模型列表 / 真实推理 / JSON 参数）。
任何一项 FAIL 都说明链路未通，按提示修复后再继续集成。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional

# 测试用的门控参数优化 prompt（与 gate_optimizer_service 保持一致的风格）
_SYSTEM_PROMPT = (
    "你是加密永续合约交易平台的门控参数优化器。根据给定的市场状态和历史交易统计，"
    "输出建议的门控参数。只能输出 JSON，不要输出任何其它内容。"
)

_TEST_USER_PROMPT = (
    "当前市场环境：trending（趋势）\n"
    "过去7天统计：\n"
    "- 交易笔数：28\n"
    "- 胜率：42%\n"
    "- 平均盈利：1.8%\n"
    "- 平均亏损：2.3%\n"
    "- 手续费占总盈亏比：35%\n"
    "当前门控参数：min_risk_reward=1.8, scalp_min_confidence=65, max_daily_trades=10\n\n"
    "请输出建议参数，JSON 格式：\n"
    '{"min_risk_reward": <1.5-3.0的数>, '
    '"scalp_min_confidence": <55-85的整数>, '
    '"max_daily_trades": <3-10的整数>, '
    '"confidence": <0-1的数>, '
    '"reasoning": "<一句话理由>"}'
)

# 期望的参数键（Governor 受管的 4 个 key 中本模型可建议的 3 个数值型）
_EXPECTED_KEYS = {"min_risk_reward", "scalp_min_confidence", "max_daily_trades"}


def _print_result(name: str, ok: bool, detail: str) -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: {detail}")
    return ok


def check_reachable(base_url: str, timeout: float = 10.0) -> bool:
    """检查1：网络可达性（能否连到服务端口）。"""
    print("\n[1/4] 检查网络可达性...")
    try:
        import httpx
    except ImportError:
        return _print_result("网络可达", False, "未安装 httpx，请 pip install httpx")

    # 用 /v1/models 做最轻量的探活（兼容 vLLM / Ollama 的 OpenAI 兼容层）
    url = base_url.rstrip("/") + "/models"
    try:
        with httpx.Client(timeout=timeout, verify=False) as client:
            resp = client.get(url)
        if resp.status_code == 200:
            return _print_result("网络可达", True, f"{url} 返回 200")
        return _print_result("网络可达", False, f"{url} 返回 HTTP {resp.status_code}")
    except httpx.ConnectError as err:
        hint = "连接被拒。检查：①GPU 机服务是否启动；②是否监听 0.0.0.0；③防火墙端口是否放行。"
        return _print_result("网络可达", False, f"{hint}（{err}）")
    except httpx.ConnectTimeout:
        hint = "连接超时。检查：①两机是否同内网；②GPU 机 IP 是否正确；③防火墙。"
        return _print_result("网络可达", False, hint)
    except Exception as err:  # noqa: BLE001
        return _print_result("网络可达", False, f"未知错误：{type(err).__name__}: {err}")


def check_models(base_url: str, api_key: str, timeout: float = 15.0) -> Optional[str]:
    """检查2：能列出模型，返回第一个模型名（供后续推理用）。"""
    print("\n[2/4] 检查模型列表...")
    import httpx

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=timeout, verify=False) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return None if not _print_result(
                "模型列表", False, f"HTTP {resp.status_code}: {resp.text[:200]}"
            ) else None
        data = resp.json()
        models = data.get("data") or data.get("models") or []
        if not models:
            _print_result("模型列表", False, "返回为空，GPU 机尚未加载任何模型")
            return None
        # 兼容 vLLM（data[].id）和 Ollama（models[].name / model）
        name = (
            models[0].get("id")
            or models[0].get("name")
            or models[0].get("model")
            or "<unknown>"
        )
        _print_result("模型列表", True, f"发现 {len(models)} 个模型，首个：{name}")
        return str(name)
    except Exception as err:  # noqa: BLE001
        _print_result("模型列表", False, f"{type(err).__name__}: {err}")
        return None


def check_inference(
    base_url: str, api_key: str, model: str, timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    """检查3：真实推理（发一次 chat completion），返回解析后的内容。"""
    print(f"\n[3/4] 检查真实推理（模型：{model}）...")
    import httpx

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _TEST_USER_PROMPT},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }

    t0 = time.time()
    try:
        with httpx.Client(timeout=timeout, verify=False) as client:
            resp = client.post(url, json=payload, headers=headers)
        elapsed = time.time() - t0
    except httpx.ReadTimeout:
        _print_result("真实推理", False, f"推理超时（>{timeout}s），模型可能未就绪或负载过高")
        return None
    except Exception as err:  # noqa: BLE001
        _print_result("真实推理", False, f"{type(err).__name__}: {err}")
        return None

    if resp.status_code != 200:
        return None if not _print_result(
            "真实推理", False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        ) else None

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tok = usage.get("completion_tokens", "?")
        print(f"  推理耗时 {elapsed:.1f}s，completion_tokens={tok}")
        _print_result("真实推理", True, f"成功返回 {len(content)} 字符")
        return {"content": content, "elapsed": elapsed, "raw": data}
    except (KeyError, IndexError) as err:
        _print_result("真实推理", False, f"响应结构异常：{err}；原始={resp.text[:300]}")
        return None


def check_json_params(inference_result: Optional[Dict[str, Any]]) -> bool:
    """检查4：返回内容能解析为含期望参数键的 JSON。"""
    print("\n[4/4] 检查 JSON 参数格式...")
    if not inference_result:
        return _print_result("JSON 参数", False, "上一步推理未成功，跳过")

    content = inference_result["content"].strip()
    # 剥离可能的 markdown 代码块包裹
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as err:
        return _print_result(
            "JSON 参数", False, f"JSON 解析失败：{err}；内容={content[:200]}"
        )

    if not isinstance(parsed, dict):
        return _print_result("JSON 参数", False, f"非对象：{type(parsed).__name__}")

    missing = _EXPECTED_KEYS - set(parsed.keys())
    if missing:
        return _print_result(
            "JSON 参数", False, f"缺少键：{missing}；实际键={list(parsed.keys())}"
        )

    # 抽样展示解析结果
    print(f"  解析成功，建议参数：")
    for k in sorted(_EXPECTED_KEYS):
        print(f"    {k} = {parsed[k]}")
    reason = parsed.get("reasoning") or parsed.get("reason") or ""
    if reason:
        print(f"    reasoning: {reason[:120]}")
    return _print_result("JSON 参数", True, "格式正确，含全部期望键")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="本地 LLM 连通性测试（交易机 → GPU 机）"
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="GPU 机推理服务地址，如 http://192.168.1.100:8000/v1（vLLM）或 "
        "http://192.168.1.100:11434/v1（Ollama）",
    )
    parser.add_argument(
        "--api-key",
        default="ollama",
        help="API Key（vLLM 用你设的 --api-key；Ollama 任意值即可，默认 ollama）",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="指定模型名；不填则自动用 /v1/models 返回的首个模型",
    )
    args = parser.parse_args()

    print("=" * 64)
    print("本地 LLM 连通性测试（交易机 → GPU 机）")
    print("=" * 64)
    print(f"目标地址：{args.base_url}")
    print(f"API Key ：{args.api_key}")
    print(f"模型    ：{args.model or '<自动探测>'}")

    # 逐项检查，任一失败即记下
    results = []

    # 检查1：网络可达
    results.append(check_reachable(args.base_url))
    if not results[-1]:
        print("\n❌ 网络不可达，后续检查无法进行。请先修复网络/防火墙。")
        print("   常见原因：GPU 机服务未启动 / 未监听 0.0.0.0 / 防火墙未放行端口。")
        return 1

    # 检查2：模型列表（顺带探测可用模型名）
    model = args.model or check_models(args.base_url, args.api_key)
    if not model:
        print("\n❌ 无法获取模型。请确认 GPU 机已加载 Qwen3-30B-A3B。")
        return 1
    results.append(True)

    # 检查3：真实推理
    inference = check_inference(args.base_url, args.api_key, model)
    results.append(inference is not None)

    # 检查4：JSON 参数格式
    results.append(check_json_params(inference))

    # 汇总
    passed = sum(results)
    total = len(results)
    print("\n" + "=" * 64)
    if passed == total:
        print(f"✅ 全部通过（{passed}/{total}）。链路就绪，可继续集成 gate_optimizer_service。")
        return 0
    else:
        print(f"⚠️  {passed}/{total} 项通过。请修复失败项后重试。")
        print("   提示：若仅检查4失败，说明模型能跑但输出格式需调 prompt，")
        print("   可微调 gate_optimizer_service 的 prompt 后用真实模型重测。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
