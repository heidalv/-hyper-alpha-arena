# -*- coding: utf-8 -*-
"""K线图表包渲染（多模态分析输入，P1 2026-09-05）。

目的：让 MiniMax / GLM 的视觉能力参与趋势判断 —— 图承载「结构/形态」
（蜡烛、影线、量、EMA9/21、最后价），精确数字一律走 context pack 文本，
禁止模型从图上读数（视觉读数会幻觉）。

工程约束：
- PIL 纯手绘：零新依赖（venv 无 matplotlib），进程内确定性渲染，
  同一数据必得同一图 → 可回测、可复现、缓存安全。
- 按 (symbol, tf, 最后一根 bar 时间戳) 文件缓存；bar 未收盘不重渲，
  与 5m/15m 级分析任务天然对齐（4h/1d/1w 图稳定数小时）。
- 渲染尺寸固定（760×480），多周期各一张，作为一个「图包」整体发给网关。
"""
from __future__ import annotations

import base64
import io
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join("data", "chart_cache")

# 与前端/常规行情配色一致：涨绿跌红
_UP = (38, 166, 154)
_DOWN = (239, 83, 80)
_GRID = (238, 238, 238)
_TEXT = (51, 51, 51)
_EMA9 = (41, 98, 255)
_EMA21 = (255, 152, 0)
_LASTPX = (120, 120, 120)

_W, _PRICE_H, _VOL_H = 760, 350, 90
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 6, 62, 26, 6


def _font(size: int):
    try:
        from PIL import ImageFont

        return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
    except Exception:
        try:
            from PIL import ImageFont

            return ImageFont.load_default()
        except Exception:
            return None


def _ema(values: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if not values:
        return out
    k = 2.0 / (n + 1.0)
    prev: Optional[float] = None
    for i, v in enumerate(values):
        prev = v if prev is None else (v * k + prev * (1 - k))
        if i >= n - 1:
            out[i] = prev
    return out


def _norm_bars(raw: Sequence[Dict[str, Any]]) -> List[Dict[str, float]]:
    """统一 kline dict → (o,h,l,c,v,ts) 浮点。幂等：兼容 open/open_price/o 三种键名
    （render_candles 与 chart_pack_for_symbol 都会调用，重复归一化不得丢数据）。"""
    out: List[Dict[str, float]] = []
    for r in raw or []:
        try:
            o = r.get("open", r.get("open_price", r.get("o")))
            h = r.get("high", r.get("high_price", r.get("h")))
            l = r.get("low", r.get("low_price", r.get("l")))
            c = r.get("close", r.get("close_price", r.get("c")))
            if o is None or h is None or l is None or c is None:
                continue
            out.append({
                "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                "v": float(r.get("volume", r.get("v")) or 0),
                "ts": float(r.get("timestamp", r.get("ts")) or 0),
            })
        except (TypeError, ValueError):
            continue
    return out


def render_candles(bars: Sequence[Dict[str, Any]], *, title: str) -> bytes:
    """渲染单图 PNG（价格蜡烛 + EMA9/21 + 成交量 + 右侧价格刻度）。"""
    from PIL import Image, ImageDraw

    b = _norm_bars(bars)
    n = len(b)
    img = Image.new("RGB", (_W, _PRICE_H + _VOL_H + _PAD_T + _PAD_B + 10), "white")
    d = ImageDraw.Draw(img)
    f14, f11 = _font(14), _font(11)

    d.text((_PAD_L + 2, 4), title, fill=_TEXT, font=f14)
    if n < 5:
        d.text((_PAD_L + 2, _PRICE_H // 2), "no data", fill=_DOWN, font=f14)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    plot_w = _W - _PAD_L - _PAD_R
    price_top, price_bot = _PAD_T, _PAD_T + _PRICE_H
    vol_top, vol_bot = price_bot + 8, price_bot + 8 + _VOL_H

    his = [x["h"] for x in b]
    los = [x["l"] for x in b]
    pmax, pmin = max(his), min(los)
    span = (pmax - pmin) or 1.0
    # 上下各留 3% 空隙，EMA 与刻度不贴边
    pmax, pmin = pmax + span * 0.03, pmin - span * 0.03
    span = pmax - pmin

    def py(p: float) -> float:
        return price_bot - (p - pmin) / span * (price_bot - price_top)

    def bx(i: int) -> float:
        return _PAD_L + (i + 0.5) * plot_w / n

    cw = max(2, int(plot_w / n * 0.62))

    # 网格 + 价格刻度（5 档）
    for gi in range(5):
        p = pmin + span * gi / 4.0
        y = py(p)
        d.line((_PAD_L, y, _PAD_L + plot_w, y), fill=_GRID, width=1)
        d.text((_PAD_L + plot_w + 4, y - 6), f"{p:,.2f}", fill=_TEXT, font=f11)

    closes = [x["c"] for x in b]
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)

    # EMA 线（先画，蜡烛压在上面）
    for series, color in ((ema9, _EMA9), (ema21, _EMA21)):
        pts = [(bx(i), py(v)) for i, v in enumerate(series) if v is not None]
        if len(pts) >= 2:
            d.line(pts, fill=color, width=2)

    # 蜡烛
    for i, x in enumerate(b):
        cx = bx(i)
        up = x["c"] >= x["o"]
        color = _UP if up else _DOWN
        d.line((cx, py(x["h"]), cx, py(x["l"])), fill=color, width=1)
        top, bot = py(max(x["o"], x["c"])), py(min(x["o"], x["c"]))
        if bot - top < 1:
            bot = top + 1
        d.rectangle((cx - cw / 2, top, cx + cw / 2, bot), fill=color, outline=color)

    # 最后价虚线
    last = closes[-1]
    ly = py(last)
    for sx in range(_PAD_L, _PAD_L + plot_w, 12):
        d.line((sx, ly, min(sx + 6, _PAD_L + plot_w), ly), fill=_LASTPX, width=1)
    d.text((_PAD_L + plot_w + 4, ly - 6), f"{last:,.2f}", fill=(0, 102, 204), font=f11)

    # 成交量
    vmax = max([x["v"] for x in b] or [1.0]) or 1.0
    for i, x in enumerate(b):
        hgt = x["v"] / vmax * (_VOL_H - 12)
        cx = bx(i)
        up = x["c"] >= x["o"]
        d.rectangle((cx - cw / 2, vol_bot - hgt, cx + cw / 2, vol_bot),
                    fill=_UP if up else _DOWN)
    d.text((_PAD_L + 2, vol_top - 2), "VOL", fill=_TEXT, font=f11)

    # 图例
    d.text((_PAD_L + plot_w - 210, _PAD_T + 2), "EMA9=blue EMA21=orange", fill=_TEXT, font=f11)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


_TF_BARS = {"1w": 130, "1d": 230, "4h": 110}  # 1d 230 根保证 EMA200 可算（与 context_pack 口径一致）


def chart_pack_for_symbol(
    symbol: str,
    tfs: Sequence[str] = ("1w", "1d", "4h"),
) -> Tuple[List[Dict[str, str]], List[str]]:
    """为一个币种构建多周期图包（含文件缓存）。

    返回 (images, errors)：images 元素形如
      {"b64": ..., "media_type": "image/png", "title": "BTCUSDT 1d n=130 last=..."}
    供 ModelGateway 双票与仲裁直接消费。
    """
    from backend.services.analysis.context_pack import _klines

    symbol = str(symbol).upper()
    images: List[Dict[str, str]] = []
    errors: List[str] = []
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
    except Exception as exc:
        logger.debug("[chart_service] 缓存目录创建失败: %s", exc)

    for tf in tfs:
        try:
            bars = _norm_bars(_klines(symbol, tf, _TF_BARS.get(tf, 120)))
            if len(bars) < 20:
                errors.append(f"{symbol}/{tf}: K线不足（{len(bars)}）")
                continue
            last_ts = int(bars[-1]["ts"])
            cache = os.path.join(_CACHE_DIR, f"{symbol}_{tf}_{last_ts}.png")
            png: Optional[bytes] = None
            if os.path.exists(cache):
                try:
                    with open(cache, "rb") as fh:
                        png = fh.read()
                except Exception:
                    png = None
            if png is None:
                png = render_candles(
                    bars,
                    title=f"{symbol} {tf} bars={len(bars)} close={bars[-1]['c']:,.4f}".rstrip("0").rstrip("."),
                )
                try:
                    with open(cache, "wb") as fh:
                        fh.write(png)
                except Exception:
                    pass
            images.append({
                "b64": base64.b64encode(png).decode(),
                "media_type": "image/png",
                "title": f"{symbol} {tf} (bars={len(bars)}, last_close={bars[-1]['c']:,.6g})",
            })
        except Exception as exc:
            errors.append(f"{symbol}/{tf}: {exc}")
            logger.warning("[chart_service] %s/%s 渲染失败: %s", symbol, tf, exc)
    if not images and not errors:
        errors.append(f"{symbol}: 全部周期无数据")
    return images, errors


def pack_age_hint() -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime())


# ---------------------------------------------------------------- deep kline text
def _rsi(closes: Sequence[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return round(100 - 100 / (1 + rs), 1)


def _macd_hist(closes: Sequence[float]) -> Optional[float]:
    if len(closes) < 35:
        return None
    e12 = _ema(closes, 12)
    e26 = _ema(closes, 26)
    if e12[-1] is None or e26[-1] is None:
        return None
    # 信号线取 EMA9 的简化：用差值序列算
    diffs = [float(a - b) for a, b in zip([x for x in e12 if x is not None], [x for x in e26 if x is not None])][-9:]
    sig = diffs[-1] if diffs else 0.0
    for v in diffs:
        sig = sig * 8 / 9 + v / 9
    return round((e12[-1] - e26[-1]) - sig, 6)


def _atr_pct(bars: List[Dict[str, float]], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        h, l = bars[i]["h"], bars[i]["l"]
        pc = bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return round(sum(trs) / n / bars[-1]["c"] * 100, 2)


def _swing_structure(bars: List[Dict[str, float]], k: int = 3) -> str:
    """最近若干摆动点的高低结构（HH/HL/LH/LL 序列）。"""
    swings: List[Tuple[str, float]] = []
    for i in range(k, len(bars) - k):
        hi = all(bars[i]["h"] >= bars[j]["h"] for j in range(i - k, i + k + 1) if j != i)
        lo = all(bars[i]["l"] <= bars[j]["l"] for j in range(i - k, i + k + 1) if j != i)
        if hi:
            swings.append(("H", bars[i]["h"]))
        elif lo:
            swings.append(("L", bars[i]["l"]))
    tags = []
    kinds = [s[0] for s in swings]
    for idx in range(1, len(swings)):
        kind, val = swings[idx]
        prev_same = [v for t, v in swings[:idx] if t == kind]
        if not prev_same:
            continue
        tags.append(("HH" if kind == "H" else "HL") if val > prev_same[-1] else ("LH" if kind == "H" else "LL"))
    return "→".join(tags[-5:]) if tags else "n/a"


def deep_kline_text(symbol: str, tfs: Sequence[str] = ("1w", "1d", "4h")) -> Tuple[str, List[str]]:
    """深度K线数据包（纯文本、全数值）：图审任务的数据主体。

    [2026-09-05] 用户定位：K线**数据**深度分析是重点、图形为辅；GLM 承担深度票。
    与图表共用同一份 bars（文本与图形所见一致），产出多周期指标/结构/波动/量能表。
    """
    from backend.services.analysis.context_pack import _klines

    symbol = str(symbol).upper()
    errs: List[str] = []
    lines = [f"【{symbol} 深度K线数据】（数值为唯一事实源；图仅辅助结构判断）"]
    for tf in tfs:
        bars = _norm_bars(_klines(symbol, tf, _TF_BARS.get(tf, 120)))
        if len(bars) < 60:
            errs.append(f"{tf}: 数据不足({len(bars)})")
            continue
        closes = [b["c"] for b in bars]
        last = closes[-1]
        ema = {n: _ema(closes, n)[-1] for n in (9, 21, 50, 200)}

        def _dist(v: Optional[float]) -> str:
            return "n/a" if v is None else f"{(last / v - 1) * 100:+.2f}%"

        n = len(closes)

        def _ret(k: int) -> Optional[float]:
            return round((closes[-1] / closes[-k] - 1) * 100, 2) if n >= k else None

        vols = [b["v"] for b in bars]
        v_mean = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1e-9
        v_std = (sum((v - v_mean) ** 2 for v in vols[-21:-1]) / 20) ** 0.5 or 1e-9
        v_z = round((vols[-1] - v_mean) / v_std, 2)
        hi90 = max(b["h"] for b in bars[-90:]) if n >= 90 else max(b["h"] for b in bars)
        lo90 = min(b["l"] for b in bars[-90:]) if n >= 90 else min(b["l"] for b in bars)
        rng = (hi90 - lo90) or 1e-9
        rsi = _rsi(closes)
        up_streak = 0
        for i in range(n - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                up_streak += 1
            else:
                break
        lines.append(
            f"[{tf}] last={last:,.6g} | 收益 1期={_ret(2)}% 5期={_ret(6)}% 20期={_ret(21)}%"
            f" | EMA9={_dist(ema[9])} EMA21={_dist(ema[21])} EMA50={_dist(ema[50])} EMA200={_dist(ema[200])}"
            f" | RSI14={rsi if rsi is not None else 'n/a'} MACD柱={_macd_hist(closes)}"
            f" | ATR14%={_atr_pct(bars)} | 量z={v_z:+.1f} 连阳={up_streak}"
            f" | 90期区间位置={(last - lo90) / rng * 100:.0f}% 回撤自90期高={(last / hi90 - 1) * 100:+.1f}%"
            f" | 摆动结构={_swing_structure(bars)}"
        )
    return "\n".join(lines), errs
