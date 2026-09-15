"""静态导出 HTML 的"非法嵌套"审计（定位 React `insertBefore` 类渲染崩溃）。

[F194 2026-09-15] 为什么要查这个
--------------------------------------------------
现场：`/app/error.tsx` 渲染出
    NotFoundError
    Failed to execute 'insertBefore' on 'Node': The node before which the new node
    is to be inserted is not a child of this node.
这是 **DOM 层**错误（不是业务异常），最常见的成因是**非法 HTML 嵌套**：
浏览器解析 HTML 时会**自动纠正**结构（例如 `<p>` 里出现 `<div>` 就在 `<div>` 前自动闭合
`<p>`；`<table>` 里出现 `<div>` 会被**移到表格外面**）。而 React 拿到的树是"按源码结构"
的 ⇒ 真实 DOM 里那个"应该在的锚点节点"根本不在父节点下 ⇒ React 插入时报
`insertBefore ... not a child of this node` ✗✗。静态导出（本项目的 `out/`）尤其容易踩：
HTML 在构建时预渲染、客户端再 hydrate，两边结构一旦不一致就直接炸 ✗。

做法：直接解析 `frontend-next/out/**/index.html`（**就是浏览器要解析的那份字节**），
用显式规则找非法嵌套，不依赖浏览器：
  ① `<p>` 里出现块级元素（浏览器会提前闭合 `<p>`）；
  ② `<table>/<tbody>/<tr>` 里出现非表格子元素（会被搬出表格）；
  ③ `<select>` 里出现非 option/optgroup（会被丢弃/搬走）；
  ④ `<a>` 里再嵌 `<a>`、`<button>` 里嵌 `<button>`（同样会被浏览器改写结构）。

用法：
    python scripts/fe_html_nesting_audit.py [--root frontend-next/out] [--max 40]
"""
from __future__ import annotations

import argparse
import os
import sys
from html.parser import HTMLParser
from typing import Dict, List, Tuple

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}

# 会强制闭合 <p> 的元素（HTML 规范：p 的"闭合标签省略"触发集）
P_CLOSERS = {
    "address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section",
    "table", "ul", "li", "dd", "dt",
}
TABLE_ALLOWED = {
    "table": {"caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "script",
              "template", "style"},
    "thead": {"tr", "script", "template", "style"},
    "tbody": {"tr", "script", "template", "style"},
    "tfoot": {"tr", "script", "template", "style"},
    "tr": {"td", "th", "script", "template", "style"},
    "colgroup": {"col", "script", "template", "style"},
}
SELECT_ALLOWED = {"option", "optgroup", "script", "template", "hr"}
NEST_SAME = {"a", "button", "form", "label"}


class Audit(HTMLParser):
    def __init__(self, path: str) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.stack: List[Tuple[str, int]] = []
        self.hits: List[str] = []

    def _where(self) -> str:
        return " > ".join(f"{t}" for t, _ in self.stack[-6:]) or "(root)"

    def handle_starttag(self, tag: str, attrs) -> None:
        line = self.getpos()[0]
        if tag in VOID:
            self._check(tag, line, self_closing=True)
            return
        self._check(tag, line)
        self.stack.append((tag, line))

    def _check(self, tag: str, line: int, self_closing: bool = False) -> None:
        parent = self.stack[-1][0] if self.stack else None
        if parent == "p" and tag in P_CLOSERS:
            self.hits.append(f"L{line}: <p> 内出现 <{tag}> ⇒ 浏览器会提前闭合 <p>（路径 {self._where()}）")
        if parent in TABLE_ALLOWED and tag not in TABLE_ALLOWED[parent]:
            self.hits.append(f"L{line}: <{parent}> 内出现 <{tag}> ⇒ 浏览器会把该节点搬出表格"
                             f"（路径 {self._where()}）")
        if parent == "select" and tag not in SELECT_ALLOWED:
            self.hits.append(f"L{line}: <select> 内出现 <{tag}>（路径 {self._where()}）")
        if parent in NEST_SAME and tag == parent:
            self.hits.append(f"L{line}: <{parent}> 内再嵌 <{tag}>（路径 {self._where()}）")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self._check(tag, self.getpos()[0], self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return
        # 没有匹配的开标签 ⇒ 结构本身已乱（浏览器也会"就地修正"）
        self.hits.append(f"L{self.getpos()[0]}: 多余的 </{tag}>（路径 {self._where()}）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="frontend-next/out")
    ap.add_argument("--max", type=int, default=40)
    args = ap.parse_args()
    if not os.path.isdir(args.root):
        print(f"✗ 目录不存在: {args.root}")
        return 2

    pages = []
    for dirpath, _dirs, files in os.walk(args.root):
        for fn in files:
            if fn == "index.html":
                pages.append(os.path.join(dirpath, fn))
            elif fn.endswith(".html"):
                pages.append(os.path.join(dirpath, fn))
    pages.sort()
    print(f"审计 {len(pages)} 个预渲染 HTML（{args.root}）\n")
    bad = 0
    total = 0
    for p in pages:
        try:
            with open(p, encoding="utf-8") as f:
                html = f.read()
        except Exception as e:
            print(f"⚠ 读取失败 {p}: {e}")
            continue
        a = Audit(p)
        a.feed(html)
        route = os.path.relpath(p, args.root)
        # 只报"结构性"问题；同一条规则在同一页里可能重复很多次，去重后计数
        uniq: Dict[str, int] = {}
        for h in a.hits:
            key = h.split(":", 1)[1].strip()
            uniq[key] = uniq.get(key, 0) + 1
        total += len(a.hits)
        if uniq:
            bad += 1
            print(f"✗ {route}  （{len(a.hits)} 处）")
            for k, n in list(uniq.items())[: args.max]:
                print(f"    ×{n}  {k}")
    print(f"\n汇总：{bad}/{len(pages)} 个页面存在非法嵌套，共 {total} 处")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
