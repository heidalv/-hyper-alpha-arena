import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, AreaSeries } from 'lightweight-charts'
import PacmanLoader from '../ui/pacman-loader'
import { formatChartLocalTime, formatChartTime } from '../../lib/dateTime'
import { getSymbolDecimals } from '../../lib/priceFormat'
import { useChartPatterns, useChartSRLevels, useVolumeAnomalies } from './ChartCore'

interface TradingViewChartProps {
  symbol: string
  period: string
  chartType: 'candlestick' | 'line' | 'area'
  selectedIndicators: string[]
  selectedFlowIndicators?: string[]
  onLoadingChange: (loading: boolean) => void
  data?: any[]
  onLoadMore?: () => void
  onDataUpdate?: (klines: any[], indicators: any) => void
  onIndicatorLoadingChange?: (loading: boolean) => void
  market?: string  // 交易所参数，默认 'hyperliquid'
  compareMarket?: string  // 对比交易所（叠加收盘价线）
  wsRefreshKey?: number  // WebSocket 推送触发的刷新信号
}

type ChartType = 'candlestick' | 'line' | 'area'

export default function TradingViewChart({
  symbol,
  period,
  chartType,
  selectedIndicators,
  selectedFlowIndicators = [],
  onLoadingChange,
  data = [],
  onLoadMore,
  onDataUpdate,
  onIndicatorLoadingChange,
  market = 'hyperliquid',  // 默认为 hyperliquid
  compareMarket,
  wsRefreshKey = 0,        // WebSocket 推送刷新信号
}: TradingViewChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<any>(null)
  const seriesRef = useRef<any>(null)
  const volumeSeriesRef = useRef<any>(null)
  const ma5SeriesRef = useRef<any>(null)
  const ma10SeriesRef = useRef<any>(null)
  const ma20SeriesRef = useRef<any>(null)
  const ema20SeriesRef = useRef<any>(null)
  const ema50SeriesRef = useRef<any>(null)
  const ema100SeriesRef = useRef<any>(null)
  const vwapSeriesRef = useRef<any>(null)
  const bollUpperSeriesRef = useRef<any>(null)
  const bollMiddleSeriesRef = useRef<any>(null)
  const bollLowerSeriesRef = useRef<any>(null)
  const rsiSeriesRef = useRef<any>(null)
  const macdSeriesRef = useRef<any>(null)
  const atrSeriesRef = useRef<any>(null)
  const stochSeriesRef = useRef<any>(null)
  const obvSeriesRef = useRef<any>(null)
  // Extended indicator refs (Phase 2)
  const adxSeriesRef = useRef<any>(null)
  const williamsRSeriesRef = useRef<any>(null)
  const keltnerUpperSeriesRef = useRef<any>(null)
  const keltnerMiddleSeriesRef = useRef<any>(null)
  const keltnerLowerSeriesRef = useRef<any>(null)
  const ichimokuTenkanRef = useRef<any>(null)
  const ichimokuKijunRef = useRef<any>(null)
  const ichimokuSenkouARef = useRef<any>(null)
  const ichimokuSenkouBRef = useRef<any>(null)
  const ichimokuChikouRef = useRef<any>(null)
  // Market Flow refs - all series pre-created in flow pane
  const flowPaneRef = useRef<any>(null)
  const flowLabelRef = useRef<any>(null)
  const flowCvdSeriesRef = useRef<any>(null)
  const flowTakerBuySeriesRef = useRef<any>(null)
  const flowTakerSellSeriesRef = useRef<any>(null)
  const flowOiSeriesRef = useRef<any>(null)
  const flowOiDeltaSeriesRef = useRef<any>(null)
  const flowFundingSeriesRef = useRef<any>(null)
  const flowDepthSeriesRef = useRef<any>(null)
  const flowImbalanceSeriesRef = useRef<any>(null)
  const compareLineSeriesRef = useRef<any>(null)
  const [activeFlowIndicator, setActiveFlowIndicator] = useState<string | null>(null)
  const [flowDataCache, setFlowDataCache] = useState<Record<string, any[]>>({})
  const [flowDataAvailableFrom, setFlowDataAvailableFrom] = useState<number | null>(null)
  const prevFlowIndicatorsRef = useRef<string[]>([])
  const [loading, setLoading] = useState(false)
  const [hasData, setHasData] = useState(false)
  const [chartReadyVersion, setChartReadyVersion] = useState(0)
  const [chartData, setChartData] = useState<any[]>([])
  const [indicatorData, setIndicatorData] = useState<any>({})
  const [cachedIndicators, setCachedIndicators] = useState<string[]>([])
  const [activeSubplot, setActiveSubplot] = useState<string | null>(null)
  const indicatorPaneRef = useRef<any>(null)
  const indicatorLabelRef = useRef<any>(null)
  const prevIndicatorsRef = useRef<string[]>([])
  const prevSubplotIndicatorsRef = useRef<string[]>([])
  // Pane position tracking for selector placement
  const [indicatorPaneTop, setIndicatorPaneTop] = useState<number | null>(null)
  const [flowPaneTop, setFlowPaneTop] = useState<number | null>(null)
  const refreshIntervalRef = useRef<NodeJS.Timeout | null>(null)
  const defaultRangeKeyRef = useRef<string | null>(null)

  // --- Chart overlay hooks (must be after chartData declaration) ---
  // 形态标记
  useChartPatterns({
    symbol, period, chartRef, seriesRef, chartData, enabled: true
  })
  // S/R 支撑阻力线
  useChartSRLevels({
    symbol, period, chartRef, seriesRef, chartData, enabled: true
  })
  // 成交量异动标记
  useVolumeAnomalies({
    symbol, period, chartRef, seriesRef, chartData, enabled: true
  })

  // Market Flow indicator colors
  const FLOW_COLORS: Record<string, { up: string; down: string; line: string }> = {
    cvd: { up: '#22c55e', down: '#ef4444', line: '#3b82f6' },
    taker_volume: { up: '#22c55e', down: '#ef4444', line: '#3b82f6' },
    oi: { up: '#22c55e', down: '#ef4444', line: '#8b5cf6' },
    oi_delta: { up: '#22c55e', down: '#ef4444', line: '#8b5cf6' },
    funding: { up: '#22c55e', down: '#ef4444', line: '#f59e0b' },
    depth_ratio: { up: '#22c55e', down: '#ef4444', line: '#06b6d4' },
    order_imbalance: { up: '#22c55e', down: '#ef4444', line: '#ec4899' }
  }

  const FLOW_LABELS: Record<string, string> = {
    cvd: 'CVD',
    taker_volume: 'Taker Volume',
    oi: 'Open Interest',
    oi_delta: 'OI Delta',
    funding: 'Funding Rate (bps)',
    depth_ratio: 'Depth Ratio (log)',
    order_imbalance: 'Order Imbalance'
  }

  // 检测是否需要重新初始化图表（子图结构变化）
  const needsChartReinit = (prevIndicators: string[], newIndicators: string[]) => {
    const subplotIndicators = ['RSI14', 'RSI7', 'MACD', 'ATR14', 'STOCH', 'OBV']
    const prevSubplots = prevIndicators.filter(ind => subplotIndicators.includes(ind))
    const newSubplots = newIndicators.filter(ind => subplotIndicators.includes(ind))

    // 子图指标从无到有，或从有到无，需要重新初始化
    return (prevSubplots.length === 0) !== (newSubplots.length === 0)
  }

  // Calculate pane positions for selector placement
  const updatePanePositions = () => {
    if (!chartRef.current || !chartContainerRef.current) return
    const panes = chartRef.current.panes()
    const totalHeight = chartContainerRef.current.clientHeight
    let totalStretch = 0
    const stretchFactors: number[] = []
    for (const pane of panes) {
      const factor = pane.getStretchFactor?.() || 1
      stretchFactors.push(factor)
      totalStretch += factor
    }
    // Calculate cumulative top positions
    let currentTop = 0
    const panePositions: number[] = []
    for (let i = 0; i < panes.length; i++) {
      panePositions.push(currentTop)
      currentTop += (stretchFactors[i] / totalStretch) * totalHeight
    }
    // Update indicator pane position (pane index 2 if exists)
    if (indicatorPaneRef.current && panes.length > 2) {
      const idx = panes.indexOf(indicatorPaneRef.current)
      if (idx >= 0) setIndicatorPaneTop(panePositions[idx])
    } else {
      setIndicatorPaneTop(null)
    }
    // Update flow pane position
    if (flowPaneRef.current) {
      const idx = panes.indexOf(flowPaneRef.current)
      if (idx >= 0) setFlowPaneTop(panePositions[idx])
    } else {
      setFlowPaneTop(null)
    }
  }

  const getChartSize = (container: HTMLDivElement) => {
    const rect = container.getBoundingClientRect()
    const parentRect = container.parentElement?.getBoundingClientRect()
    const width = Math.floor(rect.width || parentRect?.width || container.clientWidth || 100)
    const height = Math.floor(rect.height || parentRect?.height || container.clientHeight || 420)
    return {
      width: Math.max(width, 320),
      height: Math.max(height, 360),
    }
  }

  const syncChartSize = () => {
    const container = chartContainerRef.current
    if (!container || !chartRef.current) return
    const { width, height } = getChartSize(container)
    chartRef.current.applyOptions({ width, height })
    updatePanePositions()
  }

  const applyDefaultVisibleRange = (dataLength: number, force = false) => {
    if (!chartRef.current || dataLength <= 0) return
    const rangeKey = `${symbol}:${period}:${chartType}`
    if (!force && defaultRangeKeyRef.current === rangeKey) return

    const visibleBars = Math.min(dataLength, 120)
    const rightPaddingBars = 8
    const from = Math.max(0, dataLength - visibleBars)
    const to = dataLength + rightPaddingBars

    try {
      chartRef.current.timeScale().setVisibleLogicalRange({ from, to })
      defaultRangeKeyRef.current = rangeKey
    } catch {
      try { chartRef.current.timeScale().scrollToRealTime() } catch {}
    }
  }

  // 创建 pane 标签的 primitive
  const createPaneLabel = (text: string) => ({
    paneViews() {
      return [{
        renderer() {
          return {
            draw(target: any) {
              target.useMediaCoordinateSpace((scope: any) => {
                const ctx = scope.context
                ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
                ctx.fillStyle = 'rgba(156, 163, 175, 0.6)'
                ctx.textAlign = 'left'
                ctx.textBaseline = 'top'
                ctx.fillText(text, 8, 8)
              })
            }
          }
        }
      }]
    }
  })

  // 根据价格动态计算精度配置
  const getPriceFormatByPrice = (price: number) => {
    const absPrice = Math.abs(price)
    let decimals: number
    
    if (absPrice >= 100) {
      decimals = 2  // >= $100: 2位小数
    } else if (absPrice >= 1) {
      decimals = 4  // >= $1: 4位小数
    } else if (absPrice >= 0.01) {
      decimals = 6  // >= $0.01: 6位小数
    } else {
      decimals = 8  // < $0.01: 8位小数
    }
    
    const minMove = 1 / Math.pow(10, decimals)
    return {
      type: 'price' as const,
      precision: decimals,
      minMove: minMove
    }
  }

  // 根据symbol获取价格精度配置（用于初始化）
  const getPriceFormat = (sym: string) => {
    const decimals = getSymbolDecimals(sym)
    const minMove = 1 / Math.pow(10, decimals)
    return {
      type: 'price' as const,
      precision: decimals,
      minMove: minMove
    }
  }

  // 创建主图表系列
  const createMainSeries = (chart: any, type: ChartType, sym: string) => {
    const priceFormat = getPriceFormat(sym)
    switch (type) {
      case 'candlestick':
        return chart.addSeries(CandlestickSeries, {
          upColor: '#22c55e',
          downColor: '#ef4444',
          borderDownColor: '#ef4444',
          borderUpColor: '#22c55e',
          wickDownColor: '#ef4444',
          wickUpColor: '#22c55e',
          priceFormat,
        })
      case 'line':
        return chart.addSeries(LineSeries, {
          color: '#3b82f6',
          lineWidth: 2,
          priceFormat,
        })
      case 'area':
        return chart.addSeries(AreaSeries, {
          topColor: '#3b82f640',
          bottomColor: '#3b82f610',
          lineColor: '#3b82f6',
          lineWidth: 2,
          priceFormat,
        })
      default:
        return chart.addSeries(CandlestickSeries, {
          upColor: '#22c55e',
          downColor: '#ef4444',
          borderDownColor: '#ef4444',
          borderUpColor: '#22c55e',
          wickDownColor: '#ef4444',
          wickUpColor: '#22c55e',
          priceFormat,
        })
    }
  }

  // 转换数据格式
  const convertDataForSeries = (data: any[], type: ChartType) => {
    switch (type) {
      case 'candlestick':
        return data.map(item => ({
          time: item.time,
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
        }))
      case 'line':
      case 'area':
        return data.map(item => ({
          time: item.time,
          value: item.close,
        }))
      default:
        return data
    }
  }

  // 计算移动平均线
  const calculateMA = (data: any[], period: number) => {
    const result = []
    for (let i = period - 1; i < data.length; i++) {
      const sum = data.slice(i - period + 1, i + 1).reduce((acc, item) => acc + item.close, 0)
      result.push({
        time: data[i].time,
        value: sum / period,
      })
    }
    return result
  }


  // 图表初始化 - 只在chartType变化时重新初始化
  useEffect(() => {
    if (!chartContainerRef.current) return

    try {
      const container = chartContainerRef.current
      container.replaceChildren()

      // 判断是否需要指标子图
      const subplotIndicators = selectedIndicators.filter(ind => ['RSI14', 'RSI7', 'MACD', 'ATR14', 'STOCH', 'OBV'].includes(ind))
      const needsSubplot = subplotIndicators.length > 0

      // 创建图表 - 使用正确的Panel架构
      // 确保至少 100px 宽度/300px 高度，防止布局延迟导致 0 尺寸图表不可见
      const initialSize = getChartSize(container)
      const chart = createChart(container, {
        width: initialSize.width,
        height: initialSize.height,
        layout: {
          background: { color: 'transparent' },
          textColor: '#9ca3af',
          attributionLogo: false,
        },
        localization: {
          locale: 'en-US',
          timeFormatter: (time: unknown) => formatChartLocalTime(time, { withDate: true }),
        },
        grid: {
          vertLines: { color: 'rgba(156, 163, 175, 0.1)' },
          horzLines: { color: 'rgba(156, 163, 175, 0.1)' },
        },
        crosshair: {
          mode: 1,
          vertLine: {
            width: 1,
            color: 'rgba(156, 163, 175, 0.5)',
            style: 0,
          },
          horzLine: {
            width: 1,
            color: 'rgba(156, 163, 175, 0.5)',
            style: 0,
          },
        },
        rightPriceScale: {
          borderColor: 'rgba(156, 163, 175, 0.2)',
        },
        timeScale: {
          borderColor: 'rgba(156, 163, 175, 0.2)',
          timeVisible: true,
          secondsVisible: false,
          barSpacing: 9,
          rightBarStaysOnScroll: false,
          tickMarkFormatter: (time: unknown) => formatChartLocalTime(time),
        },
      })

      // 创建Volume Panel
      const volumePane = chart.addPane()
      volumePane.attachPrimitive(createPaneLabel('Volume'))

      // 创建指标Panel（如果需要）
      let indicatorPane = null
      if (needsSubplot) {
        indicatorPane = chart.addPane()
        indicatorPaneRef.current = indicatorPane
        // 创建并附加标签 primitive
        const labelPrimitive = createPaneLabel('Indicators')
        indicatorPane.attachPrimitive(labelPrimitive)
        indicatorLabelRef.current = labelPrimitive
      }

      // 设置Panel高度比例
      if (needsSubplot) {
        // 三层布局：主图60% + Volume20% + 指标20%
        chart.panes()[0].setStretchFactor(3)  // 主图 60% (3/5)
        volumePane.setStretchFactor(1)        // Volume 20% (1/5)
        indicatorPane.setStretchFactor(1)     // 指标 20% (1/5)
      } else {
        // 两层布局：主图80% + Volume20%
        chart.panes()[0].setStretchFactor(4)  // 主图 80% (4/5)
        volumePane.setStretchFactor(1)        // Volume 20% (1/5)
      }

      // 在主Panel创建主图表系列
      const mainSeries = createMainSeries(chart, chartType, symbol)

      // 在Volume Panel创建成交量系列
      const volumeSeries = volumePane.addSeries(HistogramSeries, {
        color: '#6b7280',
        priceFormat: {
          type: 'volume',
        },
      })


      // 创建移动平均线系列
      const ma5Series = chart.addSeries(LineSeries, {
        color: '#ff6b6b',
        lineWidth: 1,
        visible: false,
      })

      const ma10Series = chart.addSeries(LineSeries, {
        color: '#4ecdc4',
        lineWidth: 1,
        visible: false,
      })

      const ma20Series = chart.addSeries(LineSeries, {
        color: '#45b7d1',
        lineWidth: 1,
        visible: false,
      })

      // EMA指标系列
      const ema20Series = chart.addSeries(LineSeries, {
        color: '#f59e0b',
        lineWidth: 2,
        visible: false,
      })

      const ema50Series = chart.addSeries(LineSeries, {
        color: '#8b5cf6',
        lineWidth: 2,
        visible: false,
      })

      const ema100Series = chart.addSeries(LineSeries, {
        color: '#ec4899',
        lineWidth: 2,
        visible: false,
      })

      const vwapSeries = chart.addSeries(LineSeries, {
        color: '#14b8a6',
        lineWidth: 2,
        visible: false,
      })

      // 创建BOLL布林带系列
      const bollUpperSeries = chart.addSeries(LineSeries, {
        color: '#9333ea',
        lineWidth: 1,
        visible: false,
      })

      const bollMiddleSeries = chart.addSeries(LineSeries, {
        color: '#3b82f6',
        lineWidth: 1,
        visible: false,
      })

      const bollLowerSeries = chart.addSeries(LineSeries, {
        color: '#9333ea',
        lineWidth: 1,
        visible: false,
      })

      const keltnerUpperSeries = chart.addSeries(LineSeries, {
        color: '#06b6d4',
        lineWidth: 1,
        visible: false,
      })
      const keltnerMiddleSeries = chart.addSeries(LineSeries, {
        color: '#06b6d4',
        lineWidth: 1,
        lineStyle: 2,
        visible: false,
      })
      const keltnerLowerSeries = chart.addSeries(LineSeries, {
        color: '#06b6d4',
        lineWidth: 1,
        visible: false,
      })

      // 创建指标系列（在指标Panel中）
      let rsiSeries = null
      let macdSeries = null
      let atrSeries = null
      let stochSeries = null
      let obvSeries = null

      if (indicatorPane) {
        rsiSeries = indicatorPane.addSeries(LineSeries, {
          color: '#e11d48',
          lineWidth: 2,
          visible: false,
        })

        // MACD需要多个系列
        const macdLine = indicatorPane.addSeries(LineSeries, {
          color: '#3b82f6',
          lineWidth: 2,
          visible: false,
        })
        const signalLine = indicatorPane.addSeries(LineSeries, {
          color: '#f59e0b',
          lineWidth: 1,
          visible: false,
        })
        const histogram = indicatorPane.addSeries(HistogramSeries, {
          color: '#6b7280',
          visible: false,
        })
        macdSeries = { macdLine, signalLine, histogram }

        atrSeries = indicatorPane.addSeries(LineSeries, {
          color: '#8b5cf6',
          lineWidth: 2,
          visible: false,
        })

        // Stochastic需要两条线（%K和%D）
        const stochK = indicatorPane.addSeries(LineSeries, {
          color: '#3b82f6',
          lineWidth: 2,
          visible: false,
        })
        const stochD = indicatorPane.addSeries(LineSeries, {
          color: '#f59e0b',
          lineWidth: 1,
          visible: false,
        })
        stochSeries = { stochK, stochD }

        obvSeries = indicatorPane.addSeries(LineSeries, {
          color: '#10b981',
          lineWidth: 2,
          visible: false,
        })

        let adxSeries = indicatorPane.addSeries(LineSeries, {
          color: '#f97316',
          lineWidth: 2,
          visible: false,
        })

        let williamsRSeries = indicatorPane.addSeries(LineSeries, {
          color: '#a855f7',
          lineWidth: 2,
          visible: false,
        })
      }

      chartRef.current = chart
      seriesRef.current = mainSeries
      volumeSeriesRef.current = volumeSeries
      setChartReadyVersion(v => v + 1)
      ma5SeriesRef.current = ma5Series
      ma10SeriesRef.current = ma10Series
      ma20SeriesRef.current = ma20Series
      ema20SeriesRef.current = ema20Series
      ema50SeriesRef.current = ema50Series
      ema100SeriesRef.current = ema100Series
      vwapSeriesRef.current = vwapSeries
      bollUpperSeriesRef.current = bollUpperSeries
      bollMiddleSeriesRef.current = bollMiddleSeries
      bollLowerSeriesRef.current = bollLowerSeries
      keltnerUpperSeriesRef.current = keltnerUpperSeries
      keltnerMiddleSeriesRef.current = keltnerMiddleSeries
      keltnerLowerSeriesRef.current = keltnerLowerSeries
      rsiSeriesRef.current = rsiSeries
      macdSeriesRef.current = macdSeries
      atrSeriesRef.current = atrSeries
      stochSeriesRef.current = stochSeries
      obvSeriesRef.current = obvSeries
      adxSeriesRef.current = adxSeries
      williamsRSeriesRef.current = williamsRSeries

      // 监听容器大小变化
      let resizeTimeout: NodeJS.Timeout
      const resizeObserver = new ResizeObserver(() => {
        clearTimeout(resizeTimeout)
        resizeTimeout = setTimeout(() => {
          syncChartSize()
          applyDefaultVisibleRange(chartData.length)
        }, 100)
      })
      resizeObserver.observe(container)

      // Initial pane position calculation
      requestAnimationFrame(() => {
        syncChartSize()
        applyDefaultVisibleRange(chartData.length)
      })
      setTimeout(() => {
        syncChartSize()
        applyDefaultVisibleRange(chartData.length)
      }, 250)

      return () => {
        clearTimeout(resizeTimeout)
        resizeObserver.disconnect()
        try {
          chart.remove()
        } catch {}
        if (chartRef.current === chart) {
          chartRef.current = null
          seriesRef.current = null
          compareLineSeriesRef.current = null
          volumeSeriesRef.current = null
          ma5SeriesRef.current = null
          ma10SeriesRef.current = null
          ma20SeriesRef.current = null
          ema20SeriesRef.current = null
          ema50SeriesRef.current = null
          ema100SeriesRef.current = null
          vwapSeriesRef.current = null
          bollUpperSeriesRef.current = null
          bollMiddleSeriesRef.current = null
          bollLowerSeriesRef.current = null
          rsiSeriesRef.current = null
          macdSeriesRef.current = null
          atrSeriesRef.current = null
          stochSeriesRef.current = null
          obvSeriesRef.current = null
          indicatorPaneRef.current = null
          indicatorLabelRef.current = null
          // Clean up flow refs
          flowPaneRef.current = null
          flowLabelRef.current = null
          flowCvdSeriesRef.current = null
          flowTakerBuySeriesRef.current = null
          flowTakerSellSeriesRef.current = null
          flowOiSeriesRef.current = null
          flowOiDeltaSeriesRef.current = null
          flowFundingSeriesRef.current = null
          flowDepthSeriesRef.current = null
          flowImbalanceSeriesRef.current = null
        }
      }
    } catch (error) {
      console.error('Chart initialization failed:', error)
    }
  }, [chartType])

  // 动态管理子图Pane - 只在子图结构变化时重新初始化
  useEffect(() => {
    if (!chartRef.current || !chartContainerRef.current) return

    const shouldReinit = needsChartReinit(prevIndicatorsRef.current, selectedIndicators)

    if (shouldReinit) {
      // 需要重新初始化图表结构
      const container = chartContainerRef.current
      const currentChartData = chartData
      const currentIndicatorData = indicatorData

      // 在重建前设置正确的activeSubplot，避免状态滞后
      const subplotIndicators = selectedIndicators.filter(ind => ['RSI14', 'RSI7', 'MACD', 'ATR14', 'STOCH', 'OBV'].includes(ind))
      if (subplotIndicators.length > 0 && !activeSubplot) {
        setActiveSubplot(subplotIndicators[0])
      }

      // 保存当前数据，重新初始化图表
      if (chartRef.current) {
        chartRef.current.remove()
        // Clear flow pane refs since chart is destroyed - they will be recreated
        flowPaneRef.current = null
        flowLabelRef.current = null
        flowCvdSeriesRef.current = null
        flowTakerBuySeriesRef.current = null
        flowTakerSellSeriesRef.current = null
        flowOiSeriesRef.current = null
        flowOiDeltaSeriesRef.current = null
        flowFundingSeriesRef.current = null
        flowDepthSeriesRef.current = null
        flowImbalanceSeriesRef.current = null
      }
      container.replaceChildren()

      try {
        // 判断是否需要指标子图
        const subplotIndicators = selectedIndicators.filter(ind => ['RSI14', 'RSI7', 'MACD', 'ATR14', 'STOCH', 'OBV'].includes(ind))
        const needsSubplot = subplotIndicators.length > 0

        // 创建图表 - 使用正确的Panel架构
        const initialSize = getChartSize(container)
        const chart = createChart(container, {
          width: initialSize.width,
          height: initialSize.height,
          layout: {
            background: { color: 'transparent' },
            textColor: '#9ca3af',
            attributionLogo: false,
          },
          localization: {
            locale: 'en-US',
            timeFormatter: (time: unknown) => formatChartLocalTime(time, { withDate: true }),
          },
          grid: {
            vertLines: { color: 'rgba(156, 163, 175, 0.1)' },
            horzLines: { color: 'rgba(156, 163, 175, 0.1)' },
          },
          crosshair: {
            mode: 1,
            vertLine: {
              width: 1,
              color: 'rgba(156, 163, 175, 0.5)',
              style: 0,
            },
            horzLine: {
              width: 1,
              color: 'rgba(156, 163, 175, 0.5)',
              style: 0,
            },
          },
          rightPriceScale: {
            borderColor: 'rgba(156, 163, 175, 0.2)',
          },
          timeScale: {
            borderColor: 'rgba(156, 163, 175, 0.2)',
            timeVisible: true,
            secondsVisible: false,
            barSpacing: 9,
            rightBarStaysOnScroll: false,
            tickMarkFormatter: (time: unknown) => formatChartLocalTime(time),
          },
        })

        // 创建Volume Panel
        const volumePane = chart.addPane()
        volumePane.attachPrimitive(createPaneLabel('Volume'))

        // 创建指标Panel（如果需要）
        let indicatorPane = null
        if (needsSubplot) {
          indicatorPane = chart.addPane()
          indicatorPaneRef.current = indicatorPane
          const labelPrimitive = createPaneLabel('Indicators')
          indicatorPane.attachPrimitive(labelPrimitive)
          indicatorLabelRef.current = labelPrimitive
        }

        // 设置Panel高度比例
        if (needsSubplot) {
          chart.panes()[0].setStretchFactor(3)
          volumePane.setStretchFactor(1)
          indicatorPane.setStretchFactor(1)
        } else {
          chart.panes()[0].setStretchFactor(4)
          volumePane.setStretchFactor(1)
        }

        // 重新创建所有系列
        const mainSeries = createMainSeries(chart, chartType, symbol)
        const volumeSeries = volumePane.addSeries(HistogramSeries, {
          color: '#6b7280',
          priceFormat: { type: 'volume' },
        })

        // 创建移动平均线系列
        const ma5Series = chart.addSeries(LineSeries, { color: '#ff6b6b', lineWidth: 1, visible: false })
        const ma10Series = chart.addSeries(LineSeries, { color: '#4ecdc4', lineWidth: 1, visible: false })
        const ma20Series = chart.addSeries(LineSeries, { color: '#45b7d1', lineWidth: 1, visible: false })
        const ema20Series = chart.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 2, visible: false })
        const ema50Series = chart.addSeries(LineSeries, { color: '#8b5cf6', lineWidth: 2, visible: false })
        const ema100Series = chart.addSeries(LineSeries, { color: '#ec4899', lineWidth: 2, visible: false })
        const vwapSeries = chart.addSeries(LineSeries, { color: '#14b8a6', lineWidth: 2, visible: false })
        const bollUpperSeries = chart.addSeries(LineSeries, { color: '#9333ea', lineWidth: 1, visible: false })
        const bollMiddleSeries = chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 1, visible: false })
        const bollLowerSeries = chart.addSeries(LineSeries, { color: '#9333ea', lineWidth: 1, visible: false })

        // 创建指标系列（在指标Panel中）
        let rsiSeries = null
        let macdSeries = null
        let atrSeries = null
        let stochSeries = null
        let obvSeries = null

        if (indicatorPane) {
          rsiSeries = indicatorPane.addSeries(LineSeries, { color: '#e11d48', lineWidth: 2, visible: false })
          const macdLine = indicatorPane.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 2, visible: false })
          const signalLine = indicatorPane.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1, visible: false })
          const histogram = indicatorPane.addSeries(HistogramSeries, { color: '#6b7280', visible: false })
          macdSeries = { macdLine, signalLine, histogram }
          atrSeries = indicatorPane.addSeries(LineSeries, { color: '#8b5cf6', lineWidth: 2, visible: false })
          const stochK = indicatorPane.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 2, visible: false })
          const stochD = indicatorPane.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1, visible: false })
          stochSeries = { stochK, stochD }
          obvSeries = indicatorPane.addSeries(LineSeries, { color: '#10b981', lineWidth: 2, visible: false })
        }

        // 更新所有引用
        chartRef.current = chart
        seriesRef.current = mainSeries
        volumeSeriesRef.current = volumeSeries
        setChartReadyVersion(v => v + 1)
        ma5SeriesRef.current = ma5Series
        ma10SeriesRef.current = ma10Series
        ma20SeriesRef.current = ma20Series
        ema20SeriesRef.current = ema20Series
        ema50SeriesRef.current = ema50Series
        ema100SeriesRef.current = ema100Series
        vwapSeriesRef.current = vwapSeries
        bollUpperSeriesRef.current = bollUpperSeries
        bollMiddleSeriesRef.current = bollMiddleSeries
        bollLowerSeriesRef.current = bollLowerSeries
        rsiSeriesRef.current = rsiSeries
        macdSeriesRef.current = macdSeries
        atrSeriesRef.current = atrSeries
        stochSeriesRef.current = stochSeries
        obvSeriesRef.current = obvSeries

        // 重新应用数据
        const resolvedActiveSubplot = (activeSubplot && subplotIndicators.includes(activeSubplot))
          ? activeSubplot
          : subplotIndicators[0]

        if (currentChartData.length > 0) {
          const mainData = convertDataForSeries(currentChartData, chartType)
          const volumeData = currentChartData.map(item => ({
            time: item.time,
            value: item.volume || 0,
            color: item.close >= item.open ? '#22c55e' : '#ef4444',
          }))

          mainSeries.setData(mainData)
          volumeSeries.setData(volumeData)

          // 重建图表后默认显示最近一段 K 线，避免 500 根全挤在一屏。
          applyDefaultVisibleRange(currentChartData.length, true)

          // 重新应用移动平均线数据
          const ma5Data = calculateMA(currentChartData, 5)
          const ma10Data = calculateMA(currentChartData, 10)
          const ma20Data = calculateMA(currentChartData, 20)
          ma5Series.setData(ma5Data)
          ma10Series.setData(ma10Data)
          ma20Series.setData(ma20Data)

          // 重新应用指标数据
          if (currentIndicatorData.EMA20 && ema20Series) {
            const ema20Data = currentIndicatorData.EMA20.map((value: number, index: number) => ({
              time: currentChartData[index]?.time,
              value: value
            })).filter((item: any) => item.time && item.value > 0)
            ema20Series.setData(ema20Data)
          }

          if (currentIndicatorData.EMA50 && ema50Series) {
            const ema50Data = currentIndicatorData.EMA50.map((value: number, index: number) => ({
              time: currentChartData[index]?.time,
              value: value
            })).filter((item: any) => item.time && item.value > 0)
            ema50Series.setData(ema50Data)
          }

          if (currentIndicatorData.EMA100 && ema100SeriesRef.current) {
            const ema100Data = currentIndicatorData.EMA100.map((value: number, index: number) => ({
              time: currentChartData[index]?.time,
              value: value
            })).filter((item: any) => item.time && item.value > 0)
            ema100SeriesRef.current.setData(ema100Data)
          }

          if (currentIndicatorData.VWAP && vwapSeriesRef.current) {
            const vwapData = currentIndicatorData.VWAP.map((value: number, index: number) => ({
              time: currentChartData[index]?.time,
              value: value
            })).filter((item: any) => item.time && !isNaN(item.value) && item.value !== null)
            vwapSeriesRef.current.setData(vwapData)
          }

          // 重新应用BOLL数据
          if (currentIndicatorData.BOLL) {
            const bollData = currentIndicatorData.BOLL
            if (bollData.upper && bollUpperSeries) {
              const upperData = bollData.upper.map((value: number, index: number) => ({
                time: currentChartData[index]?.time,
                value: value
              })).filter((item: any) => item.time && !isNaN(item.value))
              bollUpperSeries.setData(upperData)
            }
            if (bollData.middle && bollMiddleSeries) {
              const middleData = bollData.middle.map((value: number, index: number) => ({
                time: currentChartData[index]?.time,
                value: value
              })).filter((item: any) => item.time && !isNaN(item.value))
              bollMiddleSeries.setData(middleData)
            }
            if (bollData.lower && bollLowerSeries) {
              const lowerData = bollData.lower.map((value: number, index: number) => ({
                time: currentChartData[index]?.time,
                value: value
              })).filter((item: any) => item.time && !isNaN(item.value))
              bollLowerSeries.setData(lowerData)
            }
          }

          // 重新应用RSI数据 - 应用所有可用的RSI数据
          if (rsiSeries) {
            const rsiSource = resolvedActiveSubplot === 'RSI7' ? currentIndicatorData.RSI7 : currentIndicatorData.RSI14 || currentIndicatorData.RSI7
            const rsiData = (rsiSource || []).map((value: number, index: number) => ({
              time: currentChartData[index]?.time,
              value: value
            })).filter((item: any) => item.time && !isNaN(item.value) && item.value > 0)
            rsiSeries.setData(rsiData)
          }

          // 重新应用MACD数据 - 无条件应用如果数据存在
          if (currentIndicatorData.MACD && macdSeries) {
            const macdData = currentIndicatorData.MACD
            if (macdData.macd && macdSeries.macdLine) {
              const macdLineData = macdData.macd.map((value: number, index: number) => ({
                time: currentChartData[index]?.time,
                value: value
              })).filter((item: any) => item.time && !isNaN(item.value))
              macdSeries.macdLine.setData(macdLineData)
            }
            if (macdData.signal && macdSeries.signalLine) {
              const signalData = macdData.signal.map((value: number, index: number) => ({
                time: currentChartData[index]?.time,
                value: value
              })).filter((item: any) => item.time && !isNaN(item.value))
              macdSeries.signalLine.setData(signalData)
            }
            if (macdData.histogram && macdSeries.histogram) {
              const histogramData = macdData.histogram.map((value: number, index: number) => ({
                time: currentChartData[index]?.time,
                value: value,
                color: value >= 0 ? '#22c55e' : '#ef4444'
              })).filter((item: any) => item.time && !isNaN(item.value))
              macdSeries.histogram.setData(histogramData)
            }
          }

          // 重新应用ATR数据 - 无条件应用如果数据存在
          if (currentIndicatorData.ATR14 && atrSeries) {
            const atrData = currentIndicatorData.ATR14.map((value: number, index: number) => ({
              time: currentChartData[index]?.time,
              value: value
            })).filter((item: any) => item.time && !isNaN(item.value))
            atrSeries.setData(atrData)
          }

          // 重新应用STOCH数据
          if (currentIndicatorData.STOCH && stochSeriesRef.current) {
            const stochData = currentIndicatorData.STOCH
            if (stochData.k && stochSeriesRef.current.stochK) {
              const kData = stochData.k.map((value: number, index: number) => ({
                time: currentChartData[index]?.time,
                value: value
              })).filter((item: any) => item.time && !isNaN(item.value))
              stochSeriesRef.current.stochK.setData(kData)
            }
            if (stochData.d && stochSeriesRef.current.stochD) {
              const dData = stochData.d.map((value: number, index: number) => ({
                time: currentChartData[index]?.time,
                value: value
              })).filter((item: any) => item.time && !isNaN(item.value))
              stochSeriesRef.current.stochD.setData(dData)
            }
          }

          // 重新应用OBV数据
          if (currentIndicatorData.OBV && obvSeriesRef.current) {
            const obvData = currentIndicatorData.OBV.map((value: number, index: number) => ({
              time: currentChartData[index]?.time,
              value: value
            })).filter((item: any) => item.time && !isNaN(item.value))
            obvSeriesRef.current.setData(obvData)
          }
        }

        // 重新应用指标显示状态
        setTimeout(() => {
          const subplotIndicators = selectedIndicators.filter(ind => ['RSI14', 'RSI7', 'MACD', 'ATR14', 'STOCH', 'OBV'].includes(ind))
          const resolvedActiveSubplot = (activeSubplot && subplotIndicators.includes(activeSubplot))
            ? activeSubplot
            : subplotIndicators[0]

          // 主图指标显示状态
          if (ma5Series) ma5Series.applyOptions({ visible: selectedIndicators.includes('MA5') })
          if (ma10Series) ma10Series.applyOptions({ visible: selectedIndicators.includes('MA10') })
          if (ma20Series) ma20Series.applyOptions({ visible: selectedIndicators.includes('MA20') })
          if (ema20Series) ema20Series.applyOptions({ visible: selectedIndicators.includes('EMA20') })
          if (ema50Series) ema50Series.applyOptions({ visible: selectedIndicators.includes('EMA50') })
          if (ema100SeriesRef.current) ema100SeriesRef.current.applyOptions({ visible: selectedIndicators.includes('EMA100') })
          if (vwapSeriesRef.current) vwapSeriesRef.current.applyOptions({ visible: selectedIndicators.includes('VWAP') })

          const showBoll = selectedIndicators.includes('BOLL')
          if (bollUpperSeries) bollUpperSeries.applyOptions({ visible: showBoll })
          if (bollMiddleSeries) bollMiddleSeries.applyOptions({ visible: showBoll })
          if (bollLowerSeries) bollLowerSeries.applyOptions({ visible: showBoll })

          // 子图指标显示状态
          if (rsiSeries) {
            const showRSI = (resolvedActiveSubplot === 'RSI14' || resolvedActiveSubplot === 'RSI7') && selectedIndicators.includes(resolvedActiveSubplot)
            rsiSeries.applyOptions({ visible: showRSI })
          }

          if (macdSeries) {
            const showMACD = resolvedActiveSubplot === 'MACD' && selectedIndicators.includes('MACD')
            if (macdSeries.macdLine) macdSeries.macdLine.applyOptions({ visible: showMACD })
            if (macdSeries.signalLine) macdSeries.signalLine.applyOptions({ visible: showMACD })
            if (macdSeries.histogram) macdSeries.histogram.applyOptions({ visible: showMACD })
          }

          if (atrSeries) {
            const showATR = resolvedActiveSubplot === 'ATR14' && selectedIndicators.includes('ATR14')
            atrSeries.applyOptions({ visible: showATR })
          }

          if (stochSeriesRef.current) {
            const showSTOCH = resolvedActiveSubplot === 'STOCH' && selectedIndicators.includes('STOCH')
            if (stochSeriesRef.current.stochK) stochSeriesRef.current.stochK.applyOptions({ visible: showSTOCH })
            if (stochSeriesRef.current.stochD) stochSeriesRef.current.stochD.applyOptions({ visible: showSTOCH })
          }

          if (obvSeriesRef.current) {
            const showOBV = resolvedActiveSubplot === 'OBV' && selectedIndicators.includes('OBV')
            obvSeriesRef.current.applyOptions({ visible: showOBV })
          }

          // Recreate flow pane if there are selected flow indicators
          if (selectedFlowIndicators.length > 0 && chartRef.current && !flowPaneRef.current) {
            const flowPane = chartRef.current.addPane()
            flowPane.setStretchFactor(1)
            const labelPrimitive = createPaneLabel('Market Flow')
            flowPane.attachPrimitive(labelPrimitive)
            flowLabelRef.current = labelPrimitive
            flowPaneRef.current = flowPane

            // Pre-create all flow series
            flowCvdSeriesRef.current = flowPane.addSeries(LineSeries, {
              color: FLOW_COLORS.cvd.line, lineWidth: 2, visible: false,
              priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
            })
            flowTakerBuySeriesRef.current = flowPane.addSeries(HistogramSeries, {
              color: FLOW_COLORS.taker_volume.up, visible: false,
              priceFormat: { type: 'volume' }
            })
            flowTakerSellSeriesRef.current = flowPane.addSeries(HistogramSeries, {
              color: FLOW_COLORS.taker_volume.down, visible: false,
              priceFormat: { type: 'volume' }
            })
            flowOiSeriesRef.current = flowPane.addSeries(LineSeries, {
              color: FLOW_COLORS.oi.line, lineWidth: 2, visible: false,
              priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
            })
            flowOiDeltaSeriesRef.current = flowPane.addSeries(HistogramSeries, {
              color: FLOW_COLORS.oi_delta.line, visible: false,
              priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
            })
            flowFundingSeriesRef.current = flowPane.addSeries(LineSeries, {
              color: FLOW_COLORS.funding.line, lineWidth: 2, visible: false,
              priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
            })
            flowDepthSeriesRef.current = flowPane.addSeries(LineSeries, {
              color: FLOW_COLORS.depth_ratio.line, lineWidth: 2, visible: false,
              priceFormat: { type: 'price', precision: 4, minMove: 0.0001 }
            })
            flowImbalanceSeriesRef.current = flowPane.addSeries(HistogramSeries, {
              color: FLOW_COLORS.order_imbalance.line, visible: false,
              priceFormat: { type: 'price', precision: 4, minMove: 0.0001 }
            })

            // Show active indicator and fetch data
            if (activeFlowIndicator) {
              showFlowSeries(activeFlowIndicator)
              updateFlowPaneLabel(activeFlowIndicator)
              if (flowDataCache[activeFlowIndicator]) {
                updateFlowSeries(activeFlowIndicator, flowDataCache[activeFlowIndicator])
              } else {
                fetchFlowData(activeFlowIndicator)
              }
            }
            // Update pane positions after flow pane created
            updatePanePositions()
          }
        }, 0)
      } catch (error) {
        console.error('Chart reinitialization failed:', error)
      }
    }

    prevIndicatorsRef.current = selectedIndicators
  }, [selectedIndicators, chartData, indicatorData, chartType])

  // 更新数据
  useEffect(() => {
    const subplotIndicators = selectedIndicators.filter(ind => ['RSI14', 'RSI7', 'MACD', 'ATR14', 'STOCH', 'OBV'].includes(ind))
    const resolvedActiveSubplot = (activeSubplot && subplotIndicators.includes(activeSubplot))
      ? activeSubplot
      : subplotIndicators[0]

    if (seriesRef.current && volumeSeriesRef.current && chartData.length > 0) {
      // 转换主图数据
      const mainData = convertDataForSeries(chartData, chartType)

      // 根据实际价格动态更新价格精度
      if (chartData.length > 0 && seriesRef.current) {
        const latestPrice = chartData[chartData.length - 1].close
        const dynamicPriceFormat = getPriceFormatByPrice(latestPrice)
        seriesRef.current.applyOptions({ priceFormat: dynamicPriceFormat })
      }

      // 成交量数据
      const volumeData = chartData.map(item => ({
        time: item.time,
        value: item.volume || 0,
        color: item.close >= item.open ? '#22c55e' : '#ef4444',
      }))

      // 移动平均线数据
      const ma5Data = calculateMA(chartData, 5)
      const ma10Data = calculateMA(chartData, 10)
      const ma20Data = calculateMA(chartData, 20)

      // 确保数据完全替换，避免重合
      seriesRef.current.setData(mainData)
      volumeSeriesRef.current.setData(volumeData)
      console.log('[Data Update] setData complete. mainData:', mainData.length, 'volumeData:', volumeData.length)

      // 默认显示最近一段 K 线；实时刷新不重置用户手动缩放/拖动。
      applyDefaultVisibleRange(chartData.length)
      requestAnimationFrame(() => {
        syncChartSize()
        applyDefaultVisibleRange(chartData.length)
      })

      if (ma5SeriesRef.current) ma5SeriesRef.current.setData(ma5Data)
      if (ma10SeriesRef.current) ma10SeriesRef.current.setData(ma10Data)
      if (ma20SeriesRef.current) ma20SeriesRef.current.setData(ma20Data)

      // 渲染技术指标数据
      if (indicatorData.EMA20 && ema20SeriesRef.current) {
        const ema20Data = indicatorData.EMA20.map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && item.value > 0)
        ema20SeriesRef.current.setData(ema20Data)
      }

      if (indicatorData.EMA50 && ema50SeriesRef.current) {
        const ema50Data = indicatorData.EMA50.map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && item.value > 0)
        ema50SeriesRef.current.setData(ema50Data)
      }

      if (indicatorData.EMA100 && ema100SeriesRef.current) {
        const ema100Data = indicatorData.EMA100.map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && item.value > 0)
        ema100SeriesRef.current.setData(ema100Data)
      }

      if (indicatorData.VWAP && vwapSeriesRef.current) {
        const vwapData = indicatorData.VWAP.map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && !isNaN(item.value) && item.value !== null)
        vwapSeriesRef.current.setData(vwapData)
      }

      // 渲染RSI指标 - 根据当前有效子图决定数据源
      if (rsiSeriesRef.current) {
        const rsiSource = resolvedActiveSubplot === 'RSI7' ? indicatorData.RSI7 : indicatorData.RSI14 || indicatorData.RSI7
        const rsiData = (rsiSource || []).map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && !isNaN(item.value) && item.value > 0)
        rsiSeriesRef.current.setData(rsiData)
      }

      // 渲染MACD指标 - 无条件应用如果数据存在
      if (indicatorData.MACD && macdSeriesRef.current) {
        const macdData = indicatorData.MACD
        if (macdData.macd && macdSeriesRef.current.macdLine) {
          const macdLineData = macdData.macd.map((value: number, index: number) => ({
            time: chartData[index]?.time,
            value: value
          })).filter((item: any) => item.time && !isNaN(item.value))
          macdSeriesRef.current.macdLine.setData(macdLineData)
        }
        if (macdData.signal && macdSeriesRef.current.signalLine) {
          const signalData = macdData.signal.map((value: number, index: number) => ({
            time: chartData[index]?.time,
            value: value
          })).filter((item: any) => item.time && !isNaN(item.value))
          macdSeriesRef.current.signalLine.setData(signalData)
        }
        if (macdData.histogram && macdSeriesRef.current.histogram) {
          const histogramData = macdData.histogram.map((value: number, index: number) => ({
            time: chartData[index]?.time,
            value: value,
            color: value >= 0 ? '#22c55e' : '#ef4444'
          })).filter((item: any) => item.time && !isNaN(item.value))
          macdSeriesRef.current.histogram.setData(histogramData)
        }
      }

      // 渲染ATR指标
      if (indicatorData.ATR14 && atrSeriesRef.current) {
        const atrData = indicatorData.ATR14.map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && !isNaN(item.value))
        atrSeriesRef.current.setData(atrData)
      }

      // 渲染STOCH指标
      if (indicatorData.STOCH && stochSeriesRef.current) {
        const stochData = indicatorData.STOCH
        if (stochData.k && stochSeriesRef.current.stochK) {
          const kData = stochData.k.map((value: number, index: number) => ({
            time: chartData[index]?.time,
            value: value
          })).filter((item: any) => item.time && !isNaN(item.value))
          stochSeriesRef.current.stochK.setData(kData)
        }
        if (stochData.d && stochSeriesRef.current.stochD) {
          const dData = stochData.d.map((value: number, index: number) => ({
            time: chartData[index]?.time,
            value: value
          })).filter((item: any) => item.time && !isNaN(item.value))
          stochSeriesRef.current.stochD.setData(dData)
        }
      }

      // 渲染OBV指标
      if (indicatorData.OBV && obvSeriesRef.current) {
        const obvData = indicatorData.OBV.map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && !isNaN(item.value))
        obvSeriesRef.current.setData(obvData)
      }

      // 渲染ADX指标
      if (indicatorData.ADX && adxSeriesRef.current) {
        const adxData = indicatorData.ADX.map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && !isNaN(item.value))
        adxSeriesRef.current.setData(adxData)
      }

      // 渲染Williams %R指标
      if (indicatorData.WILLIAMS_R && williamsRSeriesRef.current) {
        const wrData = indicatorData.WILLIAMS_R.map((value: number, index: number) => ({
          time: chartData[index]?.time,
          value: value
        })).filter((item: any) => item.time && !isNaN(item.value))
        williamsRSeriesRef.current.setData(wrData)
      }

      // 渲染Keltner通道
      if (indicatorData.KELTNER) {
        if (indicatorData.KELTNER.upper && keltnerUpperSeriesRef.current) {
          const upData = indicatorData.KELTNER.upper.map((v: number, i: number) => ({
            time: chartData[i]?.time, value: v
          })).filter((item: any) => item.time && !isNaN(item.value))
          keltnerUpperSeriesRef.current.setData(upData)
        }
        if (indicatorData.KELTNER.middle && keltnerMiddleSeriesRef.current) {
          const midData = indicatorData.KELTNER.middle.map((v: number, i: number) => ({
            time: chartData[i]?.time, value: v
          })).filter((item: any) => item.time && !isNaN(item.value))
          keltnerMiddleSeriesRef.current.setData(midData)
        }
        if (indicatorData.KELTNER.lower && keltnerLowerSeriesRef.current) {
          const loData = indicatorData.KELTNER.lower.map((v: number, i: number) => ({
            time: chartData[i]?.time, value: v
          })).filter((item: any) => item.time && !isNaN(item.value))
          keltnerLowerSeriesRef.current.setData(loData)
        }
      }

      // 渲染Ichimoku一目均衡表
      if (indicatorData.ICHIMOKU) {
        const im = indicatorData.ICHIMOKU
        const mapLine = (arr: number[], series: any) => {
          if (series && arr) {
            const data = arr.map((v: number, i: number) => ({
              time: chartData[i]?.time, value: v
            })).filter((item: any) => item.time && !isNaN(item.value) && item.value > 0)
            series.setData(data)
          }
        }
        mapLine(im.tenkan, ichimokuTenkanRef.current)
        mapLine(im.kijun, ichimokuKijunRef.current)
        mapLine(im.senkou_a, ichimokuSenkouARef.current)
        mapLine(im.senkou_b, ichimokuSenkouBRef.current)
        mapLine(im.chikou, ichimokuChikouRef.current)
      }

      // 渲染BOLL布林带
      if (indicatorData.BOLL) {
        const bollData = indicatorData.BOLL
        if (bollData.upper && bollUpperSeriesRef.current) {
          const upperData = bollData.upper.map((value: number, index: number) => ({
            time: chartData[index]?.time,
            value: value
          })).filter((item: any) => item.time && !isNaN(item.value))
          bollUpperSeriesRef.current.setData(upperData)
        }
        if (bollData.middle && bollMiddleSeriesRef.current) {
          const middleData = bollData.middle.map((value: number, index: number) => ({
            time: chartData[index]?.time,
            value: value
          })).filter((item: any) => item.time && !isNaN(item.value))
          bollMiddleSeriesRef.current.setData(middleData)
        }
        if (bollData.lower && bollLowerSeriesRef.current) {
          const lowerData = bollData.lower.map((value: number, index: number) => ({
            time: chartData[index]?.time,
            value: value
          })).filter((item: any) => item.time && !isNaN(item.value))
          bollLowerSeriesRef.current.setData(lowerData)
        }
      }
    }
  }, [chartData, chartType, indicatorData, chartReadyVersion])

  // 控制主图指标显示/隐藏 - 纯UI操作，不重绘图表
  useEffect(() => {
    const overlayOptions = { priceLineVisible: false, lastValueVisible: false }

    // 移动平均线
    if (ma5SeriesRef.current) {
      ma5SeriesRef.current.applyOptions({ visible: selectedIndicators.includes('MA5'), ...overlayOptions })
    }
    if (ma10SeriesRef.current) {
      ma10SeriesRef.current.applyOptions({ visible: selectedIndicators.includes('MA10'), ...overlayOptions })
    }
    if (ma20SeriesRef.current) {
      ma20SeriesRef.current.applyOptions({ visible: selectedIndicators.includes('MA20'), ...overlayOptions })
    }

    // EMA指标
    if (ema20SeriesRef.current) {
      ema20SeriesRef.current.applyOptions({ visible: selectedIndicators.includes('EMA20'), ...overlayOptions })
    }
    if (ema50SeriesRef.current) {
      ema50SeriesRef.current.applyOptions({ visible: selectedIndicators.includes('EMA50'), ...overlayOptions })
    }
    if (ema100SeriesRef.current) {
      ema100SeriesRef.current.applyOptions({ visible: selectedIndicators.includes('EMA100'), ...overlayOptions })
    }
    if (vwapSeriesRef.current) {
      vwapSeriesRef.current.applyOptions({ visible: selectedIndicators.includes('VWAP'), ...overlayOptions })
    }

    // BOLL布林带
    const showBoll = selectedIndicators.includes('BOLL')
    if (bollUpperSeriesRef.current) {
      bollUpperSeriesRef.current.applyOptions({ visible: showBoll, ...overlayOptions })
    }
    if (bollMiddleSeriesRef.current) {
      bollMiddleSeriesRef.current.applyOptions({ visible: showBoll, ...overlayOptions })
    }
    if (bollLowerSeriesRef.current) {
      bollLowerSeriesRef.current.applyOptions({ visible: showBoll, ...overlayOptions })
    }

    const showKeltner = selectedIndicators.includes('KELTNER')
    keltnerUpperSeriesRef.current?.applyOptions({ visible: showKeltner, ...overlayOptions })
    keltnerMiddleSeriesRef.current?.applyOptions({ visible: showKeltner, ...overlayOptions })
    keltnerLowerSeriesRef.current?.applyOptions({ visible: showKeltner, ...overlayOptions })

    const showIchimoku = selectedIndicators.includes('ICHIMOKU')
    ichimokuTenkanRef.current?.applyOptions({ visible: showIchimoku, ...overlayOptions })
    ichimokuKijunRef.current?.applyOptions({ visible: showIchimoku, ...overlayOptions })
    ichimokuSenkouARef.current?.applyOptions({ visible: showIchimoku, ...overlayOptions })
    ichimokuSenkouBRef.current?.applyOptions({ visible: showIchimoku, ...overlayOptions })
    ichimokuChikouRef.current?.applyOptions({ visible: showIchimoku, ...overlayOptions })
  }, [selectedIndicators, chartReadyVersion])

  // 更新指标 pane 标签
  const updateIndicatorPaneLabel = (labelText: string) => {
    if (indicatorPaneRef.current && indicatorLabelRef.current) {
      // 移除旧标签
      indicatorPaneRef.current.detachPrimitive(indicatorLabelRef.current)
      // 添加新标签
      const newLabel = createPaneLabel(labelText)
      indicatorPaneRef.current.attachPrimitive(newLabel)
      indicatorLabelRef.current = newLabel
    }
  }

  // 控制子图指标显示/隐藏 - 纯UI操作，不重绘图表
  useEffect(() => {
    const subplotIndicators = selectedIndicators.filter(ind => ['RSI14', 'RSI7', 'MACD', 'ATR14', 'STOCH', 'OBV'].includes(ind))
    const resolvedActiveSubplot = (activeSubplot && subplotIndicators.includes(activeSubplot))
      ? activeSubplot
      : subplotIndicators[0]

    // 检测新增的子图指标
    const prevSubplotIndicators = prevSubplotIndicatorsRef.current
    const newlyAddedIndicators = subplotIndicators.filter(ind => !prevSubplotIndicators.includes(ind))

    // 如果有新增的子图指标，自动切换到最新添加的指标
    if (newlyAddedIndicators.length > 0) {
      setActiveSubplot(newlyAddedIndicators[newlyAddedIndicators.length - 1])
    }
    // 设置默认激活的子图（仅在没有activeSubplot且有子图指标时）
    else if (subplotIndicators.length > 0 && !activeSubplot) {
      setActiveSubplot(subplotIndicators[0])
    }
    // 如果当前激活的子图不在选中列表中，切换到第一个可用的
    else if (activeSubplot && !subplotIndicators.includes(activeSubplot) && subplotIndicators.length > 0) {
      setActiveSubplot(subplotIndicators[0])
    }

    // 更新上一次的子图指标列表
    prevSubplotIndicatorsRef.current = subplotIndicators

    // 控制RSI显示
    if (rsiSeriesRef.current) {
      const showRSI = (resolvedActiveSubplot === 'RSI14' || resolvedActiveSubplot === 'RSI7') && selectedIndicators.includes(resolvedActiveSubplot)
      rsiSeriesRef.current.applyOptions({ visible: showRSI })
    }

    // 控制MACD显示
    if (macdSeriesRef.current) {
      const showMACD = resolvedActiveSubplot === 'MACD' && selectedIndicators.includes('MACD')
      if (macdSeriesRef.current.macdLine) {
        macdSeriesRef.current.macdLine.applyOptions({ visible: showMACD })
      }
      if (macdSeriesRef.current.signalLine) {
        macdSeriesRef.current.signalLine.applyOptions({ visible: showMACD })
      }
      if (macdSeriesRef.current.histogram) {
        macdSeriesRef.current.histogram.applyOptions({ visible: showMACD })
      }
    }

    // 控制ATR显示
    if (atrSeriesRef.current) {
      const showATR = resolvedActiveSubplot === 'ATR14' && selectedIndicators.includes('ATR14')
      atrSeriesRef.current.applyOptions({ visible: showATR })
    }

    // 控制STOCH显示
    if (stochSeriesRef.current) {
      const showSTOCH = resolvedActiveSubplot === 'STOCH' && selectedIndicators.includes('STOCH')
      if (stochSeriesRef.current.stochK) {
        stochSeriesRef.current.stochK.applyOptions({ visible: showSTOCH })
      }
      if (stochSeriesRef.current.stochD) {
        stochSeriesRef.current.stochD.applyOptions({ visible: showSTOCH })
      }
    }

    // 控制OBV显示
    if (obvSeriesRef.current) {
      const showOBV = resolvedActiveSubplot === 'OBV' && selectedIndicators.includes('OBV')
      obvSeriesRef.current.applyOptions({ visible: showOBV })
    }

    // Indicator pane label is fixed as "Indicators" - no need to update
  }, [selectedIndicators, activeSubplot])

  // Fetch market flow indicator data with loading state
  const fetchFlowData = async (indicator: string) => {
    if (!indicator || !symbol) return

    onIndicatorLoadingChange?.(true)
    try {
      const endTime = Date.now()
      const startTime = endTime - 7 * 24 * 60 * 60 * 1000 // 7 days
      const response = await fetch(
        `/api/market-flow/indicators?symbol=${symbol}&timeframe=${period}&start_time=${startTime}&end_time=${endTime}&indicators=${indicator}`
      )
      if (!response.ok) return

      const data = await response.json()
      setFlowDataAvailableFrom(data.data_available_from)
      const indicatorData = data.indicators[indicator] || []

      // Cache the data
      setFlowDataCache(prev => ({ ...prev, [indicator]: indicatorData }))

      // Update the series
      updateFlowSeries(indicator, indicatorData)
    } catch (error) {
      console.error('Failed to fetch flow data:', error)
    } finally {
      onIndicatorLoadingChange?.(false)
    }
  }

  // Get series ref for a flow indicator
  const getFlowSeriesRef = (indicator: string) => {
    switch (indicator) {
      case 'cvd': return flowCvdSeriesRef
      case 'taker_volume': return { buy: flowTakerBuySeriesRef, sell: flowTakerSellSeriesRef }
      case 'oi': return flowOiSeriesRef
      case 'oi_delta': return flowOiDeltaSeriesRef
      case 'funding': return flowFundingSeriesRef
      case 'depth_ratio': return flowDepthSeriesRef
      case 'order_imbalance': return flowImbalanceSeriesRef
      default: return null
    }
  }

  // Update flow series with data
  const updateFlowSeries = (indicator: string, data: any[]) => {
    if (!data || data.length === 0) return

    const colors = FLOW_COLORS[indicator]

    if (indicator === 'taker_volume') {
      if (flowTakerBuySeriesRef.current) {
        const buyData = data.map(d => ({
          time: formatChartTime(d.time),
          value: d.buy || 0,
          color: colors.up
        }))
        flowTakerBuySeriesRef.current.setData(buyData)
      }
      if (flowTakerSellSeriesRef.current) {
        const sellData = data.map(d => ({
          time: formatChartTime(d.time),
          value: -(d.sell || 0),
          color: colors.down
        }))
        flowTakerSellSeriesRef.current.setData(sellData)
      }
    } else {
      const seriesRef = getFlowSeriesRef(indicator)
      if (seriesRef && 'current' in seriesRef && seriesRef.current) {
        if (['oi_delta', 'order_imbalance'].includes(indicator)) {
          const histData = data.map(d => ({
            time: formatChartTime(d.time),
            value: d.value || 0,
            color: (d.value || 0) >= 0 ? colors.up : colors.down
          }))
          seriesRef.current.setData(histData)
        } else if (indicator === 'depth_ratio') {
          // Use log scale for depth_ratio to handle extreme values
          const lineData = data.map(d => ({
            time: formatChartTime(d.time),
            value: d.value > 0 ? Math.log10(d.value) : 0
          }))
          seriesRef.current.setData(lineData)
        } else if (indicator === 'funding') {
          // Multiply by 10000 to convert to basis points (bps) for better display
          // e.g., 0.000292% becomes 2.92 bps
          const lineData = data.map(d => ({
            time: formatChartTime(d.time),
            value: (d.value || 0) * 10000
          }))
          seriesRef.current.setData(lineData)
        } else {
          const lineData = data.map(d => ({
            time: formatChartTime(d.time),
            value: d.value
          }))
          seriesRef.current.setData(lineData)
        }
      }
    }
  }

  // Update flow pane label
  const updateFlowPaneLabel = (indicator: string) => {
    if (flowLabelRef.current && flowLabelRef.current.updateText) {
      flowLabelRef.current.updateText(FLOW_LABELS[indicator] || indicator)
    }
  }

  // Hide all flow series
  const hideAllFlowSeries = () => {
    flowCvdSeriesRef.current?.applyOptions({ visible: false })
    flowTakerBuySeriesRef.current?.applyOptions({ visible: false })
    flowTakerSellSeriesRef.current?.applyOptions({ visible: false })
    flowOiSeriesRef.current?.applyOptions({ visible: false })
    flowOiDeltaSeriesRef.current?.applyOptions({ visible: false })
    flowFundingSeriesRef.current?.applyOptions({ visible: false })
    flowDepthSeriesRef.current?.applyOptions({ visible: false })
    flowImbalanceSeriesRef.current?.applyOptions({ visible: false })
  }

  // Show specific flow series
  const showFlowSeries = (indicator: string) => {
    hideAllFlowSeries()
    if (indicator === 'taker_volume') {
      flowTakerBuySeriesRef.current?.applyOptions({ visible: true })
      flowTakerSellSeriesRef.current?.applyOptions({ visible: true })
    } else {
      const seriesRef = getFlowSeriesRef(indicator)
      if (seriesRef && 'current' in seriesRef && seriesRef.current) {
        seriesRef.current.applyOptions({ visible: true })
      }
    }
  }

  // 获取K线数据和指标
  const fetchKlineData = async (forceAllIndicators = false) => {
    if (loading) return

    setLoading(true)
    onIndicatorLoadingChange?.(true)
    onLoadingChange(true)
    
    // 设置30秒超时保护
    const timeoutId = setTimeout(() => {
      console.error('K线数据加载超时')
      setLoading(false)
      onLoadingChange(false)
      onIndicatorLoadingChange?.(false)
      setHasData(false)
    }, 30000)
    
    try {
      // 始终请求当前选中的指标，避免缓存缺失
      const indicatorsToFetch = selectedIndicators
      const indicatorsParam = indicatorsToFetch.length > 0 ? `&indicators=${indicatorsToFetch.join(',')}` : ''
      const url = `/api/market/kline-with-indicators/${symbol}?market=${market}&period=${period}&count=500${indicatorsParam}`
      
      console.log(`Fetching kline data: ${url}`)
      
      const response = await fetch(url)
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
      
      const result = await response.json()
      
      console.log(`Received kline data: ${result.klines?.length || 0} records`)

      if (result.klines && result.klines.length > 0) {
        const newChartData = result.klines.map((item: any) => ({
          time: formatChartTime(item.timestamp),
          open: item.open || 0,
          high: item.high || 0,
          low: item.low || 0,
          close: item.close || 0,
          volume: item.volume || 0,
        }))

        setChartData(newChartData)

        // 合并新获取的指标数据
        if (result.indicators) {
          setIndicatorData(prev => ({ ...prev, ...result.indicators }))
          setCachedIndicators(prev => [...new Set([...prev, ...indicatorsToFetch])])
        }

        // 通知父组件最新数据，用于 AI 分析启用按钮
        if (onDataUpdate) {
          onDataUpdate(newChartData, result.indicators || {})
        }

        setHasData(true)
      } else {
        console.warn('K线数据为空')
        setHasData(false)
      }
    } catch (error) {
      console.error('Failed to fetch kline data:', error)
      setHasData(false)
    } finally {
      clearTimeout(timeoutId)
      setLoading(false)
      onLoadingChange(false)
      onIndicatorLoadingChange?.(false)
    }
  }

  // 当symbol或period变化时清空缓存并重新获取数据
  useEffect(() => {
    if (symbol && period) {
      // 立即清空图表数据和缓存
      setHasData(false)
      setChartData([])
      setIndicatorData({})
      setCachedIndicators([])

      // 清空所有series数据，避免新旧数据混合
      if (seriesRef.current) seriesRef.current.setData([])
      if (volumeSeriesRef.current) volumeSeriesRef.current.setData([])
      if (ma5SeriesRef.current) ma5SeriesRef.current.setData([])
      if (ma10SeriesRef.current) ma10SeriesRef.current.setData([])
      if (ma20SeriesRef.current) ma20SeriesRef.current.setData([])
      if (ema20SeriesRef.current) ema20SeriesRef.current.setData([])
      if (ema50SeriesRef.current) ema50SeriesRef.current.setData([])
      if (ema100SeriesRef.current) ema100SeriesRef.current.setData([])
      if (vwapSeriesRef.current) vwapSeriesRef.current.setData([])
      if (bollUpperSeriesRef.current) bollUpperSeriesRef.current.setData([])
      if (bollMiddleSeriesRef.current) bollMiddleSeriesRef.current.setData([])
      if (bollLowerSeriesRef.current) bollLowerSeriesRef.current.setData([])
      if (rsiSeriesRef.current) rsiSeriesRef.current.setData([])
      if (macdSeriesRef.current?.macdLine) macdSeriesRef.current.macdLine.setData([])
      if (macdSeriesRef.current?.signalLine) macdSeriesRef.current.signalLine.setData([])
      if (macdSeriesRef.current?.histogram) macdSeriesRef.current.histogram.setData([])
      if (atrSeriesRef.current) atrSeriesRef.current.setData([])
      if (stochSeriesRef.current?.stochK) stochSeriesRef.current.stochK.setData([])
      if (stochSeriesRef.current?.stochD) stochSeriesRef.current.stochD.setData([])
      if (obvSeriesRef.current) obvSeriesRef.current.setData([])
      if (adxSeriesRef.current) adxSeriesRef.current.setData([])
      if (williamsRSeriesRef.current) williamsRSeriesRef.current.setData([])
      if (keltnerUpperSeriesRef.current) keltnerUpperSeriesRef.current.setData([])
      if (keltnerMiddleSeriesRef.current) keltnerMiddleSeriesRef.current.setData([])
      if (keltnerLowerSeriesRef.current) keltnerLowerSeriesRef.current.setData([])
      if (ichimokuTenkanRef.current) ichimokuTenkanRef.current.setData([])
      if (ichimokuKijunRef.current) ichimokuKijunRef.current.setData([])
      if (ichimokuSenkouARef.current) ichimokuSenkouARef.current.setData([])
      if (ichimokuSenkouBRef.current) ichimokuSenkouBRef.current.setData([])
      if (ichimokuChikouRef.current) ichimokuChikouRef.current.setData([])
      if (compareLineSeriesRef.current) {
        compareLineSeriesRef.current.setData([])
        compareLineSeriesRef.current.applyOptions({ visible: false })
      }

      // 强制请求所有选中指标
      fetchKlineData(true)

      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current)
      }
      refreshIntervalRef.current = setInterval(async () => {
        try {
          const indicatorsParam = selectedIndicators.length > 0 ? `&indicators=${selectedIndicators.join(',')}` : ''
          const url = `/api/market/kline-with-indicators/${symbol}?market=${market}&period=${period}&count=5${indicatorsParam}`
          const res = await fetch(url)
          if (!res.ok) return
          const result = await res.json()
          if (result.klines && result.klines.length > 0) {
            setChartData(prev => {
              const updated = result.klines.map((item: any) => ({
                time: formatChartTime(item.timestamp),
                open: item.open || 0,
                high: item.high || 0,
                low: item.low || 0,
                close: item.close || 0,
                volume: item.volume || 0,
              }))
              if (prev.length === 0) return updated
              const merged = [...prev]
              for (const bar of updated) {
                const idx = merged.findIndex((b: any) => b.time === bar.time)
                if (idx >= 0) merged[idx] = bar
                else merged.push(bar)
              }
              return merged.sort((a: any, b: any) => (a.time < b.time ? -1 : 1))
            })
            if (result.indicators) {
              setIndicatorData((prev: any) => ({ ...prev, ...result.indicators }))
            }
          }
        } catch {}
      }, 5000)

      // 清理定时器
      return () => {
        if (refreshIntervalRef.current) {
          clearInterval(refreshIntervalRef.current)
          refreshIntervalRef.current = null
        }
      }
    }
  }, [symbol, period, market])

  // 对比交易所叠加线
  useEffect(() => {
    if (!compareMarket || compareMarket === market || !symbol || !period) {
      if (compareLineSeriesRef.current) {
        compareLineSeriesRef.current.setData([])
        compareLineSeriesRef.current.applyOptions({ visible: false })
      }
      return
    }

    let cancelled = false

    const fetchCompareKlines = async () => {
      try {
        const url = `/api/market/kline-with-indicators/${symbol}?market=${compareMarket}&period=${period}&count=500`
        const res = await fetch(url)
        if (!res.ok || cancelled) return
        const result = await res.json()
        if (!result.klines?.length || cancelled || !chartRef.current) return

        const lineData = result.klines.map((item: any) => ({
          time: formatChartTime(item.timestamp),
          value: item.close || 0,
        }))

        if (!compareLineSeriesRef.current) {
          compareLineSeriesRef.current = chartRef.current.addSeries(LineSeries, {
            color: '#f97316',
            lineWidth: 2,
            lineStyle: 2,
            title: compareMarket,
            visible: true,
          })
        } else {
          compareLineSeriesRef.current.applyOptions({ visible: true, title: compareMarket })
        }
        compareLineSeriesRef.current.setData(lineData)
      } catch (err) {
        console.warn('Failed to fetch compare klines:', err)
      }
    }

    fetchCompareKlines()
    return () => { cancelled = true }
  }, [compareMarket, market, symbol, period, wsRefreshKey, chartReadyVersion])

  // WebSocket 推送触发轻量刷新（与 5s 轮询逻辑一致）
  useEffect(() => {
    if (!wsRefreshKey || !symbol || !period || !chartRef.current) return

    const doRefresh = async () => {
      try {
        const indicatorsParam = selectedIndicators.length > 0 ? `&indicators=${selectedIndicators.join(',')}` : ''
        const url = `/api/market/kline-with-indicators/${symbol}?market=${market}&period=${period}&count=5${indicatorsParam}`
        const res = await fetch(url)
        if (!res.ok) return
        const result = await res.json()
        if (result.klines && result.klines.length > 0) {
          setChartData(prev => {
            const updated = result.klines.map((item: any) => ({
              time: formatChartTime(item.timestamp),
              open: item.open || 0,
              high: item.high || 0,
              low: item.low || 0,
              close: item.close || 0,
              volume: item.volume || 0,
            }))
            if (prev.length === 0) return updated
            const merged = [...prev]
            for (const bar of updated) {
              const idx = merged.findIndex((b: any) => b.time === bar.time)
              if (idx >= 0) merged[idx] = bar
              else merged.push(bar)
            }
            return merged.sort((a: any, b: any) => (a.time < b.time ? -1 : 1))
          })
          if (result.indicators) {
            setIndicatorData((prev: any) => ({ ...prev, ...result.indicators }))
          }
        }
      } catch {}
    }
    doRefresh()
  }, [wsRefreshKey])

  // 当指标选择变化时，检查并获取缺失的指标数据
  useEffect(() => {
    if (symbol && period && selectedIndicators.length > 0) {
      const missingIndicators = selectedIndicators.filter(ind =>
        !cachedIndicators.includes(ind) || !indicatorData[ind]
      )
      if (missingIndicators.length > 0) {
        fetchKlineData()
      }
    }
  }, [selectedIndicators])

  // Handle market flow indicator changes - similar to technical indicators
  useEffect(() => {
    if (!chartRef.current) {
      console.log('[FlowPane] No chart ref, skipping')
      return
    }

    const chart = chartRef.current
    const hasFlowIndicators = selectedFlowIndicators.length > 0
    console.log('[FlowPane] hasFlowIndicators:', hasFlowIndicators, 'flowPaneRef:', !!flowPaneRef.current)

    if (hasFlowIndicators) {
      // Create flow pane if not exists
      if (!flowPaneRef.current) {
        const flowPane = chart.addPane()
        flowPane.setStretchFactor(1)
        const labelPrimitive = createPaneLabel('Market Flow')
        flowPane.attachPrimitive(labelPrimitive)
        flowLabelRef.current = labelPrimitive
        flowPaneRef.current = flowPane

        // Pre-create all series (initially hidden)
        // CVD - Line
        flowCvdSeriesRef.current = flowPane.addSeries(LineSeries, {
          color: FLOW_COLORS.cvd.line, lineWidth: 2, visible: false,
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        })
        // Taker Volume - Dual Histogram
        flowTakerBuySeriesRef.current = flowPane.addSeries(HistogramSeries, {
          color: FLOW_COLORS.taker_volume.up, visible: false,
          priceFormat: { type: 'volume' }
        })
        flowTakerSellSeriesRef.current = flowPane.addSeries(HistogramSeries, {
          color: FLOW_COLORS.taker_volume.down, visible: false,
          priceFormat: { type: 'volume' }
        })
        // OI - Line
        flowOiSeriesRef.current = flowPane.addSeries(LineSeries, {
          color: FLOW_COLORS.oi.line, lineWidth: 2, visible: false,
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        })
        // OI Delta - Histogram
        flowOiDeltaSeriesRef.current = flowPane.addSeries(HistogramSeries, {
          color: FLOW_COLORS.oi_delta.line, visible: false,
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        })
        // Funding - Line (values converted to bps, e.g., 0.000292% -> 2.92 bps)
        flowFundingSeriesRef.current = flowPane.addSeries(LineSeries, {
          color: FLOW_COLORS.funding.line, lineWidth: 2, visible: false,
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        })
        // Depth Ratio - Line
        flowDepthSeriesRef.current = flowPane.addSeries(LineSeries, {
          color: FLOW_COLORS.depth_ratio.line, lineWidth: 2, visible: false,
          priceFormat: { type: 'price', precision: 4, minMove: 0.0001 }
        })
        // Order Imbalance - Histogram
        flowImbalanceSeriesRef.current = flowPane.addSeries(HistogramSeries, {
          color: FLOW_COLORS.order_imbalance.line, visible: false,
          priceFormat: { type: 'price', precision: 4, minMove: 0.0001 }
        })
        // Update pane positions after flow pane created
        updatePanePositions()
      }

      // Detect newly added indicators
      const prevFlowIndicators = prevFlowIndicatorsRef.current
      const newlyAdded = selectedFlowIndicators.filter(ind => !prevFlowIndicators.includes(ind))

      // Auto-switch to newly added indicator
      if (newlyAdded.length > 0) {
        setActiveFlowIndicator(newlyAdded[newlyAdded.length - 1])
      }
      // Set default if no active indicator
      else if (!activeFlowIndicator || !selectedFlowIndicators.includes(activeFlowIndicator)) {
        setActiveFlowIndicator(selectedFlowIndicators[0])
      }

      // Update previous indicators ref
      prevFlowIndicatorsRef.current = selectedFlowIndicators

    } else {
      // Remove flow pane if no indicators selected
      console.log('[FlowPane] Removing pane, flowPaneRef:', !!flowPaneRef.current)
      if (flowPaneRef.current) {
        // Find the pane index before clearing refs
        const panes = chart.panes()
        const paneIndex = panes.indexOf(flowPaneRef.current)
        console.log('[FlowPane] Pane index:', paneIndex, 'Total panes:', panes.length)

        // Clear refs first to prevent any further operations
        flowPaneRef.current = null
        flowLabelRef.current = null
        flowCvdSeriesRef.current = null
        flowTakerBuySeriesRef.current = null
        flowTakerSellSeriesRef.current = null
        flowOiSeriesRef.current = null
        flowOiDeltaSeriesRef.current = null
        flowFundingSeriesRef.current = null
        flowDepthSeriesRef.current = null
        flowImbalanceSeriesRef.current = null

        // Now remove the pane by index (removePane takes index, not pane object)
        if (paneIndex > 0) {
          try {
            console.log('[FlowPane] Calling chart.removePane with index:', paneIndex)
            chart.removePane(paneIndex)
            console.log('[FlowPane] removePane succeeded')
            // Update pane positions after flow pane removed
            updatePanePositions()
          } catch (e) {
            console.warn('[FlowPane] Failed to remove flow pane:', e)
          }
        }
        setActiveFlowIndicator(null)
        setFlowDataCache({})
        setFlowDataAvailableFrom(null)
      }
      prevFlowIndicatorsRef.current = []
    }
  }, [selectedFlowIndicators])

  // Handle active flow indicator changes - show/hide series and fetch data
  useEffect(() => {
    if (!activeFlowIndicator || !flowPaneRef.current) return

    // Show the active series
    showFlowSeries(activeFlowIndicator)

    // Update label
    updateFlowPaneLabel(activeFlowIndicator)

    // Fetch data if not cached
    if (!flowDataCache[activeFlowIndicator]) {
      fetchFlowData(activeFlowIndicator)
    } else {
      // Use cached data
      updateFlowSeries(activeFlowIndicator, flowDataCache[activeFlowIndicator])
    }
  }, [activeFlowIndicator])

  // Re-fetch flow data when symbol or period changes
  useEffect(() => {
    if (selectedFlowIndicators.length > 0 && flowPaneRef.current) {
      // Clear all flow series data first (consistent with main chart behavior)
      if (flowCvdSeriesRef.current) flowCvdSeriesRef.current.setData([])
      if (flowTakerBuySeriesRef.current) flowTakerBuySeriesRef.current.setData([])
      if (flowTakerSellSeriesRef.current) flowTakerSellSeriesRef.current.setData([])
      if (flowOiSeriesRef.current) flowOiSeriesRef.current.setData([])
      if (flowOiDeltaSeriesRef.current) flowOiDeltaSeriesRef.current.setData([])
      if (flowFundingSeriesRef.current) flowFundingSeriesRef.current.setData([])
      if (flowDepthSeriesRef.current) flowDepthSeriesRef.current.setData([])
      if (flowImbalanceSeriesRef.current) flowImbalanceSeriesRef.current.setData([])
      // Clear cache and re-fetch active indicator
      setFlowDataCache({})
      if (activeFlowIndicator) {
        fetchFlowData(activeFlowIndicator)
      }
    }
  }, [symbol, period])

  console.log('[Render] TradingViewChart render. loading:', loading, 'hasData:', hasData, 'chartData.length:', chartData.length, 'chartRef:', !!chartRef.current, 'seriesRef:', !!seriesRef.current)

  return (
    <div className="absolute inset-0 overflow-hidden">
      {/* 图表容器 - absolute inset-0 确保填满父容器 */}
      <div ref={chartContainerRef} className="absolute inset-0" />


      {/* 指标子图切换器 - positioned at indicator pane top-left */}
      {(() => {
        const subplotIndicators = selectedIndicators.filter(ind => ['RSI14', 'RSI7', 'MACD', 'ATR14', 'STOCH', 'OBV'].includes(ind))
        // Always show selector when there are indicators (1 or more)
        if (subplotIndicators.length === 0 || indicatorPaneTop === null) return null

        const currentActiveSubplot = activeSubplot || subplotIndicators[0]

        return (
          <div
            className="absolute left-2 z-10 flex items-center bg-background/80 backdrop-blur-sm rounded-md p-1 px-2 border text-xs"
            style={{ top: indicatorPaneTop + 4 }}
          >
            <select
              value={currentActiveSubplot}
              onChange={(e) => setActiveSubplot(e.target.value)}
              className="bg-transparent border-0 text-xs focus:outline-none cursor-pointer"
              disabled={subplotIndicators.length === 1}
            >
              {subplotIndicators.map(indicator => (
                <option key={indicator} value={indicator}>
                  {indicator}
                </option>
              ))}
            </select>
          </div>
        )
      })()}

      {/* Market Flow indicator selector - positioned at flow pane top-left */}
      {/* Always show selector when there are flow indicators (1 or more) */}
      {selectedFlowIndicators.length > 0 && activeFlowIndicator && flowPaneTop !== null && (
        <div
          className="absolute left-2 z-10 flex items-center gap-2 bg-background/80 backdrop-blur-sm rounded-md p-1 px-2 border text-xs"
          style={{ top: flowPaneTop + 4 }}
        >
          <select
            value={activeFlowIndicator}
            onChange={(e) => setActiveFlowIndicator(e.target.value)}
            className="bg-transparent border-0 text-xs focus:outline-none cursor-pointer text-cyan-400"
            disabled={selectedFlowIndicators.length === 1}
          >
            {selectedFlowIndicators.map(indicator => (
              <option key={indicator} value={indicator}>
                {FLOW_LABELS[indicator]}
              </option>
            ))}
          </select>
          {flowDataAvailableFrom && (
            <span className="text-muted-foreground">
              from {new Date(flowDataAvailableFrom).toLocaleDateString()}
            </span>
          )}
        </div>
      )}

      {/* 自定义水印 */}
      <div className="absolute bottom-2 right-2 text-xs text-muted-foreground/30 pointer-events-none select-none">
        Herdalv Alpha Arena
      </div>


      {!loading && !hasData && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center text-muted-foreground">
            <p className="text-lg font-medium">No K-line data available</p>
            <p className="text-sm">Click "Backfill Historical Data" to fetch data</p>
          </div>
        </div>
      )}
    </div>
  )
}
