import { useState, useEffect, useCallback, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import {
  Drawer, DrawerContent, DrawerHeader, DrawerTitle, DrawerDescription,
} from '../ui/drawer';
import {
  Dna, Play, Square, RefreshCw, Trophy, TrendingUp, TrendingDown,
  BarChart3, Target, Zap, Clock, Activity, CheckCircle2, AlertTriangle,
  FlaskConical, Brain, ChevronDown, ChevronUp, ChevronLeft, ChevronRight,
  X, Rocket, ArrowRight, Shield, Eye, Crosshair, Plus, Search,
} from 'lucide-react';
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs';

const CHAMPIONS_PAGE_SIZE = 5;

// ═══ 类型定义 ═══

interface AiLearningEntry {
  gen: number;
  best_sharpe: number;
  best_wr: number;
  best_mdd: number;
  trades: number;
  ai_diagnosis: string;
  ai_action: string;
}

interface EvolutionProgress {
  running: boolean;
  status: string;
  current_template: string;
  current_generation: number;
  total_templates: number;
  completed_templates: number;
  total_backtests: number;
  completed_backtests: number;
  champions: Array<{ template: string; sharpe: number; win_rate: number; return: number }>;
  ai_phase?: string;
  ai_learning_log?: AiLearningEntry[];
  started_at?: string;
}

interface BacktestRunItem {
  run_id: string;
  template_id: string;
  strategy_name: string;
  symbol: string;
  timeframe: string;
  status: string;
  generation: number;
  is_champion: boolean;
  strategy_config?: Record<string, any>;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  final_equity: number;
  duration_seconds: number;
  bars_total: number;
  created_at: string;
}

interface TradeItem {
  side: string;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  exit_reason: string;
  entry_time: string;
  exit_time: string;
  entry_bar: number;
  exit_bar: number;
  quantity: number;
  leverage: number;
  fee: number;
}

interface RunDetail {
  run: BacktestRunItem & {
    annualized_return: number;
    avg_trade_return: number;
    max_consecutive_wins: number;
    max_consecutive_losses: number;
    avg_holding_bars: number;
    equity_curve: number[] | null;
  };
  trades: TradeItem[];
}

interface StrategyTemplate {
  template_id: string;
  name: string;
  category: string;
  tier?: string;
}

const TIER_OPTIONS = [
  { value: 'short', label: '短线 (5m~15m)', desc: '日内高频，持仓数小时' },
  { value: 'mid', label: '中线 (1h~4h)', desc: '波段交易，持仓数天' },
  { value: 'long', label: '长线 (4h~1d)', desc: '趋势跟随，持仓数周' },
];

const DATA_RANGE_OPTIONS = [
  { value: 180, label: '半年' },
  { value: 365, label: '1 年' },
  { value: 730, label: '2 年' },
  { value: 1095, label: '3 年' },
];

const DEPTH_OPTIONS = [
  { value: 3, label: '快速', desc: '~3分钟，粗略筛选' },
  { value: 5, label: '标准', desc: '~8分钟，推荐' },
  { value: 10, label: '深度', desc: '~20分钟，精细优化' },
];

// ═══ Canvas 资金曲线 ═══

function EquityCurveCanvas({ data, height = 200 }: { data: number[]; height?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data || data.length < 2) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width;
    const H = rect.height;

    ctx.clearRect(0, 0, W, H);

    const minVal = Math.min(...data);
    const maxVal = Math.max(...data);
    const range = maxVal - minVal || 1;
    const pad = 8;

    const toX = (i: number) => pad + (i / (data.length - 1)) * (W - 2 * pad);
    const toY = (v: number) => pad + (1 - (v - minVal) / range) * (H - 2 * pad);

    let peakIdx = 0, mddStart = 0, mddEnd = 0, mddVal = 0;
    let peak = data[0];
    for (let i = 1; i < data.length; i++) {
      if (data[i] > peak) { peak = data[i]; peakIdx = i; }
      const dd = (peak - data[i]) / peak;
      if (dd > mddVal) { mddVal = dd; mddStart = peakIdx; mddEnd = i; }
    }

    if (mddVal > 0.01) {
      ctx.fillStyle = 'rgba(239, 68, 68, 0.08)';
      ctx.beginPath();
      ctx.moveTo(toX(mddStart), 0);
      ctx.lineTo(toX(mddEnd), 0);
      ctx.lineTo(toX(mddEnd), H);
      ctx.lineTo(toX(mddStart), H);
      ctx.fill();
    }

    const baseline = data[0];
    ctx.strokeStyle = 'rgba(128,128,128,0.3)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(pad, toY(baseline));
    ctx.lineTo(W - pad, toY(baseline));
    ctx.stroke();
    ctx.setLineDash([]);

    const gradient = ctx.createLinearGradient(0, 0, 0, H);
    const isProfit = data[data.length - 1] >= data[0];
    if (isProfit) {
      gradient.addColorStop(0, 'rgba(16, 185, 129, 0.25)');
      gradient.addColorStop(1, 'rgba(16, 185, 129, 0.02)');
    } else {
      gradient.addColorStop(0, 'rgba(239, 68, 68, 0.02)');
      gradient.addColorStop(1, 'rgba(239, 68, 68, 0.25)');
    }

    ctx.beginPath();
    ctx.moveTo(toX(0), toY(data[0]));
    for (let i = 1; i < data.length; i++) ctx.lineTo(toX(i), toY(data[i]));
    ctx.lineTo(toX(data.length - 1), H - pad);
    ctx.lineTo(toX(0), H - pad);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(toX(0), toY(data[0]));
    for (let i = 1; i < data.length; i++) ctx.lineTo(toX(i), toY(data[i]));
    ctx.strokeStyle = isProfit ? '#10b981' : '#ef4444';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.font = '10px sans-serif';
    ctx.fillStyle = '#888';
    ctx.textAlign = 'left';
    ctx.fillText(`${maxVal.toFixed(0)}`, pad, pad + 10);
    ctx.fillText(`${minVal.toFixed(0)}`, pad, H - pad);
    ctx.textAlign = 'right';
    ctx.fillText(`最终: ${data[data.length - 1].toFixed(0)}`, W - pad, pad + 10);
    if (mddVal > 0.01) {
      ctx.fillStyle = '#ef4444';
      ctx.fillText(`MDD: -${(mddVal * 100).toFixed(1)}%`, W - pad, pad + 22);
    }
  }, [data, height]);

  if (!data || data.length < 2) {
    return <div className="flex items-center justify-center h-32 text-xs text-muted-foreground">暂无资金曲线数据</div>;
  }

  return <canvas ref={canvasRef} className="w-full" style={{ height }} />;
}

// ═══ 交易分析图表 ═══

function TradeAnalysisCharts({ trades }: { trades: TradeItem[] }) {
  if (!trades || trades.length === 0) return null;

  const exitReasons: Record<string, number> = {};
  trades.forEach(t => { exitReasons[t.exit_reason] = (exitReasons[t.exit_reason] || 0) + 1; });
  const reasonEntries = Object.entries(exitReasons).sort((a, b) => b[1] - a[1]);
  const reasonColors: Record<string, string> = { sl: '#ef4444', tp: '#10b981', trailing: '#3b82f6', end_of_data: '#6b7280' };

  const longCount = trades.filter(t => t.side === 'long').length;
  const shortCount = trades.length - longCount;
  const longPct = (longCount / trades.length) * 100;

  const pnls = trades.map(t => (t.pnl_pct || 0) * 100);
  const bins = [-Infinity, -5, -3, -1, 0, 1, 3, 5, Infinity];
  const binLabels = ['<-5%', '-5~-3', '-3~-1', '-1~0', '0~1', '1~3', '3~5', '>5%'];
  const binCounts = new Array(bins.length - 1).fill(0);
  pnls.forEach(p => { for (let i = 0; i < bins.length - 1; i++) { if (p >= bins[i] && p < bins[i + 1]) { binCounts[i]++; break; } } });
  const maxBin = Math.max(...binCounts, 1);

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div className="space-y-2">
        <div className="text-xs font-semibold text-muted-foreground">出场原因分布</div>
        {reasonEntries.map(([reason, count]) => (
          <div key={reason} className="flex items-center gap-2 text-xs">
            <div className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: reasonColors[reason] || '#9ca3af' }} />
            <span className="w-16">{reason === 'sl' ? '止损' : reason === 'tp' ? '止盈' : reason === 'trailing' ? '移动止损' : reason}</span>
            <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded-full h-2">
              <div className="h-2 rounded-full transition-all" style={{ width: `${(count / trades.length) * 100}%`, backgroundColor: reasonColors[reason] || '#9ca3af' }} />
            </div>
            <span className="font-mono w-10 text-right">{count} ({((count / trades.length) * 100).toFixed(0)}%)</span>
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <div className="text-xs font-semibold text-muted-foreground">盈亏分布 (PnL%)</div>
        <div className="flex items-end gap-0.5 h-20">
          {binCounts.map((c, i) => (
            <div key={i} className="flex-1 flex flex-col items-center justify-end">
              <div className="w-full rounded-t transition-all" style={{ height: `${(c / maxBin) * 100}%`, minHeight: c > 0 ? 4 : 0, backgroundColor: i < 4 ? '#ef4444' : '#10b981', opacity: 0.6 + (c / maxBin) * 0.4 }} />
            </div>
          ))}
        </div>
        <div className="flex gap-0.5 text-[9px] text-muted-foreground">
          {binLabels.map((l, i) => <div key={i} className="flex-1 text-center truncate">{l}</div>)}
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-xs font-semibold text-muted-foreground">多空比例</div>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-6 rounded-full overflow-hidden flex bg-gray-200 dark:bg-gray-700">
            <div className="h-full bg-green-500 transition-all flex items-center justify-center text-[10px] text-white font-bold" style={{ width: `${longPct}%` }}>
              {longPct >= 15 && `${longPct.toFixed(0)}%`}
            </div>
            <div className="h-full bg-red-500 transition-all flex items-center justify-center text-[10px] text-white font-bold" style={{ width: `${100 - longPct}%` }}>
              {(100 - longPct) >= 15 && `${(100 - longPct).toFixed(0)}%`}
            </div>
          </div>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-green-600">多 {longCount}笔</span>
          <span className="text-red-600">空 {shortCount}笔</span>
        </div>
        <div className="grid grid-cols-2 gap-2 mt-2">
          <div className="p-2 rounded bg-green-50 dark:bg-green-900/10 text-center">
            <div className="text-[10px] text-muted-foreground">多头胜率</div>
            <div className="text-sm font-bold text-green-600">
              {longCount > 0 ? ((trades.filter(t => t.side === 'long' && t.pnl > 0).length / longCount) * 100).toFixed(0) : 0}%
            </div>
          </div>
          <div className="p-2 rounded bg-red-50 dark:bg-red-900/10 text-center">
            <div className="text-[10px] text-muted-foreground">空头胜率</div>
            <div className="text-sm font-bold text-red-600">
              {shortCount > 0 ? ((trades.filter(t => t.side === 'short' && t.pnl > 0).length / shortCount) * 100).toFixed(0) : 0}%
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══ Walk-Forward 验证展示 ═══

function WalkForwardDisplay({ config }: { config: Record<string, any> }) {
  const valSharpe = config.wf_val_sharpe;
  const overfitRatio = config.wf_overfit_ratio;
  const overfitWarn = config.wf_overfit_warning;

  if (valSharpe === undefined && overfitRatio === undefined) return null;

  let riskLevel: 'green' | 'yellow' | 'red' = 'green';
  if (overfitRatio !== undefined) {
    if (overfitRatio > 0.6) riskLevel = 'red';
    else if (overfitRatio > 0.3) riskLevel = 'yellow';
  }
  if (overfitWarn) riskLevel = 'red';

  const riskColors = { green: 'bg-green-100 text-green-700 border-green-300 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800', yellow: 'bg-yellow-100 text-yellow-700 border-yellow-300 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800', red: 'bg-red-100 text-red-700 border-red-300 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800' };
  const riskLabels = { green: '低过拟合风险 — 策略有真实盈利能力', yellow: '中等过拟合风险 — 谨慎使用', red: '高过拟合风险 — 不建议实盘使用' };
  const riskIcons = { green: <Shield className="w-4 h-4" />, yellow: <AlertTriangle className="w-4 h-4" />, red: <AlertTriangle className="w-4 h-4" /> };

  return (
    <div className="space-y-3">
      <div className="text-xs font-semibold flex items-center gap-2">
        <Eye className="w-4 h-4 text-blue-500" /> Walk-Forward 验证（用策略没见过的数据验证）
      </div>
      {overfitRatio !== undefined && (
        <div className={`flex items-center gap-2 p-2.5 rounded-lg border ${riskColors[riskLevel]}`}>
          {riskIcons[riskLevel]}
          <div className="flex-1">
            <div className="text-xs font-bold">{riskLabels[riskLevel]}</div>
            <div className="text-[10px]">过拟合比率: {(overfitRatio * 100).toFixed(0)}%</div>
          </div>
        </div>
      )}
      <div className="grid grid-cols-4 gap-2 text-center">
        {[
          { label: 'Sharpe', val: config.wf_val_sharpe, fmt: (v: number) => v?.toFixed(2) ?? '-' },
          { label: '胜率', val: config.wf_val_win_rate, fmt: (v: number) => v != null ? `${(v * 100).toFixed(0)}%` : '-' },
          { label: '最大回撤', val: config.wf_val_max_drawdown, fmt: (v: number) => v != null ? `${(v * 100).toFixed(1)}%` : '-' },
          { label: '收益率', val: config.wf_val_total_return, fmt: (v: number) => v != null ? `${(v * 100).toFixed(1)}%` : '-' },
        ].map(({ label, val, fmt }) => (
          <div key={label} className="p-2 rounded bg-blue-50 dark:bg-blue-900/10">
            <div className="text-[10px] text-muted-foreground">验证集 {label}</div>
            <div className="text-sm font-bold">{fmt(val)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══ 回测详情抽屉 ═══

function BacktestDetailDrawer({ runId, isOpen, onClose, onDeploy }: {
  runId: string | null; isOpen: boolean; onClose: () => void;
  onDeploy?: (runId: string, config: Record<string, any>, name: string) => void;
}) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tradeTab, setTradeTab] = useState<'table' | 'analysis'>('table');

  useEffect(() => {
    if (!runId || !isOpen) { setDetail(null); return; }
    setLoading(true);
    fetch(`/api/backtest/runs/${runId}`)
      .then(r => r.json())
      .then(data => setDetail(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [runId, isOpen]);

  const run = detail?.run;
  const trades = detail?.trades || [];

  return (
    <Drawer open={isOpen} onOpenChange={v => !v && onClose()}>
      <DrawerContent className="max-h-[90vh]">
        <DrawerHeader className="flex-row items-center justify-between">
          <div>
            <DrawerTitle>{run?.strategy_name || '回测详情'}</DrawerTitle>
            <DrawerDescription>
              {run ? `${run.symbol} · ${run.timeframe} · ${run.total_trades}笔交易` : '加载中...'}
            </DrawerDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}><X className="w-4 h-4" /></Button>
        </DrawerHeader>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <RefreshCw className="w-6 h-6 animate-spin text-emerald-500" />
          </div>
        ) : run ? (
          <div className="space-y-4 overflow-auto">
            <div className="grid grid-cols-4 md:grid-cols-8 gap-2">
              {[
                { label: '总收益', value: `${((run.total_return || 0) * 100).toFixed(1)}%`, color: (run.total_return || 0) >= 0 ? 'text-green-600' : 'text-red-600', icon: <TrendingUp className="w-3.5 h-3.5" /> },
                { label: 'Sharpe', value: (run.sharpe_ratio || 0).toFixed(2), color: (run.sharpe_ratio || 0) >= 1 ? 'text-emerald-600' : 'text-orange-600', icon: <BarChart3 className="w-3.5 h-3.5" /> },
                { label: '最大回撤', value: `${((run.max_drawdown || 0) * 100).toFixed(1)}%`, color: 'text-red-600', icon: <TrendingDown className="w-3.5 h-3.5" /> },
                { label: '胜率', value: `${((run.win_rate || 0) * 100).toFixed(0)}%`, color: (run.win_rate || 0) >= 0.5 ? 'text-green-600' : 'text-orange-600', icon: <Target className="w-3.5 h-3.5" /> },
                { label: '盈亏比', value: (run.profit_factor || 0).toFixed(2), color: (run.profit_factor || 0) >= 1.3 ? 'text-green-600' : 'text-orange-600', icon: <Zap className="w-3.5 h-3.5" /> },
                { label: '平均持仓', value: `${(run.avg_holding_bars || 0).toFixed(1)} bars`, color: '', icon: <Clock className="w-3.5 h-3.5" /> },
                { label: '连胜', value: `${run.max_consecutive_wins || 0}`, color: 'text-green-600', icon: <CheckCircle2 className="w-3.5 h-3.5" /> },
                { label: '连亏', value: `${run.max_consecutive_losses || 0}`, color: 'text-red-600', icon: <AlertTriangle className="w-3.5 h-3.5" /> },
              ].map(m => (
                <div key={m.label} className="p-2 rounded-lg bg-gray-50 dark:bg-gray-800/50 text-center">
                  <div className="flex items-center justify-center gap-1 text-[10px] text-muted-foreground mb-0.5">{m.icon}{m.label}</div>
                  <div className={`text-sm font-bold ${m.color}`}>{m.value}</div>
                </div>
              ))}
            </div>

            <Card>
              <CardHeader className="py-2">
                <CardTitle className="text-xs flex items-center gap-1.5">
                  <Activity className="w-3.5 h-3.5" /> 资金曲线
                  <span className="ml-auto text-[10px] text-muted-foreground font-normal">
                    初始 {(run.equity_curve?.[0] || 10000).toFixed(0)} → 最终 {(run.final_equity || 0).toFixed(0)}
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-0">
                <EquityCurveCanvas data={run.equity_curve || []} height={180} />
              </CardContent>
            </Card>

            {run.is_champion && run.strategy_config && <WalkForwardDisplay config={run.strategy_config} />}

            <div className="flex items-center gap-2 border-b pb-2">
              <button onClick={() => setTradeTab('table')}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${tradeTab === 'table' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' : 'text-muted-foreground hover:bg-muted'}`}>
                交易明细 ({trades.length})
              </button>
              <button onClick={() => setTradeTab('analysis')}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${tradeTab === 'analysis' ? 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400' : 'text-muted-foreground hover:bg-muted'}`}>
                交易分析
              </button>
              {run.is_champion && onDeploy && run.strategy_config && (
                <Button size="sm" className="ml-auto bg-gradient-to-r from-yellow-500 to-orange-500 text-white h-7 text-xs"
                  onClick={() => onDeploy(run.run_id, run.strategy_config!, run.strategy_name || '')}>
                  <Rocket className="w-3.5 h-3.5 mr-1" /> 部署到实战
                </Button>
              )}
            </div>

            {tradeTab === 'table' ? (
              <div className="max-h-[250px] overflow-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-background z-10">
                    <tr className="border-b text-muted-foreground">
                      <th className="text-left py-1.5 px-2">方向</th>
                      <th className="text-right py-1.5 px-2">入场价</th>
                      <th className="text-right py-1.5 px-2">出场价</th>
                      <th className="text-right py-1.5 px-2">盈亏</th>
                      <th className="text-right py-1.5 px-2">PnL%</th>
                      <th className="text-center py-1.5 px-2">出场原因</th>
                      <th className="text-right py-1.5 px-2">入场时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((t, i) => (
                      <tr key={i} className="border-b border-gray-100 dark:border-gray-800 hover:bg-muted/30">
                        <td className="py-1 px-2">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${t.side === 'long' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}>
                            {t.side === 'long' ? '多' : '空'}
                          </span>
                        </td>
                        <td className="text-right py-1 px-2 font-mono">{t.entry_price?.toFixed(2)}</td>
                        <td className="text-right py-1 px-2 font-mono">{t.exit_price?.toFixed(2)}</td>
                        <td className={`text-right py-1 px-2 font-mono font-bold ${t.pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {t.pnl >= 0 ? '+' : ''}{t.pnl?.toFixed(2)}
                        </td>
                        <td className={`text-right py-1 px-2 font-mono ${(t.pnl_pct || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {((t.pnl_pct || 0) * 100).toFixed(2)}%
                        </td>
                        <td className="text-center py-1 px-2">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                            t.exit_reason === 'tp' ? 'bg-green-100 text-green-600' :
                            t.exit_reason === 'sl' ? 'bg-red-100 text-red-600' :
                            t.exit_reason === 'trailing' ? 'bg-blue-100 text-blue-600' :
                            'bg-gray-100 text-gray-600'
                          }`}>{t.exit_reason === 'sl' ? '止损' : t.exit_reason === 'tp' ? '止盈' : t.exit_reason === 'trailing' ? '追踪' : t.exit_reason}</span>
                        </td>
                        <td className="text-right py-1 px-2 text-muted-foreground">{t.entry_time || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {trades.length === 0 && <div className="text-center py-6 text-muted-foreground text-xs">暂无交易数据</div>}
              </div>
            ) : (
              <TradeAnalysisCharts trades={trades} />
            )}
          </div>
        ) : null}
      </DrawerContent>
    </Drawer>
  );
}

// ═══ 主面板 ═══

export default function BacktestEvolutionPanel() {
  const { symbols: configuredPairs } = useTradingPairs();
  const COMMON_SYMBOLS = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS;

  const [progress, setProgress] = useState<EvolutionProgress | null>(null);
  const [runs, setRuns] = useState<BacktestRunItem[]>([]);
  const [champions, setChampions] = useState<BacktestRunItem[]>([]);
  const [starting, setStarting] = useState(false);

  // 回测配置
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [customSymbolInput, setCustomSymbolInput] = useState('');
  const [tier, setTier] = useState('mid');
  const [days, setDays] = useState(730);
  const [depth, setDepth] = useState(5);

  const [championsPage, setChampionsPage] = useState(0);
  const [detailRunId, setDetailRunId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  // 单次回测
  const [templates, setTemplates] = useState<StrategyTemplate[]>([]);
  const [singleTplId, setSingleTplId] = useState('');
  const [singleSymbol, setSingleSymbol] = useState('BTC');
  const [singleRunning, setSingleRunning] = useState(false);
  const [showSingleBacktest, setShowSingleBacktest] = useState(false);

  // 记录筛选
  const [runsFilter, setRunsFilter] = useState<'all' | 'champion'>('all');

  const toggleSymbol = (sym: string) => {
    setSelectedSymbols(prev => prev.includes(sym) ? prev.filter(s => s !== sym) : [...prev, sym]);
  };

  const addCustomSymbol = () => {
    const sym = customSymbolInput.trim().toUpperCase();
    if (!sym || !/^[A-Z0-9]{1,20}$/.test(sym) || selectedSymbols.includes(sym)) return;
    setSelectedSymbols(prev => [...prev, sym]);
    setCustomSymbolInput('');
  };

  const loadProgress = useCallback(async () => {
    try {
      const res = await fetch('/api/backtest/evolution/progress');
      if (res.ok) setProgress(await res.json());
    } catch {}
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      const [runsRes, champsRes] = await Promise.all([
        fetch('/api/backtest/runs?limit=20'),
        fetch('/api/backtest/champions'),
      ]);
      if (runsRes.ok) setRuns(await runsRes.json());
      if (champsRes.ok) setChampions(await champsRes.json());
    } catch {}
  }, []);

  const loadTemplates = useCallback(async () => {
    try {
      const res = await fetch('/api/strategy-templates');
      if (res.ok) {
        const data: StrategyTemplate[] = await res.json();
        setTemplates(data);
        if (!singleTplId && data.length > 0) setSingleTplId(data[0].template_id);
      }
    } catch {}
  }, []);

  useEffect(() => { loadProgress(); loadRuns(); loadTemplates(); }, []);

  useEffect(() => {
    const maxPage = Math.max(0, Math.ceil(champions.length / CHAMPIONS_PAGE_SIZE) - 1);
    setChampionsPage(p => Math.min(p, maxPage));
  }, [champions.length]);

  useEffect(() => {
    if (!progress?.running) return;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') { loadProgress(); loadRuns(); }
    }, 15000);
    return () => clearInterval(timer);
  }, [progress?.running]);

  const tierTimeframeMap: Record<string, string[]> = { short: ['5m', '15m'], mid: ['1h', '4h'], long: ['4h', '1d'] };

  const handleStart = async () => {
    if (selectedSymbols.length === 0) return;
    setStarting(true);
    try {
      const res = await fetch('/api/backtest/evolution/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbols: selectedSymbols,
          timeframes: tierTimeframeMap[tier] || ['1h'],
          tier,
          max_generations: depth,
          population_per_gen: 6,
          lookback_days: days,
          max_workers: 4,
        }),
      });
      const data = await res.json();
      if (!data.success) alert(data.error || '启动失败');
      setTimeout(loadProgress, 1000);
    } finally { setStarting(false); }
  };

  const handleStop = async () => {
    await fetch('/api/backtest/evolution/stop', { method: 'POST' });
    loadProgress();
  };

  const openDetail = (runId: string) => { setDetailRunId(runId); setDetailOpen(true); };

  const handleSingleBacktest = async () => {
    if (!singleTplId) return;
    setSingleRunning(true);
    try {
      const tf = tier === 'short' ? '15m' : tier === 'long' ? '4h' : '1h';
      const res = await fetch('/api/backtest/single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_id: singleTplId, symbol: singleSymbol, timeframe: tf, days }),
      });
      const data = await res.json();
      if (data.error) { alert(data.error); return; }
      await loadRuns();
      if (data.run_id) openDetail(data.run_id);
    } catch { alert('回测失败'); }
    finally { setSingleRunning(false); }
  };

  const handleDeploy = async (runId: string, config: Record<string, any>, stratName: string) => {
    const cleanName = stratName.replace('🏆 ', '');
    const deployConfig = {
      name: `[进化冠军] ${cleanName}`,
      risk_params: {
        stop_loss_pct: config.stop_loss_pct,
        take_profit_pct: config.take_profit_pct,
        max_position_size: config.max_position_size,
        default_leverage: config.default_leverage,
        trailing_activation_pct: config.trailing_activation_pct,
        trailing_distance_pct: config.trailing_distance_pct,
      },
      signal_params: config.signal_params,
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(deployConfig, null, 2));
      alert(`冠军策略 "${cleanName}" 的优化参数已复制到剪贴板！\n\n请前往 AI 策略管理创建新策略时粘贴使用。`);
    } catch {
      alert('复制失败');
    }
  };

  const isRunning = progress?.running;
  const filteredRuns = runsFilter === 'champion' ? runs.filter(r => r.is_champion) : runs;

  const progressPct = progress && progress.total_templates > 0
    ? Math.round((progress.completed_templates / progress.total_templates) * 100)
    : 0;

  return (
    <div className="h-full flex flex-col p-4 gap-4 overflow-auto">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center">
            <Dna className="w-5 h-5 text-white" />
          </div>
          <div>
            <h2 className="text-lg font-bold">策略回测</h2>
            <p className="text-xs text-muted-foreground">选择交易对和周期 → AI 自动寻找最优参数 → 验证并部署</p>
          </div>
        </div>
        <div className="flex gap-2">
          {isRunning ? (
            <Button variant="destructive" size="sm" onClick={handleStop}>
              <Square className="w-4 h-4 mr-1" />停止
            </Button>
          ) : (
            <Button size="sm" onClick={handleStart} disabled={starting || selectedSymbols.length === 0}
              className="bg-gradient-to-r from-emerald-600 to-teal-600">
              {starting ? <RefreshCw className="w-4 h-4 mr-1 animate-spin" /> : <Play className="w-4 h-4 mr-1" />}
              {starting ? '启动中...' : '开始回测优化'}
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => { loadProgress(); loadRuns(); }}>
            <RefreshCw className="w-4 h-4" />
          </Button>
        </div>
      </div>

      {/* 配置区 */}
      {!isRunning && (
        <Card>
          <CardContent className="pt-4 space-y-4">
            {/* 交易对选择 */}
            <div>
              <label className="text-xs font-medium text-muted-foreground block mb-1.5">交易对</label>
              <div className="flex flex-wrap gap-1.5 items-center">
                {COMMON_SYMBOLS.slice(0, 10).map(sym => {
                  const sel = selectedSymbols.includes(sym);
                  return (
                    <button key={sym} onClick={() => toggleSymbol(sym)}
                      className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-all ${sel
                        ? 'bg-emerald-600 text-white shadow shadow-emerald-500/30'
                        : 'bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700'}`}>
                      {sel && <CheckCircle2 className="w-3 h-3 inline mr-0.5" strokeWidth={3} />}
                      {sym}
                    </button>
                  );
                })}
                <div className="flex items-center gap-1 ml-1">
                  <Input placeholder="自定义..." value={customSymbolInput}
                    onChange={e => setCustomSymbolInput(e.target.value.toUpperCase())}
                    onKeyDown={e => e.key === 'Enter' && addCustomSymbol()}
                    className="h-7 w-24 text-xs" />
                  <Button variant="outline" size="sm" className="h-7 px-2"
                    disabled={!customSymbolInput.trim()} onClick={addCustomSymbol}>
                    <Plus className="w-3 h-3" />
                  </Button>
                </div>
              </div>
              {selectedSymbols.filter(s => !COMMON_SYMBOLS.slice(0, 10).includes(s)).length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {selectedSymbols.filter(s => !COMMON_SYMBOLS.slice(0, 10).includes(s)).map(sym => (
                    <span key={sym} className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-md text-xs font-semibold bg-emerald-600 text-white">
                      {sym}
                      <button onClick={() => toggleSymbol(sym)} className="ml-0.5 hover:bg-emerald-700 rounded-full p-0.5"><X className="w-2.5 h-2.5" /></button>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* 交易周期 + 历史范围 + 优化深度 */}
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1.5">交易周期</label>
                <div className="space-y-1">
                  {TIER_OPTIONS.map(opt => (
                    <button key={opt.value} onClick={() => setTier(opt.value)}
                      className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all ${
                        tier === opt.value
                          ? 'bg-emerald-50 dark:bg-emerald-900/20 border-2 border-emerald-500 font-bold'
                          : 'bg-gray-50 dark:bg-gray-800/50 border border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'
                      }`}>
                      <div className={tier === opt.value ? 'text-emerald-700 dark:text-emerald-400' : ''}>{opt.label}</div>
                      <div className="text-[10px] text-muted-foreground">{opt.desc}</div>
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1.5">历史数据范围</label>
                <div className="space-y-1">
                  {DATA_RANGE_OPTIONS.map(opt => (
                    <button key={opt.value} onClick={() => setDays(opt.value)}
                      className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all ${
                        days === opt.value
                          ? 'bg-emerald-50 dark:bg-emerald-900/20 border-2 border-emerald-500 font-bold text-emerald-700 dark:text-emerald-400'
                          : 'bg-gray-50 dark:bg-gray-800/50 border border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'
                      }`}>
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1.5">优化深度</label>
                <div className="space-y-1">
                  {DEPTH_OPTIONS.map(opt => (
                    <button key={opt.value} onClick={() => setDepth(opt.value)}
                      className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all ${
                        depth === opt.value
                          ? 'bg-emerald-50 dark:bg-emerald-900/20 border-2 border-emerald-500 font-bold'
                          : 'bg-gray-50 dark:bg-gray-800/50 border border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'
                      }`}>
                      <div className={depth === opt.value ? 'text-emerald-700 dark:text-emerald-400' : ''}>{opt.label}</div>
                      <div className="text-[10px] text-muted-foreground">{opt.desc}</div>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* 单次回测（折叠） */}
            <div className="border-t pt-3">
              <button onClick={() => setShowSingleBacktest(prev => !prev)}
                className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors">
                <Crosshair className="w-3.5 h-3.5" />
                <span>单次回测（测试特定模板）</span>
                {showSingleBacktest ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              </button>
              {showSingleBacktest && (
                <div className="flex items-center gap-3 mt-2">
                  <select className="border rounded px-2 py-1 text-xs bg-background flex-1 min-w-0"
                    value={singleTplId} onChange={e => setSingleTplId(e.target.value)}>
                    <option value="">选择策略模板...</option>
                    {templates.map(t => <option key={t.template_id} value={t.template_id}>{t.name} ({t.category})</option>)}
                  </select>
                  <select className="border rounded px-2 py-1 text-xs bg-background w-20"
                    value={singleSymbol} onChange={e => setSingleSymbol(e.target.value)}>
                    {COMMON_SYMBOLS.slice(0, 5).map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <Button size="sm" variant="outline" className="h-7 text-xs" disabled={!singleTplId || singleRunning}
                    onClick={handleSingleBacktest}>
                    {singleRunning ? <RefreshCw className="w-3 h-3 mr-1 animate-spin" /> : <FlaskConical className="w-3 h-3 mr-1" />}
                    {singleRunning ? '回测中...' : '运行'}
                  </Button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 进化进度 — 简化为交易员能理解的信息 */}
      {isRunning && progress && (
        <Card className="border-emerald-200 dark:border-emerald-800">
          <CardContent className="py-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <RefreshCw className="w-4 h-4 animate-spin text-emerald-500" />
                <span className="text-sm font-bold">AI 正在寻找最优策略参数...</span>
              </div>
              <span className="text-sm font-bold text-emerald-600">{progressPct}%</span>
            </div>
            <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2.5">
              <div className="bg-gradient-to-r from-emerald-500 to-teal-500 h-2.5 rounded-full transition-all"
                style={{ width: `${progressPct}%` }} />
            </div>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>当前: {progress.current_template || '准备中'}</span>
              <span>已完成 {progress.completed_backtests} 次回测 · 发现 {progress.champions?.length || 0} 个冠军</span>
            </div>
            {progress.ai_phase && (
              <div className="flex items-center gap-2 text-xs">
                <Brain className="w-3.5 h-3.5 text-violet-500" />
                <span className="text-violet-600 dark:text-violet-400 font-medium">{progress.ai_phase}</span>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* AI 学习日志 */}
      {progress?.ai_learning_log && progress.ai_learning_log.length > 0 && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Brain className="w-4 h-4 text-violet-500" /> AI 优化记录
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 space-y-1.5">
            {progress.ai_learning_log.map((log, i) => (
              <div key={i} className="p-2.5 rounded-lg border border-violet-200 dark:border-violet-800 bg-violet-50/50 dark:bg-violet-900/10">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-bold text-violet-700 dark:text-violet-400">第 {log.gen} 轮</span>
                  <div className="flex gap-3 text-[11px]">
                    <span>Sharpe <strong>{log.best_sharpe}</strong></span>
                    <span>胜率 <strong>{log.best_wr}%</strong></span>
                    <span>回撤 <strong>{log.best_mdd}%</strong></span>
                    <span>{log.trades}笔</span>
                  </div>
                </div>
                {log.ai_diagnosis && (
                  <div className="text-xs mt-1">
                    <span className="text-red-600 dark:text-red-400 font-medium">问题: </span>
                    <span className="text-foreground/80">{log.ai_diagnosis}</span>
                  </div>
                )}
                {log.ai_action && (
                  <div className="text-xs mt-0.5">
                    <span className="text-emerald-600 dark:text-emerald-400 font-medium">改进: </span>
                    <span className="text-foreground/80">{log.ai_action}</span>
                  </div>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* 冠军策略 — 默认展开，最重要的结果 */}
      {champions.length > 0 && (
        <Card className="border-yellow-200 dark:border-yellow-800">
          <CardHeader className="py-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Trophy className="w-4 h-4 text-yellow-500" />
              冠军策略（回测验证通过）
              <span className="text-xs font-normal text-muted-foreground">共 {champions.length} 个</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 space-y-2">
            {champions
              .slice(championsPage * CHAMPIONS_PAGE_SIZE, (championsPage + 1) * CHAMPIONS_PAGE_SIZE)
              .map(c => {
                const wfOverfit = c.strategy_config?.wf_overfit_ratio;
                return (
                  <div key={c.run_id}
                    className="flex items-center justify-between p-3 rounded-lg bg-gradient-to-r from-yellow-50 to-amber-50 dark:from-yellow-900/10 dark:to-amber-900/10 border border-yellow-200 dark:border-yellow-800 cursor-pointer hover:shadow-md transition-shadow"
                    onClick={() => openDetail(c.run_id)}>
                    <div className="flex items-center gap-3 min-w-0">
                      <Trophy className="w-5 h-5 text-yellow-500 flex-shrink-0" />
                      <div className="min-w-0">
                        <div className="text-sm font-semibold truncate">{c.strategy_name}</div>
                        <div className="text-[10px] text-muted-foreground flex items-center gap-1.5">
                          {c.symbol} · {c.timeframe} · {c.total_trades}笔
                          {wfOverfit !== undefined && (
                            <span className={`px-1 py-0.5 rounded text-[9px] font-bold ${
                              wfOverfit > 0.6 ? 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400' :
                              wfOverfit > 0.3 ? 'bg-yellow-100 text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400' :
                              'bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400'
                            }`}>
                              {wfOverfit > 0.6 ? '过拟合风险高' : wfOverfit > 0.3 ? '中等风险' : '验证通过'}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 text-xs flex-shrink-0">
                      <span className="text-emerald-600 font-bold">Sharpe {(c.sharpe_ratio || 0).toFixed(2)}</span>
                      <span className={`font-bold ${(c.win_rate || 0) >= 0.5 ? 'text-green-600' : 'text-orange-600'}`}>
                        胜率 {((c.win_rate || 0) * 100).toFixed(0)}%
                      </span>
                      <span className="text-red-600">MDD {((c.max_drawdown || 0) * 100).toFixed(1)}%</span>
                      <span className={`font-bold ${(c.total_return || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {(c.total_return || 0) >= 0 ? '+' : ''}{((c.total_return || 0) * 100).toFixed(1)}%
                      </span>
                      <ArrowRight className="w-4 h-4 text-muted-foreground" />
                    </div>
                  </div>
                );
              })}
            {champions.length > CHAMPIONS_PAGE_SIZE && (
              <div className="flex items-center justify-between pt-2 border-t mt-2">
                <span className="text-xs text-muted-foreground">
                  第 {championsPage + 1} / {Math.ceil(champions.length / CHAMPIONS_PAGE_SIZE)} 页
                </span>
                <div className="flex gap-1">
                  <Button variant="ghost" size="sm" className="h-7 px-2" disabled={championsPage === 0}
                    onClick={() => setChampionsPage(p => Math.max(0, p - 1))}>
                    <ChevronLeft className="w-4 h-4" />
                  </Button>
                  <Button variant="ghost" size="sm" className="h-7 px-2"
                    disabled={championsPage >= Math.ceil(champions.length / CHAMPIONS_PAGE_SIZE) - 1}
                    onClick={() => setChampionsPage(p => p + 1)}>
                    <ChevronRight className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* 回测记录 */}
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <FlaskConical className="w-4 h-4" /> 回测记录
            <div className="ml-auto flex gap-1">
              <button onClick={() => setRunsFilter('all')}
                className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${runsFilter === 'all' ? 'bg-gray-200 dark:bg-gray-700' : 'text-muted-foreground hover:bg-muted'}`}>
                全部 ({runs.length})
              </button>
              <button onClick={() => setRunsFilter('champion')}
                className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${runsFilter === 'champion' ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' : 'text-muted-foreground hover:bg-muted'}`}>
                仅冠军 ({runs.filter(r => r.is_champion).length})
              </button>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {filteredRuns.length === 0 ? (
            <div className="text-center py-8 text-sm text-muted-foreground">
              <Dna className="w-8 h-8 mx-auto mb-2 opacity-30" />
              {runs.length === 0 ? '暂无回测记录，点击"开始回测优化"' : '没有匹配的记录'}
            </div>
          ) : (
            <div className="space-y-1.5 max-h-[300px] overflow-auto">
              {filteredRuns.map(r => (
                <div key={r.run_id}
                  className="flex items-center justify-between p-2.5 rounded-lg bg-gray-50 dark:bg-gray-800/50 text-xs cursor-pointer hover:bg-muted/80 transition-colors"
                  onClick={() => openDetail(r.run_id)}>
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    {r.is_champion ? <Trophy className="w-3.5 h-3.5 text-yellow-500 flex-shrink-0" />
                      : r.status === 'completed' ? <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                      : <Clock className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />}
                    <span className="font-medium truncate">{r.strategy_name}</span>
                    <span className="text-muted-foreground">{r.symbol} · {r.timeframe}</span>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    <span>Sharpe <strong>{(r.sharpe_ratio || 0).toFixed(2)}</strong></span>
                    <span>胜率 <strong>{((r.win_rate || 0) * 100).toFixed(0)}%</strong></span>
                    <span className={`font-bold ${(r.total_return || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {((r.total_return || 0) * 100).toFixed(1)}%
                    </span>
                    <span className="text-muted-foreground">{r.total_trades}笔</span>
                    <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <BacktestDetailDrawer
        runId={detailRunId}
        isOpen={detailOpen}
        onClose={() => setDetailOpen(false)}
        onDeploy={handleDeploy}
      />
    </div>
  );
}
