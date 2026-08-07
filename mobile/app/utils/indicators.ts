// EMA: Exponential Moving Average
export function ema(data: number[], period: number): number[] {
  const k = 2 / (period + 1)
  const result: number[] = []
  let prev = data[0]
  result.push(prev)
  for (let i = 1; i < data.length; i++) {
    prev = data[i] * k + prev * (1 - k)
    result.push(prev)
  }
  return result
}

// SMA: Simple Moving Average
export function sma(data: number[], period: number): number[] {
  const result: number[] = []
  let sum = 0
  for (let i = 0; i < data.length; i++) {
    sum += data[i]
    if (i >= period) sum -= data[i - period]
    if (i >= period - 1) result.push(sum / period)
    else result.push(NaN)
  }
  return result
}

// Standard Deviation over period
export function stddev(data: number[], period: number, smaVals?: number[]): number[] {
  const sma_ = smaVals || sma(data, period)
  const result: number[] = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1 || isNaN(sma_[i])) { result.push(NaN); continue }
    let sumSq = 0
    for (let j = i - period + 1; j <= i; j++) sumSq += (data[j] - sma_[i]) ** 2
    result.push(Math.sqrt(sumSq / period))
  }
  return result
}

// ── MACD ──
export interface MACDResult { dif: number[]; dea: number[]; histogram: number[] }

export function calcMACD(closes: number[], fast = 12, slow = 26, signal = 9): MACDResult {
  const emaFast = ema(closes, fast)
  const emaSlow = ema(closes, slow)
  const dif = emaFast.map((v, i) => v - emaSlow[i])
  const dea = ema(dif, signal)
  const histogram = dif.map((v, i) => (v - dea[i]) * 2)
  return { dif, dea, histogram }
}

// ── KDJ ──
export interface KDJResult { k: number[]; d: number[]; j: number[] }

export function calcKDJ(highs: number[], lows: number[], closes: number[], n = 9): KDJResult {
  const k: number[] = [], d: number[] = [], j: number[] = []
  let prevK = 50, prevD = 50
  for (let i = 0; i < closes.length; i++) {
    const start = Math.max(0, i - n + 1)
    const hh = Math.max(...highs.slice(start, i + 1))
    const ll = Math.min(...lows.slice(start, i + 1))
    const rsv = (hh - ll) === 0 ? 50 : ((closes[i] - ll) / (hh - ll)) * 100
    const curK = (2 / 3) * prevK + (1 / 3) * rsv
    const curD = (2 / 3) * prevD + (1 / 3) * curK
    const curJ = 3 * curK - 2 * curD
    k.push(curK); d.push(curD); j.push(curJ)
    prevK = curK; prevD = curD
  }
  return { k, d, j }
}

// ── BOLL 布林带 ──
export interface BOLLResult { upper: number[]; mid: number[]; lower: number[] }

export function calcBOLL(closes: number[], period = 20, multiplier = 2): BOLLResult {
  const mid = sma(closes, period)
  const sd = stddev(closes, period, mid)
  const upper: number[] = [], lower: number[] = []
  for (let i = 0; i < closes.length; i++) {
    if (isNaN(mid[i]) || isNaN(sd[i])) { upper.push(NaN); lower.push(NaN); continue }
    upper.push(mid[i] + multiplier * sd[i])
    lower.push(mid[i] - multiplier * sd[i])
  }
  return { upper, mid, lower }
}

// ── RSI 相对强弱 ──
export function calcRSI(closes: number[], period = 14): number[] {
  const result: number[] = []
  let avgGain = 0, avgLoss = 0
  for (let i = 1; i <= period && i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1]
    if (diff > 0) avgGain += diff; else avgLoss += -diff
  }
  if (closes.length > period) { avgGain /= period; avgLoss /= period }
  for (let i = 0; i < closes.length; i++) {
    if (i === 0) { result.push(NaN); continue }
    if (i < period) { result.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)); continue }
    const diff = closes[i] - closes[i - 1]
    const gain = diff > 0 ? diff : 0
    const loss = diff < 0 ? -diff : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    result.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss))
  }
  return result
}

// ── ATR 平均真实波幅 ──
export function calcATR(highs: number[], lows: number[], closes: number[], period = 14): number[] {
  const tr: number[] = []
  for (let i = 0; i < closes.length; i++) {
    if (i === 0) { tr.push(highs[i] - lows[i]); continue }
    tr.push(Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1])
    ))
  }
  return sma(tr, period)
}

// ── OBV 能量潮 ──
export function calcOBV(closes: number[], volumes: number[]): number[] {
  const result: number[] = []
  let obv = 0
  for (let i = 0; i < closes.length; i++) {
    if (i === 0) { obv = volumes[i]; result.push(obv); continue }
    if (closes[i] > closes[i - 1]) obv += volumes[i]
    else if (closes[i] < closes[i - 1]) obv -= volumes[i]
    result.push(obv)
  }
  return result
}

// ── MA lines (multiple periods) ──
export interface MALines {
  ma5: number[]; ma10: number[]; ma20: number[]; ma60: number[]
  ema12: number[]; ema26: number[]
}

export function calcMALines(closes: number[]): MALines {
  return {
    ma5: sma(closes, 5),
    ma10: sma(closes, 10),
    ma20: sma(closes, 20),
    ma60: sma(closes, 60),
    ema12: ema(closes, 12),
    ema26: ema(closes, 26),
  }
}
