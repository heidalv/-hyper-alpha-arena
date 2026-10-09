"""h491：`.env` 完整性审计——找出**被注释吞掉的配置项**（静默失效的经典形态）。

事故（2026-09-29 实测）：
  `.env:2528` 是一条注释行，但某次写入的编码损坏把注释与配置**粘在了一起**：

      # 前提：注册表已与 env 逐键同步（H187 审计 0 分叉），故本开关是**零行为变<乱码>MM_REGISTRY_AUTHORITATIVE=1

  ⇒ `MM_REGISTRY_AUTHORITATIVE` 实际**从未生效**（运行态 `param_authority=env` 佐证），
  而注释本身写着"设为 1 后……注册表改动热采用即生效、无需重启"——
  即"以为打开了、其实没打开"的静默偏差（与 F189/F280/F298/F327 同一族）。

审计规则（只读）：
  R1 **注释吞配置**：以 `#` 开头的行里出现 `<大写键名>=<值>`，且该键名看起来是
     合法环境变量名（≥5 个字符、全大写下划线）⇒ 可疑；
  R2 **重复键**：同一键在非注释行里出现多次（后值生效，前面的静默失效）；
  R3 **空值**：`KEY=`（空值）在非注释行里 ⇒ 可能让 `os.getenv` 返回空串；
  R4 可选 `--check KEY`：该键是否真的存在于**进程可见**的配置里（用 .env 解析结果判断）。

用法：python scripts/h491_env_integrity.py [--fix-suspicious]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
OUT = ROOT / "research_l1" / "out" / "h491_env_integrity.json"
KEYVAL = re.compile(rb"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{4,})=([^\s#]*)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix-key", default="",
                    help="**只修一个键**：若它只在注释行里出现，则在该键前插入换行"
                         "使其成为独立生效行（字节级、其它内容零改动）")
    ap.add_argument("--check", default="", help="额外检查某个键是否生效")
    a = ap.parse_args()
    raw = ENV.read_bytes()
    lines = raw.split(b"\n")
    susp, dupes, empties = [], {}, []
    seen: dict = {}
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s:
            continue
        if s.startswith(b"#"):
            for m in KEYVAL.finditer(s):
                key = m.group(1).decode("ascii", "replace")
                val = m.group(2).decode("ascii", "replace")
                # 注释里出现"键=值"形态 ⇒ 可疑（排除纯说明性文字，如 URL=... 少见）
                susp.append({"line": i, "key": key, "value": val,
                             "text": s.decode("utf-8", "replace")[:160]})
        else:
            if b"=" in s:
                k, _, v = s.partition(b"=")
                k = k.strip().decode("ascii", "replace")
                v = v.strip()
                if not v:
                    empties.append({"line": i, "key": k})
                if k in seen:
                    dupes.setdefault(k, [seen[k]]).append(i)
                else:
                    seen[k] = i
    print(f"扫描 {ENV.name}：{len(lines)} 行，非注释键 {len(seen)} 个")
    print("=" * 92)
    # R1 收窄：注释里提到键名是**正常文档写法**；真正的信号是
    #   ① 该键**只在注释里出现**（= 从未生效，"以为打开了"）；
    #   ② 捕获到的值里带非 ASCII 乱码（= 注释与配置被粘连的痕迹）。
    buried = [s for s in susp if s["key"] not in seen]
    corrupt = [s for s in susp if any(ord(ch) > 127 for ch in s["value"])
               or "\ufffd" in s["text"]]
    print(f"R1a **只在注释里出现**（从未生效）= {len(buried)}")
    for s in buried[:25]:
        print(f"  L{s['line']:5d}  {s['key']:34s} = {s['value'][:24]!r}")
    print(f"R1b 注释行里值含乱码/粘连痕迹 = {len(corrupt)}")
    for s in corrupt[:15]:
        print(f"  L{s['line']:5d}  {s['key']:30s} value={s['value'][:40]!r}")
    print(f"R1（原口径，仅计数，多为正常引用）= {len(susp)}")
    print(f"R2 重复键 = {len(dupes)}")
    for k, v in list(dupes.items())[:10]:
        print(f"  {k:34s} 行 {v}")
    print(f"R3 空值 = {len(empties)}")
    for e in empties[:10]:
        print(f"  L{e['line']:5d}  {e['key']}")
    # 关键开关的生效性
    important = ["MM_REGISTRY_AUTHORITATIVE", "MM_LANE_LIMITS_ENFORCE",
                 "MM_SPREAD_MULT", "MM_MAX_ONE_SIDE_SEC", "MM_JUDGE_LAG_BUCKETS"]
    print("=" * 92)
    print("关键开关在 .env 里的**生效状态**：")
    eff = {}
    for k in important:
        active = None
        for i, ln in enumerate(lines, 1):
            s = ln.strip()
            if s.startswith(b"#") or b"=" not in s:
                continue
            kk, _, vv = s.partition(b"=")
            if kk.strip().decode("ascii", "replace") == k:
                active = (i, vv.strip().decode("utf-8", "replace"))
                break
        inside_comment = any(x["key"] == k for x in susp)
        eff[k] = {"active_line": active[0] if active else None,
                  "value": active[1] if active else None,
                  "buried_in_comment": inside_comment}
        flag = "✓ 生效" if active else ("✗ **被注释吞掉**" if inside_comment else "— 未设")
        print(f"  {k:32s} {flag:20s} "
              f"{('L' + str(active[0]) + ' = ' + active[1]) if active else ''}")
    if a.check:
        print(f"\n--check {a.check}: {eff.get(a.check, '未在关键表里（自行核对上面 R1/R2）')}")
    fixed = 0
    if a.fix_key:
        # ⚠️ **只修指定的那一个键**。为什么不提供"一键修全部"：
        # 本文件里 81 个键只存在于注释中，其中绝大多数属于**其它子系统**
        # （MIDLONG / V5 / MLTO / 融合…）。一次性激活它们 = 多变量轰炸，
        # 且与"每次只动一个变量"的纪律直接冲突 ⇒ 工具只做外科手术。
        key_b = a.fix_key.encode()
        if a.fix_key in seen:
            print(f"\n--fix-key {a.fix_key}: 已是生效行（L{seen[a.fix_key]}），无需修复")
        elif any(s["key"] == a.fix_key for s in susp):
            out = bytearray(raw)
            pos = 0
            done = False
            for ln in raw.split(b"\n"):
                if ln.strip().startswith(b"#") and key_b + b"=" in ln:
                    off = ln.index(key_b)
                    out[pos + off:pos + off] = b"\n"
                    fixed += 1
                    done = True
                    break
                pos += len(ln) + 1
            if done:
                ENV.write_bytes(bytes(out))
                print(f"\n已修复：`{a.fix_key}` 拆为独立生效行（字节级插入换行，"
                      f"其它内容零改动）")
            else:
                print(f"\n✗ 未找到可修复的注释行：{a.fix_key}")
        else:
            print(f"\n--fix-key {a.fix_key}: .env 里根本没有这个键，"
                  f"请显式新增而不是「修复」")
    OUT.write_text(__import__("json").dumps(
        {"suspicious": susp, "duplicates": dupes, "empties": empties,
         "important": eff, "fixed": fixed}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
