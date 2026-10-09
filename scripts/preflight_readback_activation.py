# -*- coding: utf-8 -*-
"""[F365 2026-09-18] 学习读回路 **开启前演练**（A5 决策支持；**不开启任何东西**）。

目的：让"要不要置 `LEARNING_READBACK_ENABLED=1`"这个决定**可被量化**——
在不修改 `.env`、不重启、不写生产库的前提下，把"开启后会多什么、多多少、写到哪"算清楚。

四问：
  ① 关闭时（现状）MLTO 主脑饲料里那一项是什么？（期望空串 ⇒ 与今日逐字节一致）
  ② 开启后那一项是什么？（期望是分层教训 + 因子治理 + 回测战绩）
  ③ 主脑 prompt 会多多少字符？（对比 extras 的 60000 字符预算，给出占比）
  ④ 读取留痕会往 v7 写多少行？（按 6 小时去重窗口估算；给"每天/每车道"量级）

安全边界：全部在**本进程内**改 os.environ（进程退出即失效）；
若副本库不可用则退化为"只读真实库、不写"的估算模式。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REAL_DB = ROOT / "backend" / "data" / "factor_evolution_memory_v7.db"


def _feed():
    from backend.services.mlto.brain import _factor_system_lessons_feed
    return _factor_system_lessons_feed()


def main() -> int:
    print("=" * 84)
    print("学习读回路开启前演练（不开启、不重启、不写生产库）")
    print("=" * 84)

    # 生产库副本：让"开启后"的读取留痕落在副本上
    tmpdir = Path(tempfile.mkdtemp(prefix="preflight_"))
    copy = tmpdir / "v7.db"
    has_copy = False
    if REAL_DB.exists():
        shutil.copy2(REAL_DB, copy)
        has_copy = True
    before_sum = None
    if has_copy:
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        before_sum = con.execute("SELECT COALESCE(SUM(use_count),0) FROM v7_lessons").fetchone()[0]
        con.close()

    old_flag = os.environ.pop("LEARNING_READBACK_ENABLED", None)
    old_db = os.environ.get("V7_MEMORY_DB_PATH")
    if has_copy:
        os.environ["V7_MEMORY_DB_PATH"] = str(copy)

    try:
        # ① 关闭（现状）
        from backend.services import learning_readback as LR
        LR._POOL_CACHE.clear()
        off = _feed()
        print("\n① 关闭时（= 现状，auto 模式下 mlto 车道不启用）")
        print(f"   extras['factor_system_lessons'] 长度 = {len(off)} 字符"
              f"  内容 = {off[:60]!r}")

        # ② 开启
        os.environ["LEARNING_READBACK_ENABLED"] = "1"
        LR._POOL_CACHE.clear()
        on = _feed()
        print("\n② 开启后")
        print(f"   长度 = {len(on)} 字符，行数 = {len(on.splitlines())}")
        for ln in on.splitlines()[:12]:
            print(f"     {ln[:120]}")

        # ③ prompt 增量
        budget = int(os.environ.get("MIDLONG_BRAIN_EXTRAS_CHARS", "60000") or 60000)
        d = len(on) - len(off)
        print("\n③ 对主脑 prompt 的影响（extras 是 json.dumps 后按字符截断的）")
        print(f"   Δ = {d} 字符，占 extras 预算({budget}) 的 {d / budget:.2%}")
        # 模拟 json.dumps 后的增量（转义会让中文变长）
        j_off = len(json.dumps({"factor_system_lessons": off}, ensure_ascii=False, default=str))
        j_on = len(json.dumps({"factor_system_lessons": on}, ensure_ascii=False, default=str))
        print(f"   序列化后（extras 里的真实占用）: {j_off} → {j_on} 字符（Δ={j_on - j_off}）")
        print(f"   ⇒ 相对预算的占比 {j_on / budget:.2%}")

        # ④ 写入量
        print("\n④ 读取留痕（写 v7 的 volume）")
        if has_copy:
            con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            after_sum = con.execute("SELECT COALESCE(SUM(use_count),0) FROM v7_lessons").fetchone()[0]
            n_log = con.execute("SELECT COUNT(*) FROM v7_retrieval_log WHERE query LIKE '[%'").fetchone()[0]
            con.close()
            print(f"   副本库 use_count 合计: {before_sum} → {after_sum}"
                  f"（本次调用 += {after_sum - before_sum}）")
            print(f"   车道前缀留痕行数: {n_log}")
        dedupe_h = float(os.environ.get("LEARNING_READBACK_DEDUPE_SECONDS", "21600") or 21600) / 3600
        print(f"   去重窗口 = {dedupe_h:.1f} 小时 ⇒ 同一车道对同一批教训每 {dedupe_h:.0f} 小时只计一次")
        print(f"   估算写入量：每车道每天 ≤ {24 / max(dedupe_h, 0.1) * 6:.0f} 行 UPDATE + 同量 INSERT"
              f"（MLTO 决策循环 45 秒一轮，但受去重窗口限制）")

        # ⑤ 关掉后必须回到现状（自证演练本身无副作用）
        os.environ.pop("LEARNING_READBACK_ENABLED", None)
        LR._POOL_CACHE.clear()
        back = _feed()
        print(f"\n⑤ 演练复位自检：关闭后长度 = {len(back)} 字符"
              f" ⇒ {'已复位（无副作用）' if back == off else '**未复位，需排查**'}")
    finally:
        if old_flag is not None:
            os.environ["LEARNING_READBACK_ENABLED"] = old_flag
        else:
            os.environ.pop("LEARNING_READBACK_ENABLED", None)
        if old_db is None:
            os.environ.pop("V7_MEMORY_DB_PATH", None)
        else:
            os.environ["V7_MEMORY_DB_PATH"] = old_db
        # 生产库未被改动
        if has_copy and REAL_DB.exists():
            con = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
            real_sum = con.execute("SELECT COALESCE(SUM(use_count),0) FROM v7_lessons").fetchone()[0]
            con.close()
            print(f"   生产库 use_count 合计 = {real_sum}"
                  f"（演练前副本起点 {before_sum}）"
                  f" ⇒ {'生产库未改动' if real_sum == before_sum else '**生产库被改动，需排查**'}")
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
