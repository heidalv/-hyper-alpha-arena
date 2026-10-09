"""找出（并可自动修复）Python 源码里「双引号字符串内部嵌了 ASCII 双引号」的行。

## 为什么需要

本会话里同一个错误犯了 5 次：
    print("      ⇒ 这是"最差位置"，不是"最好位置"")
                  ^^        ^^  ^^        ^^
中文说明里习惯用 ASCII 双引号做引号，但外层已经是 `"` ⇒ 字符串提前结束，
`SyntaxError: invalid syntax. Perhaps you forgot a comma?`
每次都要跑一次才发现，浪费一整轮。

## 判定方法

用 `tokenize` 找**词法错误**，而不是猜正则 —— 正则会把
`print("a" % x, "b")` 这种**合法**代码误报（本会话也犯过这个错）。

对每个 `TokenError` / `SyntaxError` 报出的行，检查是否属于本类问题：
该行含 2 个以上 ASCII 双引号，且其中夹着 CJK 字符 ⇒ 疑似引号嵌套。

## 修法

把**内层**的 ASCII 双引号换成中文引号 `「」`（本项目文档风格，且不影响语义）。
只改被判定为可疑的行，其余不动。

用法：
    python scripts/_lint_quote_nesting.py <file|目录> [--fix]
"""
from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def syntax_error_line(path: Path):
    """返回 (行号, 异常) 或 (None, None)。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        compile(src, str(path), "exec")
        return None, None
    except SyntaxError as e:
        return e.lineno, e
    except Exception as e:  # noqa: BLE001
        return None, e


def is_quote_nesting(line: str) -> bool:
    """该行是否疑似「双引号字符串里嵌 ASCII 双引号」。"""
    if line.count('"') < 4:
        return False
    if not CJK.search(line):
        return False
    # 退化情形：print("中文"中文")  —— 引号数必为奇数或位置明显错乱
    return True


def fix_line(line: str) -> str:
    """把内层的 ASCII 双引号替换成中文引号。

    策略：保留**第 1 个** `"` 与**最后 1 个** `"`（它们是字符串边界），
    中间的 `"` 成对替换为 `「` / `」`。
    """
    first = line.find('"')
    last = line.rfind('"')
    if first < 0 or last <= first:
        return line
    head, mid, tail = line[:first + 1], line[first + 1:last], line[last:]
    out = []
    open_q = True
    for ch in mid:
        if ch == '"':
            out.append("「" if open_q else "」")
            open_q = not open_q
        else:
            out.append(ch)
    return head + "".join(out) + tail


def process(p: Path, apply: bool) -> int:
    ln, err = syntax_error_line(p)
    if err is None:
        return 0
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    print(f"\n=== {p} ===")
    print(f"  语法错误: {type(err).__name__}: {str(err)[:100]}")
    touched = 0
    # 从报错行往上找 6 行内的可疑行（错误常在上游几行）
    lo = max(0, (ln or 1) - 7)
    hi = min(len(lines), (ln or 1) + 2)
    for i in range(lo, hi):
        if is_quote_nesting(lines[i]):
            fixed = fix_line(lines[i])
            if fixed != lines[i]:
                print(f"  L{i+1}")
                print(f"    - {lines[i].rstrip()[:120]}")
                print(f"    + {fixed.rstrip()[:120]}")
                lines[i] = fixed
                touched += 1
    if touched and apply:
        p.write_text("".join(lines), encoding="utf-8")
        ln2, err2 = syntax_error_line(p)
        print(f"  已写回；重新检查: {'OK' if err2 is None else str(err2)[:80]}")
    elif touched:
        print("  （预览模式，未写回；加 --fix 生效）")
    else:
        print("  未找到可疑的引号嵌套行 —— 需要人工看")
    return touched


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--fix" in sys.argv
    if not args:
        print(__doc__)
        return 2
    targets = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            targets += [x for x in p.rglob("*.py") if "__pycache__" not in str(x)]
        elif p.exists():
            targets.append(p)
    total = 0
    bad = 0
    for p in targets:
        ln, err = syntax_error_line(p)
        if err is None:
            continue
        bad += 1
        total += process(p, apply)
    print(f"\n扫描 {len(targets)} 个文件：{bad} 个语法错误，修复 {total} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
