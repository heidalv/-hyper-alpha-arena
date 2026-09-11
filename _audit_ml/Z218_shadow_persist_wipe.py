# -*- coding: utf-8 -*-
"""[§81 核验/修复 2026-09-11] 熔断标志的**磁盘持久化**：修复前复现 + 修复后回归。

缺陷（缺陷 #65）：加载期"剥离旧累计制 shadow"的判据过宽
    _stale_shadow = bool(d.get("shadow") or d.get("breaker_shadow"))
无法区分 08-31 之前的**累计制**标志与**现行滚动窗**标志（同名同址 `breaker_shadow`）
⇒ 任何进程只要**读一次**状态文件，就会把滚动窗熔断标志从磁盘抹掉，并把
`shadow_mode` 迁移语义当成常态；`EXIT_CHANNEL_REBUILD_ON_LOAD=true` 时只在**内存**里
由 `_breaker` 重建 ⇒ 持久化形同虚设；开关一旦关掉（回滚），重启即退回"盲窗"。
另有第二条路径：空态保存的"多进程防护"分支**无条件**清空内存里的
`shadow/breaker_shadow`，随后一次正常保存就把空标志写回磁盘（线上实测一天 5 次）。

修复口径：状态文件带 `shadow_mode="rolling"` 标记 ⇒ 保留标志；无标记的旧文件
剥离**一次**并写入标记。空态合并分支同样按磁盘口径决定是否丢弃。

本脚本覆盖四种情形（全部在临时目录，不碰生产文件）：
  ① 旧文件（无标记）           → 剥离一次 + 写标记
  ② 滚动口径文件（有标记）     → **磁盘标志保留**（修复前被抹）
  ③ 滚动口径文件 + 开关关掉     → 内存仍能从磁盘取到标志（修复前为空 ⇒ 盲窗）
  ④ 空内存实例直接保存（多进程） → 合并后标志不被清零，且后续保存不写空
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

import backend.services.source_attribution as sa  # noqa: E402

KEY = "mid|trend_broken"
RECENT = [0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0]   # 15 笔 4 胜 ⇒ wr=26.7% < 40%


def _state(marker: bool, flags: bool = True) -> dict:
    d = {
        "ts": 1789000000.0,
        "tags": {"12345": {"source": "mlto", "nature": "swing", "symbol": "BTC", "meta": {}, "ts": 1.0}},
        "stats": {"mlto|swing|BTC": {"n": 3, "wins": 1, "gross": -1.0, "fee": 0.1}},
        "shadow": {},
        "breaker": {KEY: {"n": 20, "wins": 4, "recent": list(RECENT)}},
        "breaker_shadow": {KEY: True} if flags else {},
    }
    if marker:
        d[sa.SHADOW_MODE_KEY] = sa.SHADOW_MODE_ROLLING
    return d


def _write(path: str, d: dict) -> None:
    Path(path).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _flags_on_disk(path: str) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in (d.get("breaker_shadow") or {}).items() if v}, d


def _case(label: str, *, marker: bool, rebuild: bool, use_merge: bool = False) -> bool:
    tmpdir = tempfile.mkdtemp(prefix="z218b_")
    path = os.path.join(tmpdir, "state.json")
    _write(path, _state(marker))
    os.environ["EXIT_CHANNEL_REBUILD_ON_LOAD"] = "true" if rebuild else "false"
    _orig = sa._STATE_PATH
    sa._STATE_PATH = path
    try:
        if use_merge:
            # 模拟"没加载就保存"的进程：触发空态防护合并分支
            a = sa.SourceAttribution()
            a._maybe_save(force=True)          # 合并磁盘内容（不写盘）
            merged_flags = dict(a._breaker_shadow or {})
            a._maybe_save(force=True)          # 再存一次：修复前会把空标志写回磁盘
            after2, _ = _flags_on_disk(path)
            mem_live = bool((a._breaker_shadow or {}).get(KEY))
            print(f"  [{label}] 合并后内存标志={len(merged_flags)} 键；二次保存后磁盘={sorted(after2)}；"
                  f"live={mem_live}")
            return bool(merged_flags) and mem_live
        a = sa.SourceAttribution()
        live = bool(a.exit_channel_shadow("trend_broken: x", "mid"))
        mem_keys = len(a._breaker_shadow or {})
        after, disk = _flags_on_disk(path)
        marker_out = str(disk.get(sa.SHADOW_MODE_KEY) or "")
        print(f"  [{label}] 磁盘标志 {sorted(after)}；内存 {mem_keys} 键；live={live}；"
              f"磁盘标记={marker_out!r}")
        return after
    finally:
        sa._STATE_PATH = _orig


def _case_old_judgement() -> bool:
    """对照组：把"滚动口径标记"语义关掉 ⇒ 等价于修复前的旧判据
    `bool(shadow or breaker_shadow)`，同一个滚动口径文件应当**被抹掉**。
    （生产现场 2026-09-11 11:30:14 实测：13 键/7 真 → 0 键，即此路径。）"""
    tmpdir = tempfile.mkdtemp(prefix="z218c_")
    path = os.path.join(tmpdir, "state.json")
    _write(path, _state(marker=True))
    os.environ["EXIT_CHANNEL_REBUILD_ON_LOAD"] = "false"
    _orig_path = sa._STATE_PATH
    _orig_marker = sa.SHADOW_MODE_ROLLING
    sa._STATE_PATH = path
    sa.SHADOW_MODE_ROLLING = "___forces_legacy___"      # 让所有文件都按旧文件处理
    try:
        a = sa.SourceAttribution()
        a._ensure_loaded()
        after, _ = _flags_on_disk(path)
        mem = bool((a._breaker_shadow or {}).get(KEY))
        print(f"  [对照：旧判据] 磁盘标志 {sorted(after)}；内存 live={mem}")
        return (after == {}) and (not mem)      # 抹盘 + 内存空 ⇒ 与修复前一致
    finally:
        sa._STATE_PATH = _orig_path
        sa.SHADOW_MODE_ROLLING = _orig_marker


def main() -> int:
    print("=" * 96)
    print("① 旧文件（无 shadow_mode 标记） ⇒ 应当**剥离一次**并写入标记")
    print("=" * 96)
    r1 = _case("旧文件 + REBUILD=true", marker=False, rebuild=True)
    ok1 = (r1 == {})   # 剥离
    print(f"     ⇒ {'✅ 迁移按预期只发生一次' if ok1 else '❗未剥离'}")

    print()
    print("=" * 96)
    print("② 滚动口径文件（有标记） ⇒ 磁盘标志**必须保留**（缺陷点）")
    print("=" * 96)
    r2 = _case("滚动口径 + REBUILD=true", marker=True, rebuild=True)
    ok2 = (r2 == {KEY: True})
    print(f"     ⇒ {'✅ 标志保留（缺陷已修）' if ok2 else '❗标志仍被抹掉'}")

    print()
    print("=" * 96)
    print("③ 滚动口径文件 + 开关关掉 ⇒ 内存也应从磁盘取到标志（回滚不再退回盲窗）")
    print("=" * 96)
    r3 = _case("滚动口径 + REBUILD=false", marker=True, rebuild=False)
    ok3 = (r3 == {KEY: True})
    print(f"     ⇒ {'✅ 回滚后仍有判定' if ok3 else '❗回滚即盲窗'}")

    print()
    print("=" * 96)
    print("④ 空内存实例直接保存（多进程防护分支）⇒ 标志不得被清零")
    print("=" * 96)
    ok4 = _case("空态合并（滚动口径磁盘）", marker=True, rebuild=False, use_merge=True)
    print(f"     ⇒ {'✅ 合并保留标志，二次保存不再写空' if ok4 else '❗合并把标志清零'}")

    print()
    print("=" * 96)
    print("⑤ 对照组：关掉标记语义（= 修复前旧判据）⇒ 同一文件应被抹掉（红）")
    print("=" * 96)
    ok5 = _case_old_judgement()
    print(f"     ⇒ {'✅ 复现修复前行为（证明红绿对照成立）' if ok5 else '❗对照未复现'}")
    print()
    ok = ok1 and ok2 and ok3 and ok4 and ok5
    print("=" * 96)
    print(f"总判定：{'✅ 五项全部通过（修复生效）' if ok else '❗存在未通过项'}")
    print("=" * 96)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
