# P0-2 接线补丁: pwin 主轴仲裁器接入 scalp_loop(待 meta 模型 usable 后应用)
# 应用方法: 在实盘 backend/services/full_auto/loops/scalp_loop.py 中做两处修改。
# 回滚: FUSION_MODE=factor(或删除本块)即恢复 8/23 前行为。

# ── 修改 1: 在 "if _sig.action not in ('buy', 'sell'): continue" 之后、
#    "── ScalpExecutionGate 统一规则门 ──" 之前插入 ──
            # ── pwin 主轴仲裁(2026-08-25 接线,依据 decision_fusion_arbiter 回放)──
            # 回放证据: pwin>=0.55 桶胜率 59.2% 净 +5.09;factor_score>=70 桶净 -332.55。
            # factor_score 与真实胜率零相关(35 万样本校准),不再单独决定入场。
            _pwin_size_mult = 1.0
            try:
                from backend.services.decision_fusion_arbiter import decide_scalp, FUSION_MODE as _FM
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
                            f"[ScalpRouter独立] {sym} pwin仲裁{_arb.action}: {_arb.reason}"
                        )
                        _bump_block("pwin_arbiter")
                        continue
                    if _arb.size_mult > 0:
                        _pwin_size_mult = float(_arb.size_mult)
            except Exception as _arb_err:
                logger.debug(f"[ScalpRouter独立] {sym} pwin仲裁跳过(降级放行): {_arb_err}")

# ── 修改 2: 将 "_size_mult = _liquidity_mult" 改为 ──
            _size_mult = _liquidity_mult * _pwin_size_mult

# ── 修改 3(.env): 恢复回放证据参数(当前被调松)──
# FUSION_SCALP_PWIN_MIN=0.55   (现 0.45;回放: 0.45-0.55 桶净 -50.11)
# FUSION_RR_FLOOR=1.2          (现 0.9;回放: RR<1.2 结构必亏)
# FUSION_SCALP_PWIN_STRONG=0.60 保持不变
# FUSION_PWIN_MISSING_MODE=hold 保持不变(模型缺失 fail-closed,arbiter 设计)
