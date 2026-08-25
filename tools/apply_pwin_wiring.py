#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性补丁: 把 pwin 主轴仲裁器接入 scalp_loop(2026-08-25)。

改动 4 处,全部精确字符串替换,任何一处不匹配即中止(不改文件)。
回滚: git checkout backend/services/full_auto/loops/scalp_loop.py
"""
import shutil, sys

PATH = "backend/services/full_auto/loops/scalp_loop.py"
BAK = PATH + ".bak_20260825_pwin_wiring"
shutil.copy(PATH, BAK)
print("backup ->", BAK)

src = open(PATH, encoding="utf-8").read()

# 编辑1: _meta_feats 补 symbol(regime 状态查找)
old1 = '''                        _meta_feats = {
                            "factor_score": float(_sig.factor_score or 0),
                            "direction": str(_sig.direction or ""),
                            **{k: v for k, v in _snap.items() if not isinstance(v, (dict, list))},
                        }'''
new1 = '''                        _meta_feats = {
                            "factor_score": float(_sig.factor_score or 0),
                            "direction": str(_sig.direction or ""),
                            "symbol": str(sym or ""),
                            **{k: v for k, v in _snap.items() if not isinstance(v, (dict, list))},
                        }'''
assert src.count(old1) == 1, f"edit1 count={src.count(old1)}"
src = src.replace(old1, new1)

# 编辑2: 计算 15m K线趋势特征并传入 predict_win_prob
old2 = '''                        _meta_pwin = predict_win_prob(_meta_feats, require_usable=False)'''
new2 = '''                        # v3: 合并 15m K线趋势特征(入场时刻可得,零 DB 开销;
                        # 训练同源: scalp_meta_trainer.compute_kline_feats)
                        _kline_feats = {}
                        try:
                            from backend.services.scalp_meta_trainer import compute_kline_feats
                            _kline_feats = compute_kline_feats(_md.get("klines_15m"))
                        except Exception:
                            pass
                        _meta_pwin = predict_win_prob(
                            _meta_feats, require_usable=False, kline_feats=_kline_feats,
                        )'''
assert src.count(old2) == 1, f"edit2 count={src.count(old2)}"
src = src.replace(old2, new2)

# 编辑3: 仲裁器接入(在 ScalpExecutionGate 之前)
old3 = '''            if _sig.action not in ("buy", "sell"):
                continue

            # ── ScalpExecutionGate 统一规则门 ──'''
new3 = '''            if _sig.action not in ("buy", "sell"):
                continue

            # ── pwin 主轴仲裁(2026-08-25 接线,decision_fusion_arbiter)──
            # 依据: 回放 pwin>=0.55 桶 59.2% 胜率净 +5.09;factor_score>=70 桶
            # 净 -332.55 全场最差。v3 元模型(OOS AUC 0.623, usable=true)为 pwin
            # 唯一来源;factor_score 仅并列参考,不再单独决定入场/仓位。
            # FUSION_MODE=factor 时本块不生效(行为等价 8/23 前)。
            _pwin_size_mult = 1.0
            try:
                from backend.services.decision_fusion_arbiter import (
                    decide_scalp, FUSION_MODE as _FM,
                )
                if _FM != "factor":
                    _arb = decide_scalp(
                        pwin=_meta_pwin,
                        factor_score=float(_sig.factor_score or 0),
                        direction=str(_sig.direction or "neutral"),
                        tp_pct=float(_sig.tp_pct or 0),
                        sl_pct=float(_sig.sl_pct or 0),
                    )
                    _scalp_factor["pwin_arbiter"] = _arb.to_dict()
                    if not _arb.allowed:
                        logger.info(
                            "[ScalpRouter独立] %s pwin仲裁%s: %s",
                            sym, _arb.action, _arb.reason,
                        )
                        _bump_block("pwin_arbiter")
                        continue
                    if _arb.size_mult > 0:
                        _pwin_size_mult = float(_arb.size_mult)
            except Exception as _arb_err:
                logger.debug(
                    "[ScalpRouter独立] %s pwin仲裁跳过(降级放行): %s", sym, _arb_err,
                )

            # ── ScalpExecutionGate 统一规则门 ──'''
assert src.count(old3) == 1, f"edit3 count={src.count(old3)}"
src = src.replace(old3, new3)

# 编辑4: 仓位乘数接上 pwin 档位
old4 = '''            _size_mult = _liquidity_mult'''
new4 = '''            _size_mult = _liquidity_mult * _pwin_size_mult'''
assert src.count(old4) == 1, f"edit4 count={src.count(old4)}"
src = src.replace(old4, new4)

open(PATH, "w", encoding="utf-8").write(src)
print("patched OK")
