#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补丁: scalp_loop 的 pwin 计算改为 DB 兜底 + 日志带 pwin 值(2026-08-25)。"""
import shutil

PATH = "backend/services/full_auto/loops/scalp_loop.py"
BAK = PATH + ".bak_20260825b"
shutil.copy(PATH, BAK)
print("backup ->", BAK)

src = open(PATH, encoding="utf-8").read()

old = '''                    # P0-4A：meta 影子概率（usable 与否都记，决策仍不读）
                    try:
                        from backend.services.scalp_meta_trainer import predict_win_prob
                        _meta_feats = {
                            "factor_score": float(_sig.factor_score or 0),
                            "direction": str(_sig.direction or ""),
                            "symbol": str(sym or ""),
                            **{k: v for k, v in _snap.items() if not isinstance(v, (dict, list))},
                        }
                        _mp = predict_win_prob(_meta_feats, require_usable=False)
                        if _mp is not None:
                            _snap["meta_p_win"] = round(float(_mp), 6)
                        # 阶段1修正：仲裁输入与回放证据同源（回放的 meta_p_win 全部由
                        # require_usable=False 的影子模型产出，315k 笔分桶 wr 单调、
                        # pwin>=0.55 桶净 +5.09）。usable 门控（top30% 过滤仍为负即否）
                        # 与 0.55 高桶不匹配，故不以其为准；模型文件缺失仍 hold（见 arbiter）。
                        # v3: 合并 15m K线趋势特征(入场时刻可得,零 DB 开销;
                        # 训练同源: scalp_meta_trainer.compute_kline_feats)
                        _kline_feats = {}
                        try:
                            from backend.services.scalp_meta_trainer import compute_kline_feats
                            _kline_feats = compute_kline_feats(_md.get("klines_15m"))
                        except Exception:
                            pass
                        _meta_pwin = predict_win_prob(
                            _meta_feats, require_usable=False, kline_feats=_kline_feats,
                        )
                    except Exception:
                        pass'''

new = '''                    # P0-4A：meta 概率（影子 + 仲裁同源，v3 含 K线趋势特征）
                    try:
                        from backend.services.scalp_meta_trainer import predict_win_prob
                        _meta_feats = {
                            "factor_score": float(_sig.factor_score or 0),
                            "direction": str(_sig.direction or ""),
                            "symbol": str(sym or ""),
                            **{k: v for k, v in _snap.items() if not isinstance(v, (dict, list))},
                        }
                        # v3: 15m K线趋势特征。优先 _md(实时);数据中心判过期返回空时
                        # 直查 alpha_market DB 兜底(120s 缓存,与训练同源)。
                        _kline_feats = {}
                        try:
                            from backend.services.scalp_meta_trainer import compute_kline_feats
                            _kline_feats = compute_kline_feats(_md.get("klines_15m"))
                        except Exception:
                            pass
                        if not _kline_feats:
                            try:
                                from backend.services.scalp_meta_trainer import kline_feats_from_db_cached
                                _kline_feats = kline_feats_from_db_cached(sym)
                            except Exception:
                                pass
                        _mp = predict_win_prob(
                            _meta_feats, require_usable=False, kline_feats=_kline_feats,
                        )
                        if _mp is not None:
                            _snap["meta_p_win"] = round(float(_mp), 6)
                        _meta_pwin = _mp
                    except Exception:
                        pass'''
assert src.count(old) == 1, f"count={src.count(old)}"
src = src.replace(old, new)

# 日志带 pwin 值(诊断可见性)
old2 = '''                        logger.info(
                            "[ScalpRouter独立] %s pwin仲裁%s: %s",
                            sym, _arb.action, _arb.reason,
                        )'''
new2 = '''                        logger.info(
                            "[ScalpRouter独立] %s pwin仲裁%s: %s (pwin=%s)",
                            sym, _arb.action, _arb.reason, _arb.tags.get("pwin"),
                        )'''
assert src.count(old2) == 1, f"count2={src.count(old2)}"
src = src.replace(old2, new2)

open(PATH, "w", encoding="utf-8").write(src)
print("patched OK")
