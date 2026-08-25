#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补丁: 因子路由器趋势翻转(2026-08-25)。

实证(35万已结算信号, audit_kline_trend.py):
- 15m 趋势向下时多头信号胜率 33.4%,同环境下空头 43.7%(净 +0.048%);
- 趋势向上时空头 31.2%,多头 49.4%(净 +0.057%)。
MR 因子在明确趋势里逆势发单是全场最差区。本补丁在趋势明确时把逆势
方向翻转为顺势(分数不变),由下游 pwin 仲裁器最终确认——信号准确率
从源头修正,不减交易量。弱趋势(EMA 斜率 |x|<0.0025)保持原方向。
回滚: SCALP_TREND_FLIP_ENABLED=false
"""
import shutil

PATH = "backend/services/scalp_factor_router.py"
BAK = PATH + ".bak_20260825_trendflip"
shutil.copy(PATH, BAK)
print("backup ->", BAK)

src = open(PATH, encoding="utf-8").read()

old = '''        # 1. 从 market_data 提取因子信号
        factor_score, direction, breakdown = self._extract_factor_signal(symbol, market_data)
'''
new = '''        # 1. 从 market_data 提取因子信号
        factor_score, direction, breakdown = self._extract_factor_signal(symbol, market_data)

        # 1.1 [2026-08-25] 趋势翻转: MR 因子在明确趋势里逆势发单是全场最差区
        # (多头在 15m 下行段胜率 33.4%,顺势空头 43.7%)。趋势明确时把逆势方向
        # 翻转为顺势,分数不变,由下游 pwin 仲裁器最终确认。
        # 回滚: SCALP_TREND_FLIP_ENABLED=false
        try:
            import os as _os_tf
            if _os_tf.getenv("SCALP_TREND_FLIP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on"):
                if direction in ("long", "short"):
                    from backend.services.scalp_meta_trainer import compute_kline_feats
                    _tf_kf = compute_kline_feats(market_data.get("klines_15m"))
                    if not _tf_kf:
                        try:
                            from backend.services.scalp_meta_trainer import kline_feats_from_db_cached
                            _tf_kf = kline_feats_from_db_cached(symbol)
                        except Exception:
                            _tf_kf = {}
                    _tf_es = float(_tf_kf.get("ema_slope", 0.0) or 0.0)
                    if _tf_es < -0.0025 and direction == "long":
                        direction = "short"
                        breakdown["trend_flip"] = "15m_down"
                        logger.info(
                            "[ScalpRouter] %s 趋势翻转 long→short (ema_slope=%.4f)",
                            symbol, _tf_es,
                        )
                    elif _tf_es > 0.0025 and direction == "short":
                        direction = "long"
                        breakdown["trend_flip"] = "15m_up"
                        logger.info(
                            "[ScalpRouter] %s 趋势翻转 short→long (ema_slope=%.4f)",
                            symbol, _tf_es,
                        )
        except Exception as _tf_err:
            logger.debug(f"[ScalpRouter] {symbol} 趋势翻转检查跳过: {_tf_err}")
'''
assert src.count(old) == 1, f"count={src.count(old)}"
open(PATH, "w", encoding="utf-8").write(src.replace(old, new))
print("patched OK")
