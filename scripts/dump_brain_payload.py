# -*- coding: utf-8 -*-
"""把 MLTO 主脑 extras 载荷**逐键全文**落盘，供人逐字阅读（纪律 24）。

为什么需要：§38.2 的载荷表里 `chart_review` 曾标"未查"、"其余 16 键"标"—"，
而 §38.3 却写了"剩下的一半多本次未发现问题" —— 那是**假设**，不是读数。
本脚本把每个键的完整内容写成独立文件 + 一份索引（字符数/是否结构化），
让"没发现问题"这句话**有据可依**（或推翻它）。

只读：不写 DB、不改 env、不改任何生产文件；产物落在 logs/payload_dump_<ts>/。
用法：backend\\.venv\\Scripts\\python.exe scripts\dump_brain_payload.py [SYMBOL] [TIER]
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYMBOL = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()
TIER = sys.argv[2] if len(sys.argv) > 2 else "mid"


def _text_of(v) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, indent=1, default=str)


def main() -> int:
    from backend.services.mlto import brain as B

    sid = "probe"
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        db.execute(text("SET app.is_admin='on'"))
        row = db.execute(text("SELECT session_id FROM full_auto_sessions "
                              "ORDER BY id DESC LIMIT 1")).fetchone()
        db.close()
        if row:
            sid = str(row[0])
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 取 session_id 失败，使用占位: {exc}")

    feed = B.build_feed(symbol=SYMBOL, tier=TIER, session_id=sid, market_summary=None)
    extras = feed.get("extras") or {}
    core = {k: v for k, v in feed.items() if k != "extras"}

    out = ROOT / "logs" / f"payload_dump_{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for k in sorted(extras, key=lambda x: -len(_text_of(extras[x]))):
        t = _text_of(extras[k])
        (out / f"extras__{k}.txt").write_text(t, encoding="utf-8")
        rows.append((k, len(t), type(extras[k]).__name__, t.count("\n") + 1))
    total = sum(r[1] for r in rows)

    core_txt = _text_of(core)
    (out / "_core_feed.txt").write_text(core_txt, encoding="utf-8")

    lines = [
        f"# 载荷全文转储  symbol={SYMBOL} tier={TIER} session={sid}",
        f"# extras 键数={len(rows)}  extras 合计={total} 字符   core={len(core_txt)} 字符",
        "",
        "| 键 | 字符 | 占比 | 类型 | 行数 | 文件 |",
        "|---|---|---|---|---|---|",
    ]
    for k, n, ty, ln in rows:
        lines.append(f"| `{k}` | {n} | {n / max(total, 1):.1%} | {ty} | {ln} | "
                     f"extras__{k}.txt |")
    lines.append("")
    lines.append(f"**合计 extras = {total} 字符**（`MIDLONG_BRAIN_EXTRAS_CHAR_BUDGET`=60000）")
    (out / "_INDEX.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"已落盘: {out}")
    print(f"extras 键数={len(rows)}  合计={total} 字符  core={len(core_txt)} 字符")
    for k, n, ty, ln in rows[:20]:
        print(f"  {n:>6}  {n / max(total, 1):>5.1%}  {ty:<5} {ln:>4}行  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
