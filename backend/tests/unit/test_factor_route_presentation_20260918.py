# -*- coding: utf-8 -*-
"""[F379 2026-09-18] `factor_route` 呈现层：**IC 有、样本量无；`confidence` 不是置信度**。

## 实测载荷（真实价格下抓取，1813 字符，进主脑 `extras["factor_route"]`）

```json
{"symbol":"BTC","action":"hold","score":-0.1779,"confidence":55,
 "votes":{"macd@4h":{"z":-0.902,"vote":-0.902,"ic":-0.1143,"trend_inverted":true},
          "hv@4h":{"z":-0.442,"vote":-0.442,"ic":0.0961}, ...共 16 条},
 "reason":"factor_route score=-0.178 n=16 votes=macd@4h:-0.902 …",
 "sl_pct":0.05,"tp_pct":0.1,"kline_exchange":"active"}
```

**做对的地方（先说好话）**：每票带 `ic`；反向使用的因子显式标 `trend_inverted: true`；
`reason` 里带票数 `n=16`。

**三处不足**：
1. **票里没有样本量**：`ic=-0.1143` 是在多少样本上算的？LLM 无从得知。
   而样本量在上游是存在的（`factor_runtime_weights.json` 的 `stats` 段带 `n`，
   实测 rsi n=1223 / zscore n=1135）⇒ **有而没透传**。
   结合 §14 已查实的"95% 重叠窗口 / 208.8 行每笔"等问题，"给 IC 不给 n"正是
   本报告反复出现的同一类缺口。
2. **`confidence` 不是置信度**：`midlong_factor_route.py:457`
   `confidence = int(np.clip(50 + abs(score) * 30, 0, 80))` ——
   它只是 **|score| 的仿射映射**（score=0.178 ⇒ 55），不含样本量、不含 IC 显著性、
   不含票间离散度。字段名叫 `confidence`，LLM 极易读成"可信度"。
3. **`reason` 把 16 票原样再抄一遍**（与 `votes` 重复），载荷从"结构化的 16 票"变成
   "结构化 + 一长串裸数字"，对 LLM 只是噪声（本机实测 1813 字符里相当一部分是这个）。

⇒ 属"呈现层数字暗示了它不具备的含义"，与 §32/§33/§34 同一族，**严重度更低**
（IC 至少在场、反向标注在场），但它是**第 1 问"因子是否真被调用"的呈现面**，值得记录。
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.factor_engine import midlong_factor_route as R  # noqa: E402


def test_confidence_is_affine_map_of_score():
    """`confidence` 是 `clip(50 + |score|*30, 0, 80)` —— 若改成统计量，本用例失败。"""
    src = inspect.getsource(R.factor_route_decide)
    m = re.search(r'out\["confidence"\]\s*=\s*(.+)', src)
    assert m, "未找到 confidence 赋值"
    expr = m.group(1)
    assert "abs(score)" in expr and "50" in expr and "30" in expr, (
        f"confidence 口径变了 ⇒ 请更新报告 §35：{expr}")


def test_confidence_has_no_uncertainty_inputs():
    """confidence 不得引用样本量/IC/离散度（当前确实不引用）。"""
    src = inspect.getsource(R.factor_route_decide)
    line = next((ln for ln in src.splitlines() if 'out["confidence"]' in ln), "")
    for kw in ("_neg_ic_n", "weight_sum", "std", "n]", "len(votes)"):
        assert kw not in line, f"confidence 用到了『{kw}』⇒ 已含不确定性信息，请更新 §35"


def test_vote_payload_carries_ic_but_not_sample_size():
    """票里带 ic（好），但不带样本量（缺）——修好时本用例失败。"""
    src = inspect.getsource(R.factor_route_decide)
    seg = src[src.find("votes["):] if "votes[" in src else src
    assert '"ic"' in src or "'ic'" in src, "票里应带 ic"
    for kw in ('"n"', "'n'", '"samples"', '"n_obs"', '"ic_n"'):
        assert kw not in seg, f"票里已带样本量字段 {kw} ⇒ 请更新报告 §35"


def test_reason_string_duplicates_all_votes():
    """`reason` 把全部票再抄一遍（噪声来源）。"""
    src = inspect.getsource(R.factor_route_decide)
    assert '"factor_route score=%+.3f n=%d votes=%s"' in src, (
        "reason 模板变了 ⇒ 载荷体积与可读性需重新评估（§35）")


def test_trend_inverted_is_disclosed():
    """反向使用是**显式标注**的（这是做对的地方，锁住别退化）。"""
    src = inspect.getsource(R.factor_route_decide)
    assert "trend_inverted" in src, "反向标注消失 ⇒ 呈现层变差，请更新 §35"


def test_upstream_has_sample_size_available():
    """反证：上游权重文件里**有** `n`（说明"给 IC 不给 n"是透传缺失，不是拿不到）。"""
    import json
    p = ROOT / "data" / "factor_runtime_weights.json"
    if not p.exists():
        pytest.skip("无权重文件")
    d = json.loads(p.read_text(encoding="utf-8"))
    stats = d.get("stats") or {}
    with_n = [k for k, v in stats.items() if isinstance(v, dict) and v.get("n")]
    assert with_n, "stats 段里没有带 n 的条目 ⇒ 本结论前提变了，请复核 §35"
    print(f"[F379] 上游带样本量的因子数={len(with_n)}，样例={with_n[:3]}")
