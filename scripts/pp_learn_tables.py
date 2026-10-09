# -*- coding: utf-8 -*-
"""[2026-10-09 进化重挂] ping-pong 学习表生产者：rt_bp 口径，不猜方向。

替代 h817_flow_trainer(方向门)与 h831_situation_table(做市口径情境表)在
ping-pong 时代的职责。产两张表，全部来自真实往返账：

  1. data/pp_symbol_stats.json   —— 选币证据：每币 PP 往返的
     n / win_rate / avg_win_bp / avg_loss_bp / avg_bp（来自 lane_ledger
     meta_json->>'rt_bp'，与 pp_scoreboard 同口径，起点取 pp_era_start_ts）。
  2. data/pp_situation_last.json —— 桶级状态门：按 (币, 进场侧,
     价差档 × 前档量档) 分桶的 n / win_rate / avg_win_bp / avg_loss_bp
     （来自 flow_roundtrip_log.jsonl 的 strategy=PP 行 + 入场语境字段）。

消费方：runner._refresh_flow_universe（选币）与 pingpong_decision（桶门）。
失败静默：任何一步失败 ⇒ 不写文件（保持旧表，fail-open）。
用法：python scripts/pp_learn_tables.py
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from backend.services.market_maker.pp_situation import bucket_key  # noqa: E402
import psycopg  # noqa: E402

LANE = "mm_asterdex"
MARKER = ROOT / "data" / "pp_era_start_ts.txt"
RT_LOG = ROOT / "data" / "flow_roundtrip_log.jsonl"


def _read_env_dsn() -> str:
    """自解析 .env 的 DATABASE_URL（不依赖 scripts 包：backend 导入后会把
    backend/ 顶到 sys.path 前，scripts 命名空间包会被遮蔽）。"""
    url = ""
    try:
        for _line in (ROOT / ".env").read_text(encoding="utf-8",
                                               errors="replace").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            if _k.strip() == "DATABASE_URL":
                url = _v.strip().strip('"').strip("'")
                break
    except Exception:  # noqa: BLE001
        pass
    for _j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(_j, "")
    return url


def _era_start() -> float:
    try:
        return float(MARKER.read_text(encoding="utf-8").strip() or 0.0)
    except Exception:  # noqa: BLE001
        return time.time() - 24 * 3600.0


def _stats(rows: list) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "avg_win_bp": 0.0,
                "avg_loss_bp": 0.0, "avg_bp": 0.0}
    wins = [v for v in rows if v > 0]
    losses = [v for v in rows if v < 0]
    return {
        "n": n,
        "win_rate": round(len(wins) / n, 4),
        "avg_win_bp": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_bp": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "avg_bp": round(sum(rows) / n, 2),
    }


def symbol_stats() -> dict:
    out: dict = {}
    try:
        with psycopg.connect(_read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute("""
                SELECT symbol, (meta_json->>'rt_bp')::float
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= to_timestamp(%s)
                  AND meta_json->>'rt_bp' IS NOT NULL
            """, (LANE, _era_start()))
            rows = cur.fetchall()
    except Exception as e:
        print(f"  [symbol_stats] DB 读取失败(不写文件): {e}")
        return {}
    per: dict = {}
    for sym, v in rows:
        s = str(sym or "?").upper()
        if s.endswith("USDT"):
            s = s[:-4]
        per.setdefault(s, []).append(float(v or 0.0))
    for sym, vals in per.items():
        out[sym] = _stats(vals)
    return out


def situation_table() -> dict:
    out: dict = {}
    try:
        lines = RT_LOG.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        print(f"  [situation] 往返账读取失败(不写文件): {e}")
        return {}
    era0 = _era_start()
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if str(r.get("strategy") or "") != "PP" or r.get("y_bp") is None:
            continue
        try:
            if float(r.get("ts") or 0.0) < era0:
                continue
        except (TypeError, ValueError):
            continue
        sym = str(r.get("symbol") or "?").upper()
        side = str(r.get("entry_side") or "")
        if side not in ("buy", "sell"):
            side = "buy"
        key = bucket_key(float(r.get("entry_spread_bp") or 0.0),
                         float(r.get("entry_front_usd") or 0.0))
        coin = out.setdefault(sym, {"buy": {}, "sell": {}})
        coin.setdefault(side, {}).setdefault(key, []).append(float(r["y_bp"]))
    doc: dict = {"coins": {}}
    for sym, sides in out.items():
        for side, buckets in sides.items():
            for key, rows in buckets.items():
                doc["coins"].setdefault(sym, {}).setdefault(side, {})[key] = _stats(rows)
    return doc


def main() -> int:
    ts = time.time()
    sym = symbol_stats()
    # [2026-10-09 修] **空也要写**：pp_symbol_stats.json 存在且新鲜是 PP 选币
    # 路径的开关（runner 只看文件新鲜度）；不写 ⇒ 旧 AI 换币链继续接管 ⇒
    # 每 30 秒换币把 ping-pong 持仓换出宇宙 ⇒ orphan_taker 吃单砍仓。
    # 空表语义 = 「暂无 PP 证据 ⇒ 冻结现名单，不换币」（fail-open）。
    (ROOT / "data" / "pp_symbol_stats.json").write_text(
        json.dumps({"ts": ts, "lane": LANE, "coins": sym},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    n_rt = sum(int(v.get("n") or 0) for v in sym.values())
    print(f"  ✓ pp_symbol_stats.json：{len(sym)} 币 / {n_rt} 个 rt_bp 往返")
    sit = situation_table()
    n_bk = sum(len(sides.get("buy") or {}) + len(sides.get("sell") or {})
               for sides in sit.get("coins", {}).values())
    (ROOT / "data" / "pp_situation_last.json").write_text(
        json.dumps({"ts": ts, "lane": LANE, **sit}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"  ✓ pp_situation_last.json：{len(sit.get('coins') or {})} 币 / {n_bk} 桶")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
