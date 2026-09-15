"""前端契约体检：把 `/arbitrage/lanes` 页面**渲染时真正解引用**的字段逐条核对。

[F195 2026-09-15] 为什么需要它
--------------------------------------------------
现场：`/arbitrage/lanes` **刷新就崩**（稳定复现），报的是 DOM 层
`NotFoundError: Failed to execute 'insertBefore' ...`。
稳定复现 + DOM 层报错 ⇒ 最可能是"渲染期抛错被 React 的错误恢复掩盖成 insertBefore"，
而渲染期抛错几乎只有两类：**字段缺失/类型不符**（`.toFixed()`、展开 `null`）或结构性
非法嵌套 ✗。结构性那部分我已用 `fe_html_nesting_audit.py` 扫过预渲染 HTML（0 命中 ✓），
但**数据驱动的部分不在预渲染里** ⇒ 必须直接把接口返回与组件解引用逐条对表 ✓。

做法：拉真实接口，按组件代码里出现的解引用逐项断言类型，**不依赖浏览器** ⇒
不需要登录、不需要 Playwright，几秒出结论 ✓。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any, List, Tuple

BASE = os.getenv("ARENA_API", "http://127.0.0.1:8000/api")


def get(path: str) -> Any:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=25) as r:
        return json.loads(r.read())


def typeof(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


class Check:
    def __init__(self) -> None:
        self.bad: List[str] = []
        self.n = 0

    def want(self, where: str, obj: Any, key: str, types: Tuple[str, ...],
             allow_null: bool = False) -> Any:
        """断言 obj[key] 存在且类型在 types 内（allow_null 时允许 null）。"""
        self.n += 1
        if not isinstance(obj, dict) or key not in obj:
            self.bad.append(f"{where}.{key} 缺失（组件会直接解引用它）")
            return None
        v = obj[key]
        t = typeof(v)
        if v is None and not allow_null:
            self.bad.append(f"{where}.{key} 是 null（组件按 {types} 用）")
            return None
        if v is not None and t not in types:
            self.bad.append(f"{where}.{key} 类型是 {t}，组件按 {types} 用（值={v!r:.120}）")
        return v


def check_promotion(c: Check, where: str, p: Any) -> None:
    """PromotionBoard + 车道表里的解引用：progress_pct.toFixed / 展开 passed+failed。"""
    if p is None:
        return  # 组件对 null 有兜底（返回"尚无晋升判定"）✓
    if not isinstance(p, dict):
        c.bad.append(f"{where} 是 {typeof(p)}，组件按对象用")
        return
    c.want(where, p, "progress_pct", ("number",))       # page.tsx:175 / PromotionBoard:108 toFixed
    c.want(where, p, "passed", ("array",))              # [...passed, ...failed]
    c.want(where, p, "failed", ("array",))
    c.want(where, p, "ready", ("bool",))
    c.want(where, p, "as_of", ("string",), allow_null=True)
    c.want(where, p, "labels", ("object",), allow_null=True)


def main() -> int:
    c = Check()
    print(f"契约体检 {BASE}\n")

    lanes = get("/trading/lanes")
    items = lanes.get("items") if isinstance(lanes, dict) else None
    print(f"GET /trading/lanes → {typeof(lanes)}，items={typeof(items)}"
          f"（{len(items) if isinstance(items, list) else '—'} 条）")
    if not isinstance(items, list):
        c.bad.append("/trading/lanes.items 不是数组（页面 rows 取值会失败）")
        items = []
    for it in items:
        lid = it.get("lane_id") if isinstance(it, dict) else "?"
        w = f"lane[{lid}]"
        c.want(w, it, "lane_id", ("string",))
        c.want(w, it, "mode", ("string",))
        c.want(w, it, "status", ("string",), allow_null=True)
        c.want(w, it, "edge", ("object",), allow_null=True)   # EdgeBadge edge={lane.edge}
        c.want(w, it, "meta", ("object",), allow_null=True)
        if isinstance(it.get("edge"), dict):
            e = it["edge"]
            print(f"  · {lid} edge 键 = {sorted(e.keys())}")
        check_promotion(c, f"{w}.promotion", it.get("promotion"))

    summ = get("/trading/portfolio/summary")
    print(f"GET /trading/portfolio/summary → {sorted(summ.keys()) if isinstance(summ, dict) else typeof(summ)}")
    c.want("summary", summ, "equity", ("number",), allow_null=True)
    c.want("summary", summ, "lane_budgets", ("object",), allow_null=True)
    c.want("summary", summ, "as_of", ("string",), allow_null=True)

    attr = get("/trading/portfolio/attribution?days=7")
    by_lane = attr.get("by_lane") if isinstance(attr, dict) else None
    print(f"GET /trading/attribution?days=7 → by_lane={typeof(by_lane)}"
          f"（{len(by_lane) if isinstance(by_lane, list) else '—'} 条）")
    if isinstance(by_lane, list):
        for b in by_lane[:20]:
            c.want("attribution.by_lane[]", b, "lane_id", ("string",))
            c.want("attribution.by_lane[]", b, "net_usd", ("number",), allow_null=True)

    # 详情页的三个 hook（如果用户带 ?lane= 打开）
    for lid in [it.get("lane_id") for it in items if isinstance(it, dict)][:8]:
        if not lid:
            continue
        try:
            pr = get(f"/trading/lanes/{lid}/promotion")
            check_promotion(c, f"{lid}.promotion", pr.get("promotion") if isinstance(pr, dict) else None)
        except Exception as e:
            c.bad.append(f"GET /trading/lanes/{lid}/promotion 失败: {e}")

    # [F195] 统一账户的策略分账行 —— **这正是本次整页崩溃的触发点**：
    # `UnifiedAccountCard` 渲染 `fmtNum(s.fills ?? s.entries, 0)`，两者都为 null 时
    # 传 null 进数值格式化 ⇒ TypeError ⇒ 整页被错误边界接管 ✗（详见 lib/format.ts 注释）。
    # ⇒ 契约检查必须盯住"**行内字段的可空性**"，而不只是顶层字段是否存在 ✓。
    ua = get("/trading/account/unified?days=30")
    rows = ua.get("strategies") if isinstance(ua, dict) else None
    print(f"GET /trading/account/unified?days=30 → strategies={typeof(rows)}"
          f"（{len(rows) if isinstance(rows, list) else '—'} 行）")
    if isinstance(rows, list):
        for r in rows:
            c.want("unified.strategies[]", r, "strategy_type", ("string",))
            for f in ("net_usd", "pnl_usd", "fee_usd", "capital_usd"):
                c.want("unified.strategies[]", r, f, ("number",))
            f_ok = r.get("fills") is not None or r.get("entries") is not None
            if not f_ok:
                c.bad.append(f"unified.strategies[{r.get('strategy_type')}] 的 fills/entries 都为 null"
                             "（前端 `fmtNum(s.fills ?? s.entries, 0)` 会崩）")

    print()
    if c.bad:
        print(f"✗ 发现 {len(c.bad)} 处契约不符（检查 {c.n} 个字段）：")
        for b in c.bad:
            print("   - " + b)
        return 1
    print(f"✓ 所有 {c.n} 个字段契约一致（页面解引用的字段都存在且类型正确）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
