# -*- coding: utf-8 -*-
"""[F252 2026-09-20] 成交汇总「净额」口径回归测试。

## 现象（用户可见）

`/hft` 页面「成交记录（近 24h）」显示 **净额 -$15.83**，
而同一时段模拟账户已实现只有 **-$0.153**、账本逐行折算 **-$0.224**。
用户据此认为策略在巨亏 —— 实际虚报 **71 倍**，是纯显示 bug。

## 根因

`backend/api/hft_routes.py` 的 `/hft/fills` 里写的是

    "net_usd_sum": net_bp_sum / 1e4 * notional_sum

`net_bp_sum = Σ net_bp` 是**不加权的无量纲比率之和**，`notional_sum = Σ notional`
是名义之和。两者相乘等于「把每一笔各自的 bp 都当成**全部名义**的 bp」——
只有当所有行的 notional 都相等时才近似成立，而实测 notional 从 $0.26 到 $90 不等
（同一 position_id 会被多腿复用 ⇒ 名义跨 3 个数量级）。

正确口径只有一种：**逐行折算后求和**，即 `Σ (net_bp_i / 1e4 × notional_i)`。
它与 `items[].net_usd` 的求和必须**逐分一致**（前端两处都要显示，不能互相打架）。

## 顺带锁住的第二件事：加权 bp

界面上原来的「每笔均值 bp」是**等权平均**（`Σbp / n`），会把 $0.26 的碎腿
和 $90 的整腿同等对待。有金融含义的只有**名义加权 bp** = 金额 ÷ 名义 × 1e4。

## 测试写法

不启动 HTTP，直接读源码做**可执行语句**级断言（同 F249 的教训：不要用
"从子串截 N 字符"的窗口断言，窗口起点会落在注释里 ⇒ 假失败）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

ROUTES = ROOT / "backend" / "api" / "hft_routes.py"


def _code_lines(path: Path) -> str:
    """剥掉注释，只留可执行文本。

    为什么必须剥：`hft_routes.py` 在修复处写了**大段解释性注释**，其中**故意**
    包含了那个错误写法（`Σbp × Σnotional`）作为反例。若不剥注释，
    「断言错误写法不存在」会被自己的注释触发 ⇒ 假失败。
    （与 F326 同一类错误：断言撞上了注释文本。）
    """
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        # 去掉行尾注释（本文件不含 '#' 出现在字符串里的情况；SQL 里用的是 --）
        if "#" in ln:
            ln = ln.split("#", 1)[0]
        out.append(ln)
    return "\n".join(out)


def test_fills_source_exists():
    assert ROUTES.exists(), f"找不到 {ROUTES}"


def test_no_unweighted_bp_times_notional_product():
    """**核心回归**：源码里不得再出现 `Σbp × Σnotional` 这种乘积。

    历史写法（两种等价形态）：
        float(agg[4] or 0.0) / 1e4 * float(agg[5] or 0.0)
        net_bp_sum / 1e4 * notional_sum
    """
    src = _code_lines(ROUTES)
    # 形态 1：agg[4] 与 agg[5] 出现在同一个乘法表达式里
    bad1 = re.search(r"agg\[4\][^\n]*\*\s*[^\n]*agg\[5\]", src)
    bad2 = re.search(r"net_bp_sum\s*/\s*1e4\s*\*\s*notional_sum", src)
    assert bad1 is None, (
        "命中历史 bug 形态 1：`agg[4] … * … agg[5]`（Σnet_bp × Σnotional）。\n"
        "金额必须逐行折算：SUM(net_bp / 1e4 * notional)。"
    )
    assert bad2 is None, (
        "命中历史 bug 形态 2：`net_bp_sum / 1e4 * notional_sum`。\n"
        "这是把每笔的 bp 当成全部名义的 bp —— 实测虚报 71 倍。"
    )


def test_summary_uses_per_row_conversion():
    """必须存在**逐行**折算的 SQL：`SUM(net_bp / 1e4 * notional)`。"""
    src = _code_lines(ROUTES)
    # 允许空格/换行差异
    flat = re.sub(r"\s+", " ", src)
    assert re.search(r"SUM\(\s*net_bp\s*/\s*1e4\s*\*\s*notional\s*\)", flat), (
        "没有找到逐行折算的聚合表达式 `SUM(net_bp / 1e4 * notional)`。\n"
        "汇总金额必须由 SQL 逐行折算后求和，不能拿两个和相乘。"
    )


def test_weighted_bp_present():
    """必须下发名义加权 bp（前端用它替代等权均值）。"""
    src = _code_lines(ROUTES)
    for key in ("net_bp_w", "spread_bp_w", "price_bp_w", "fee_bp_w"):
        assert f'"{key}"' in src, f"summary 缺少加权字段 {key}"


def test_items_net_usd_is_per_fill():
    """逐笔 `net_usd` 必须是 `net_bp/1e4 × 该笔 notional`（每行各用自己的名义）。"""
    src = _code_lines(ROUTES)
    assert re.search(r"\"net_usd\":\s*net_bp\s*/\s*1e4\s*\*\s*notional", src), (
        "逐笔 net_usd 口径被改动：应为 net_bp / 1e4 * notional（本行名义，不是合计）。"
    )


def test_frontend_type_matches_backend_keys():
    """前端类型必须声明后端实际下发的键（否则 UI 拿不到新字段）。"""
    ts = ROOT / "frontend-next" / "src" / "lib" / "hft-api.ts"
    if not ts.exists():
        return  # 前端不在本仓库布局时跳过
    body = ts.read_text(encoding="utf-8")
    assert "net_usd_sum" in body
    assert "net_bp_w" in body, "前端 HftFillsResponse 未声明 net_bp_w（加权 bp 拿不到）"
