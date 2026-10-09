# -*- coding: utf-8 -*-
"""[h899] A:影子车道闭环 —— 学到的策略真正反哺执行。

链条:
  ① export_policy   —— ShadowLearner 的权重导出为 JSON
  ② shadow_side     —— runner 每拍用学到的策略算影子侧(轻量前向)
  ③ runner 记录     —— 影子决策逐拍写 data/evolution_shadow_log.jsonl
                       (symbol, ts, shadow_side, rule_side, 当时中价)
  ④ evaluate_shadow  —— 反事实评估:影子侧 vs 规则侧,各自的"事后 45s 漂移 y";
                       影子 ≥ 规则且三关通过 ⇒ 写 evolution_promoted.json
  ⑤ runner 晋升      —— evolution_promoted.json 存在时,方向由影子策略给
                       (explore 分支的 MM_SHADOW_POLICY 开关)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
POLICY_PATH = ROOT / "data" / "evolution_policy.json"
SHADOW_LOG = ROOT / "data" / "evolution_shadow_log.jsonl"
PROMOTED = ROOT / "data" / "evolution_promoted.json"


# ═══ ① 导出 ═══
def export_policy(params: Dict[str, np.ndarray], path: Optional[Path] = None) -> Path:
    p = path or POLICY_PATH
    doc = {"ts": time.time(), "hidden": int(params["w1"].shape[1]),
           "w1": params["w1"].tolist(), "b1": params["b1"].tolist(),
           "w2": params["w2"].tolist(), "b2": params["b2"].tolist()}
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# ═══ ② 影子侧(轻量前向;**worker 的 .runtime 解释器没有 numpy/torch ⇒ 纯 Python**) ═══
def _load(path: Optional[Path] = None) -> Optional[dict]:
    p = path or POLICY_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tanh(x: float) -> float:
    if x > 20.0:
        return 1.0
    if x < -20.0:
        return -1.0
    import math
    e2 = math.exp(2.0 * x)
    return (e2 - 1.0) / (e2 + 1.0)


def _forward(obs, d: dict) -> Optional[int]:
    """支持 2 层或 3 层 MLP(与导出格式对齐)。obs 缺维补 0。"""
    obs_dim = len(d["w1"])
    o = list(obs)[:obs_dim] + [0.0] * max(0, obs_dim - len(list(obs)))
    h = [_tanh(sum(o[i] * d["w1"][i][j] for i in range(obs_dim)) + d["b1"][j])
         for j in range(len(d["b1"]))]
    if "w2" in d and isinstance(d["w2"], list) and d["w2"] \
            and isinstance(d["w2"][0], list) \
            and len(d["w2"][0]) == len(h):
        # 3 层:线性 → tanh → 线性
        h2 = [_tanh(sum(h[i] * d["w2"][i][j] for i in range(len(h))) + d["b2"][j])
              for j in range(len(d["b2"]))]
        logits = [sum(h2[j] * d["w3"][j][k] for j in range(len(h2))) + d["b3"][k]
                  for k in range(len(d["b3"]))]
    else:
        logits = [sum(h[j] * d["w2"][j][k] for j in range(len(h))) + d["b2"][k]
                  for k in range(len(d["b2"]))]
    return max(range(len(logits)), key=lambda k: logits[k])


def shadow_action(obs, path: Optional[Path] = None) -> Optional[int]:
    """学到的策略在 obs 上的动作(0~17)。无策略文件 ⇒ None。"""
    d = _load(path)
    if not d:
        return None
    return _forward(obs, d)


def shadow_side(obs, path: Optional[Path] = None) -> Optional[str]:
    """动作 → 侧。动作空间:侧(2)×价位档(3)×名义档(3)⇒ 0..8=多,9..17=空。"""
    a = shadow_action(obs, path)
    if a is None:
        return None
    return "buy" if a < 9 else "sell"


# ═══ ①′ TorchPPO 导出(torch 权重 → 纯 Python JSON) ═══
def export_torch_actor(actor, act_dim: int, path: Optional[Path] = None) -> Path:
    """把 TorchPPO 的 _actor(3 层 MLP:Linear→Tanh→Linear→Tanh→Linear)导出。"""
    import torch
    p = path or POLICY_PATH
    layers = list(actor.children())
    doc = {"ts": time.time(), "act_dim": int(act_dim)}
    ws, bs = [], []
    for m in layers:
        if isinstance(m, torch.nn.Linear):
            ws.append(m.weight.detach().cpu().numpy().tolist())
            bs.append(m.bias.detach().cpu().numpy().tolist())
    # w 的存法是 [out×in] ⇒ 转置成 [in][out] 供纯 Python 前向
    doc["w1"] = [list(r) for r in zip(*ws[0])] if len(ws) >= 1 else []
    doc["b1"] = bs[0] if len(bs) >= 1 else []
    doc["w2"] = [list(r) for r in zip(*ws[1])] if len(ws) >= 2 else []
    doc["b2"] = bs[1] if len(bs) >= 2 else []
    doc["w3"] = [list(r) for r in zip(*ws[2])] if len(ws) >= 3 else []
    doc["b3"] = bs[2] if len(bs) >= 3 else []
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# ═══ ③ 记录(由 runner 调用) ═══
def record_shadow(symbol: str, now_ts: float, obs: np.ndarray,
                  rule_side: str, mid: float, path: Optional[Path] = None) -> None:
    sd = shadow_side(obs, path)
    if sd is None:
        return
    row = {"ts": now_ts, "symbol": symbol, "shadow_side": sd,
           "rule_side": rule_side, "mid": float(mid)}
    with open(SHADOW_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ═══ ④ 反事实评估 + 晋升裁决 ═══
def evaluate_shadow(hours: float = 6.0) -> dict:
    """把影子决策与市场对账:每条的 y = 45s 后中价相对入场中价的符号化漂移。

    晋级三关:影子 n≥30;影子平均 y > 0;影子 y ≥ 规则 y(同拍对比)。
    """
    from backend.services.market_maker.attribution import _market_dsn
    import psycopg
    if not SHADOW_LOG.exists():
        return {"error": "no_shadow_log"}
    t0 = time.time() - hours * 3600
    rows = []
    for line in SHADOW_LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if float(r.get("ts") or 0) >= t0:
            rows.append(r)
    if len(rows) < 30:
        return {"n": len(rows), "error": "n<30"}
    sh_y, ru_y = [], []
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        for r in rows:
            sym = str(r["symbol"]).upper()
            sym2 = sym + "USDT" if "USDT" not in sym else sym
            t_ms = int(float(r["ts"]) * 1000)
            cur.execute(
                "SELECT event_ts_ms/1000.0, (bid_px+ask_px)/2.0"
                " FROM asterdex_book_ticker WHERE symbol=%s AND bid_px>0"
                " AND event_ts_ms BETWEEN %s AND %s ORDER BY event_ts_ms",
                (sym2, t_ms - 5_000, t_ms + 60_000))
            data = cur.fetchall()
            if len(data) < 30:
                continue
            mid0 = float(data[0][1])
            j = int(np.searchsorted([x[0] for x in data], data[0][0] + 45,
                                    side="right") - 1)
            y = (float(data[j][1]) / mid0 - 1.0) * 1e4
            sh_y.append(y if r["shadow_side"] == "buy" else -y)
            ru_y.append(y if r["rule_side"] == "buy" else -y)
    if len(sh_y) < 30:
        return {"n": len(sh_y), "error": "matched<30"}
    sh_m = float(np.mean(sh_y))
    ru_m = float(np.mean(ru_y))
    promoted = sh_m > 0 and sh_m >= ru_m
    doc = {"ts": time.time(), "n": len(sh_y),
           "shadow_mean_y": round(sh_m, 3), "rule_mean_y": round(ru_m, 3),
           "promoted": bool(promoted)}
    PROMOTED.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return doc


# ═══ ⑤ runner 晋升查询 ═══
def is_promoted(max_age_hours: float = 12.0) -> bool:
    if not PROMOTED.exists():
        return False
    try:
        d = json.loads(PROMOTED.read_text(encoding="utf-8"))
        return bool(d.get("promoted")) and \
            (time.time() - float(d.get("ts") or 0.0)) < max_age_hours * 3600
    except Exception:
        return False
