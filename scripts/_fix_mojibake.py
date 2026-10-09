"""还原被 CP936 误解码的 UTF-8 中文（mojibake 修复）。

原理：文件本应是 UTF-8 字节 `B`。某工具把 `B` 按 **CP936** 解码成字符串 `S`，
再以 UTF-8 写回 ⇒ 磁盘上现在是 `S.encode('utf-8')`。
逆运算：`bytes.fromhex(磁盘字节) → decode('utf-8') → encode('cp936') → decode('utf-8')`。

用法：
    python scripts/_fix_mojibake.py <file> [--apply]      # 不加 --apply 只预览
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def repair(raw_bytes: bytes) -> tuple[bytes, int, int]:
    """返回 (修复后字节, 成功修复的字符数, 仍损坏的字符数)。"""
    text = raw_bytes.decode("utf-8", errors="replace")
    out_chars = []
    fixed = bad = 0
    buf = []

    def flush():
        nonlocal fixed, bad, buf
        if not buf:
            return
        chunk = "".join(buf)
        buf = []
        try:
            rec = chunk.encode("cp936", errors="strict").decode("utf-8", errors="strict")
            out_chars.append(rec)
            fixed += len(chunk)
        except Exception:
            # 无法还原：逐字符保留，统计损坏量
            # （损坏字符通常是 U+FFFD 或 € 等替换产物）
            out_chars.append(chunk)
            bad += sum(1 for c in chunk if c in "\ufffd\u20ac")

    for ch in text:
        # 判定"是否属于 mojibake 区段"：CJK 统一表意 + 全角标点 + 常见误码符号
        if "\u4e00" <= ch <= "\u9fff" or ch in "锛鈥鈭脳鍧囧€兼棩瀛愬€?" \
                or "\uff00" <= ch <= "\uffef" or ch in "\ufffd\u20ac\u2192\u2265\u2264":
            buf.append(ch)
        else:
            flush()
            out_chars.append(ch)
    flush()
    return "".join(out_chars).encode("utf-8"), fixed, bad


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    p = Path(sys.argv[1])
    if not p.exists():
        print(f"文件不存在: {p}")
        return 1
    raw = p.read_bytes()
    fixed_bytes, n_fixed, n_bad = repair(raw)
    # 显示前后差异（只显示含非 ASCII 的行）
    before = raw.decode("utf-8", errors="replace").splitlines()
    after = fixed_bytes.decode("utf-8", errors="replace").splitlines()
    print(f"文件: {p}")
    print(f"行数: {len(before)} -> {len(after)}")
    print(f"还原字符数: {n_fixed}   仍无法还原: {n_bad}")
    shown = 0
    for i, (b, a) in enumerate(zip(before, after), 1):
        if b != a:
            print(f"\n  L{i}")
            print(f"    - {b[:110]}")
            print(f"    + {a[:110]}")
            shown += 1
            if shown >= 12:
                print("\n  …（更多差异略）")
                break
    if "--apply" in sys.argv:
        p.write_bytes(fixed_bytes)
        print(f"\n已写回 {p}")
    else:
        print("\n（预览模式，未写入。加 --apply 生效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
