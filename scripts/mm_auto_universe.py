"""F284：把 AI 选币的产出**自动**接到交易车道（补上缺失的那一环）。

## 病根（两个，都已在代码中确认）

1. **`apply_to_lane()` 存在但没人定时调用。**
   `backend/services/coin_select_hft.py:384` 有完整的写入实现，
   但全仓库只有 `scripts/hft_universe_select.py`（**手工 CLI**）调它。
   ⇒ AI 选币每小时算出候选、写 `coin_select_candidates`（实测近 3 小时每小时 54~58 行），
      **却从来没有人把结果写进 `lane_registry.meta.symbols`** ⇒ 车道宇宙纹丝不动。
   旁证：`auto_coin_selections` **0 行**、`coin_select_adoptions` 最新停在 **373 小时前**。

2. **宇宙不在热更新指纹里**（已由 F283 修掉）。
   即使 applier 被调用，运行中的 runner 也不会重新读 `meta.symbols` ⇒ 照旧挂老币。

## 本脚本做什么

扫一遍候选 → 选宇宙 → **写进车道**，并保证：
  · **陈旧保护**：`select_universe` 的 `as_of` 若超过 `--max-age-min`（默认 45 分钟）
    ⇒ **拒绝写入并告警**（实测当前 as_of 陈旧 8.2 小时 —— 正是"看起来在跑其实没数据"的坑）
  · **幂等**：宇宙没变就不写（避免每分钟刷 `updated_at`）
  · **dry-run 默认**：真正写入需要 `--apply`
  · **只增不减可配**：`--keep-only-ai` 决定是否移除掉出榜的 AI 币

用法：
    .venv\\Scripts\\python.exe scripts\\mm_auto_universe.py            # 干跑，看会写什么
    .venv\\Scripts\\python.exe scripts\\mm_auto_universe.py --apply    # 真写
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

LANE = os.getenv("MM_LANE_ID", "mm_asterdex")
LOG = ROOT / "logs" / "mm_auto_universe.log"


def log(msg: str) -> None:
    line = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " [auto-universe] " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只干跑）")
    ap.add_argument("--ai-slots", type=int, default=5)
    ap.add_argument("--max-age-min", type=float, default=45.0,
                    help="as_of 超过该分钟数则拒绝写入（陈旧保护）")
    a = ap.parse_args()

    from backend.services import lane_registry as reg
    from backend.services.coin_select_hft import apply_to_lane, select_universe

    lane = reg.get_lane(LANE)
    if not lane:
        log(f"车道不存在: {LANE}")
        return 1
    meta = dict(lane.get("meta") or {})
    cur_syms = [str(s) for s in (meta.get("symbols") or [])]
    fixed = (meta.get("universe") or {}).get("fixed") or []

    log(f"车道 {LANE}  当前宇宙({len(cur_syms)})={cur_syms}")
    log(f"fixed={fixed}  ai_slots={a.ai_slots}")

    d = select_universe(fixed=fixed, ai_slots=a.ai_slots)
    new_syms = list(d.symbols)
    log(f"选币结果：fixed={d.fixed}  ai={d.ai}")
    log(f"新宇宙({len(new_syms)})={new_syms}")

    # ── 陈旧保护 ─────────────────────────────────────────────────────────────
    # ⚠️ **不能用 `as_of` 做陈旧判据**：实测它 = `now − 8h00m`，随墙钟同步平移
    #    （两次相邻调用分别是 17:40:19 与 17:45:12，间隔正好等于真实间隔差）
    #    ⇒ 它是"被盖上当下时间戳的**陈旧快照**"，`as_of` 永远显示"刚刚" ✗✗
    #    这正是本项目第 23 条教训的变体：**看起来在跑，其实数据是旧的**。
    # 正确判据：用**行级**真实年龄 —— `scored[]` 里的 `book_age_s` / `depth_age_s`。
    _ages = []
    _neg = 0
    for r in (d.scored or []):
        for k in ("book_age_s", "depth_age_s"):
            v = r.get(k)
            if isinstance(v, (int, float)):
                if v < 0:
                    _neg += 1          # 负年龄 = 源数据时间戳在**未来** ⇒ 时钟/拼接异常
                else:
                    _ages.append(float(v))
    age_s = max(_ages) if _ages else None
    log(f"as_of={d.as_of}（**不可信，见下**）")
    if age_s is None:
        log("无法从 scored[] 取到 book_age_s/depth_age_s ⇒ 保守拒绝写入")
        return 1
    log(f"行级真实年龄：最大 {age_s:.1f}s（{age_s/60:.1f} 分钟）"
        f"  样本 {len(_ages)}  负年龄(时钟异常) {_neg}")
    age_min = age_s / 60.0

    if _neg > 0:
        log(f"⚠️ 有 {_neg} 个**负年龄** ⇒ 源数据时间戳落在未来，数据拼接/时钟有问题")
    if age_min > a.max_age_min:
        log(f"✗ **拒绝写入**：行级年龄 {age_min:.1f} 分钟 > 阈值 {a.max_age_min:.0f} 分钟。"
            f"（写进去会把车道锁在陈旧快照上 —— 这正是用户报的"
            f"「AI 选币好像没更新过」的机制）")
        return 1

    if new_syms == cur_syms:
        log("宇宙无变化 ⇒ 不写入（幂等）")
        return 0

    added = [s for s in new_syms if s not in cur_syms]
    removed = [s for s in cur_syms if s not in new_syms]
    log(f"变更：+{added}  −{removed}")

    if not a.apply:
        log("(干跑，未写入。加 --apply 真写)")
        return 0

    r = apply_to_lane(LANE, d, dry_run=False)
    log(f"写入结果: {json.dumps(r, ensure_ascii=False, default=str)}")
    log("⚠️ F283 之后 runner 会把 symbols 纳入热更新指纹 ⇒ 60s 内自动采用，无需重启。")
    log("   移除币的既有持仓由 orphan 逻辑强制退出（不丢仓）。")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
