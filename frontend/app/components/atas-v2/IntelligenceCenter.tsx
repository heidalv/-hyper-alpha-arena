/**
 * 智能情报中心 — 交易员视角的情报仪表盘
 *
 * 核心理念：一眼看清方向
 * 布局：交易方向仪表盘 → 三大衍生品面板 → 新闻/鲸鱼/情绪
 */
import { useState, useEffect, useCallback }from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import {
  Shield, RefreshCw, TrendingUp, TrendingDown, Minus,
  ArrowUpCircle, ArrowDownCircle, Gauge, BarChart3,
  Newspaper, Fish, AlertTriangle, Activity, Zap,
  Target, Layers, Flame,
} from 'lucide-react';
import { apiRequest } from '../../lib/api';
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs';

// ═══════════════════ 类型 ═══════════════════

interface TradingSignal {
  symbol: string;
  direction: string;
  confidence: number;
  funding: { rate: number; regime: string; signal: string; description: string };
  oi: { oi_change_pct: number; price_change_pct: number; quadrant: string; signal: string; description: string };
  liquidation: { liq_long_1h: number; liq_short_1h: number; bias: string; signal: string; description: string; cluster_above_pct: number; cluster_below_pct: number };
  whale_direction: number;
  whale_summary: string;
  news_sentiment: number;
  news_top_event: string;
  fear_greed_index: number;
  long_short_ratio: number;
  top_trader_ls_ratio: number;
  predicted_funding_rate: number;
  sentiment_zone: string;
  ai_reasoning: string;
  risk_level: string;
  data_sources: string;
}

// ═══════════════════ 工具函数 ═══════════════════

const dirColor = (dir: string) =>
  dir === 'bullish' ? 'text-green-600 dark:text-green-400' :
  dir === 'bearish' ? 'text-red-600 dark:text-red-400' :
  'text-gray-500 dark:text-gray-400';

const dirBg = (dir: string) =>
  dir === 'bullish' ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800' :
  dir === 'bearish' ? 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800' :
  'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700';

const dirLabel = (dir: string) =>
  dir === 'bullish' ? '看多' : dir === 'bearish' ? '看空' : '观望';

const DirIcon = ({ dir, size = 'w-5 h-5' }: { dir: string; size?: string }) =>
  dir === 'bullish' ? <TrendingUp className={`${size} text-green-500`} /> :
  dir === 'bearish' ? <TrendingDown className={`${size} text-red-500`} /> :
  <Minus className={`${size} text-gray-400`} />;

const riskColor = (risk: string) => ({
  normal: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  caution: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  warning: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  danger: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
}[risk] || 'bg-gray-100 text-gray-600');

const riskLabel = (risk: string) => ({
  normal: '正常', caution: '注意', warning: '警告', danger: '危险',
}[risk] || risk);

const zoneLabel = (zone: string) => ({
  extreme_fear: '极度恐惧', fear: '恐惧', neutral: '中性', greed: '贪婪', extreme_greed: '极度贪婪',
}[zone] || zone);

const formatUSD = (v: number) => v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${(v / 1e3).toFixed(0)}K` : `$${v.toFixed(0)}`;

// ═══════════════════ 交易方向仪表盘 ═══════════════════

function TradingDirectionDashboard({ symbol, signal, loading }: { symbol: string; signal: TradingSignal | null; loading: boolean }) {
  if (loading) return <div className="animate-pulse h-56 rounded-xl bg-gray-100 dark:bg-gray-800" />;
  if (!signal) return null;

  const conf = signal.confidence;
  const dir = signal.direction;

  return (
    <Card className={`border-2 ${dirBg(dir)} transition-all`}>
      <CardContent className="p-5">
        {/* 主方向 */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className={`w-14 h-14 rounded-xl flex items-center justify-center ${
              dir === 'bullish' ? 'bg-green-500/20' : dir === 'bearish' ? 'bg-red-500/20' : 'bg-gray-500/20'
            }`}>
              <DirIcon dir={dir} size="w-8 h-8" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className={`text-2xl font-bold ${dirColor(dir)}`}>{dirLabel(dir)}</span>
                <span className={`text-lg font-semibold ${dirColor(dir)}`}>{conf}%</span>
              </div>
              <span className="text-xs text-gray-500 dark:text-gray-400">{symbol} 综合情报信号</span>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${riskColor(signal.risk_level)}`}>
              {riskLabel(signal.risk_level)}
            </span>
            <span className="text-xs text-gray-400">
              恐贪 {signal.fear_greed_index.toFixed(0)} · {zoneLabel(signal.sentiment_zone)}
            </span>
          </div>
        </div>

        {/* 七维信号概览 */}
        <div className="grid grid-cols-7 gap-1.5 mb-3">
          <SignalChip label="费率" signal={signal.funding?.signal} desc={signal.funding?.regime || ''} />
          <SignalChip label="OI" signal={signal.oi?.signal} desc={signal.oi?.quadrant?.replace('_', ' ') || ''} />
          <SignalChip label="清算" signal={signal.liquidation?.signal} desc={signal.liquidation?.bias?.replace('_', ' ') || ''} />
          <SignalChip label="鲸鱼" signal={signal.whale_direction > 0.2 ? 'bullish' : signal.whale_direction < -0.2 ? 'bearish' : 'neutral'} desc={signal.whale_direction > 0 ? '流入' : signal.whale_direction < 0 ? '流出' : '中性'} />
          <SignalChip label="新闻" signal={signal.news_sentiment > 0.2 ? 'bullish' : signal.news_sentiment < -0.2 ? 'bearish' : 'neutral'} desc={signal.news_sentiment > 0 ? '利多' : signal.news_sentiment < 0 ? '利空' : '中性'} />
          <SignalChip label="散户多空" signal={signal.long_short_ratio > 1.3 ? 'bearish' : signal.long_short_ratio < 0.7 ? 'bullish' : 'neutral'} desc={signal.long_short_ratio?.toFixed(2) ?? '-'} />
          <SignalChip label="大户多空" signal={(signal.top_trader_ls_ratio || 1) > 1.3 ? 'bearish' : (signal.top_trader_ls_ratio || 1) < 0.7 ? 'bullish' : 'neutral'} desc={(signal.top_trader_ls_ratio || 1).toFixed(2)} />
        </div>

        {/* AI 解读 */}
        <div className="bg-white/60 dark:bg-gray-900/40 rounded-lg px-3 py-2">
          <p className="text-xs text-gray-600 dark:text-gray-300 leading-relaxed">
            <Zap className="w-3 h-3 inline mr-1 text-yellow-500" />
            {signal.ai_reasoning}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function SignalChip({ label, signal, desc }: { label: string; signal: string; desc: string }) {
  return (
    <div className={`text-center p-1.5 rounded-lg border ${dirBg(signal)}`}>
      <div className="text-[10px] text-gray-400 mb-0.5">{label}</div>
      <DirIcon dir={signal} size="w-3.5 h-3.5 mx-auto" />
      <div className={`text-[10px] font-medium mt-0.5 ${dirColor(signal)} truncate`}>{desc}</div>
    </div>
  );
}

// ═══════════════════ 资金费率面板 ═══════════════════

function FundingRatePanel({ signal }: { signal: TradingSignal | null }) {
  if (!signal?.funding) return null;
  const f = signal.funding;
  const rate = f.rate;
  const annualized = rate * 3 * 365;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Activity className="w-4 h-4 text-blue-500" /> 资金费率
          <span className={`ml-auto text-xs px-2 py-0.5 rounded-full ${dirBg(f.signal)} ${dirColor(f.signal)} font-medium`}>
            {dirLabel(f.signal)}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <div className={`text-xl font-bold ${rate > 0 ? 'text-green-600 dark:text-green-400' : rate < 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-500'}`}>
              {rate >= 0 ? '+' : ''}{(rate * 100).toFixed(4)}%
            </div>
            <div className="text-[10px] text-gray-400">年化 {annualized >= 0 ? '+' : ''}{(annualized * 100).toFixed(1)}%</div>
          </div>
          <div className={`text-xs px-2 py-1 rounded-lg ${
            f.regime === 'extreme_positive' || f.regime === 'extreme_negative'
              ? 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 font-semibold'
              : 'bg-gray-50 dark:bg-gray-800 text-gray-500'
          }`}>
            {f.regime === 'extreme_positive' ? '极端正费' :
             f.regime === 'positive' ? '正费偏高' :
             f.regime === 'extreme_negative' ? '极端负费' :
             f.regime === 'negative' ? '负费偏低' : '正常'}
          </div>
        </div>

        {/* 费率条 */}
        <div className="relative h-3 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
          <div className="absolute inset-y-0 left-1/2 w-px bg-gray-300 dark:bg-gray-600 z-10" />
          <div
            className={`absolute inset-y-0 ${rate >= 0 ? 'left-1/2' : 'right-1/2'} ${rate >= 0 ? 'bg-green-400' : 'bg-red-400'} rounded-full transition-all`}
            style={{ width: `${Math.min(50, Math.abs(rate) * 50000)}%` }}
          />
        </div>

        <p className="text-[10px] text-gray-500 dark:text-gray-400">{f.description}</p>
      </CardContent>
    </Card>
  );
}

// ═══════════════════ OI 四象限面板 ═══════════════════

function OIQuadrantPanel({ signal }: { signal: TradingSignal | null }) {
  if (!signal?.oi) return null;
  const o = signal.oi;

  const quadrantLabel: Record<string, string> = {
    long_buildup: '多头建仓',
    short_buildup: '空头建仓',
    short_covering: '空头平仓',
    long_unwinding: '多头投降',
    consolidation: '震荡整理',
  };

  const quadrantEmoji: Record<string, string> = {
    long_buildup: '🟢',
    short_buildup: '🔴',
    short_covering: '🟡',
    long_unwinding: '🟠',
    consolidation: '⚪',
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Layers className="w-4 h-4 text-purple-500" /> OI 四象限
          <span className={`ml-auto text-xs px-2 py-0.5 rounded-full ${dirBg(o.signal)} ${dirColor(o.signal)} font-medium`}>
            {dirLabel(o.signal)}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">{quadrantEmoji[o.quadrant] || '⚪'}</span>
          <span className={`text-base font-bold ${dirColor(o.signal)}`}>
            {quadrantLabel[o.quadrant] || o.quadrant}
          </span>
        </div>

        {/* 四象限示意 */}
        <div className="grid grid-cols-2 gap-1 text-[10px]">
          <div className={`p-1.5 rounded text-center ${o.quadrant === 'long_buildup' ? 'bg-green-100 dark:bg-green-900/30 font-bold ring-1 ring-green-400' : 'bg-gray-50 dark:bg-gray-800'}`}>
            <div>价↑ OI↑</div>
            <div className="text-green-600 dark:text-green-400">多头建仓</div>
          </div>
          <div className={`p-1.5 rounded text-center ${o.quadrant === 'short_covering' ? 'bg-yellow-100 dark:bg-yellow-900/30 font-bold ring-1 ring-yellow-400' : 'bg-gray-50 dark:bg-gray-800'}`}>
            <div>价↑ OI↓</div>
            <div className="text-yellow-600 dark:text-yellow-400">空头平仓</div>
          </div>
          <div className={`p-1.5 rounded text-center ${o.quadrant === 'short_buildup' ? 'bg-red-100 dark:bg-red-900/30 font-bold ring-1 ring-red-400' : 'bg-gray-50 dark:bg-gray-800'}`}>
            <div>价↓ OI↑</div>
            <div className="text-red-600 dark:text-red-400">空头建仓</div>
          </div>
          <div className={`p-1.5 rounded text-center ${o.quadrant === 'long_unwinding' ? 'bg-orange-100 dark:bg-orange-900/30 font-bold ring-1 ring-orange-400' : 'bg-gray-50 dark:bg-gray-800'}`}>
            <div>价↓ OI↓</div>
            <div className="text-orange-600 dark:text-orange-400">多头投降</div>
          </div>
        </div>

        <div className="flex justify-between text-[10px] text-gray-400">
          <span>OI变化: {o.oi_change_pct >= 0 ? '+' : ''}{(o.oi_change_pct * 100).toFixed(2)}%</span>
          <span>价格: {o.price_change_pct >= 0 ? '+' : ''}{(o.price_change_pct * 100).toFixed(2)}%</span>
        </div>

        <p className="text-[10px] text-gray-500 dark:text-gray-400">{o.description}</p>
      </CardContent>
    </Card>
  );
}

// ═══════════════════ 清算分布面板 ═══════════════════

function LiquidationPanel({ signal }: { signal: TradingSignal | null }) {
  if (!signal?.liquidation) return null;
  const l = signal.liquidation;
  const total = l.liq_long_1h + l.liq_short_1h;
  const longPct = total > 0 ? (l.liq_long_1h / total * 100) : 50;
  const shortPct = total > 0 ? (l.liq_short_1h / total * 100) : 50;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Flame className="w-4 h-4 text-orange-500" /> 清算分布
          <span className={`ml-auto text-xs px-2 py-0.5 rounded-full ${dirBg(l.signal)} ${dirColor(l.signal)} font-medium`}>
            {l.bias === 'upward_magnet' ? '↑ 上方磁吸' : l.bias === 'downward_magnet' ? '↓ 下方磁吸' : '均衡'}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* 多空清算对比条 */}
        <div>
          <div className="flex justify-between text-[10px] text-gray-400 mb-1">
            <span>多头清算 {formatUSD(l.liq_long_1h)}</span>
            <span>空头清算 {formatUSD(l.liq_short_1h)}</span>
          </div>
          <div className="flex h-5 rounded-full overflow-hidden">
            <div className="bg-red-400 dark:bg-red-500 transition-all flex items-center justify-center" style={{ width: `${longPct}%` }}>
              {longPct > 20 && <span className="text-[9px] text-white font-medium">{longPct.toFixed(0)}%</span>}
            </div>
            <div className="bg-green-400 dark:bg-green-500 transition-all flex items-center justify-center" style={{ width: `${shortPct}%` }}>
              {shortPct > 20 && <span className="text-[9px] text-white font-medium">{shortPct.toFixed(0)}%</span>}
            </div>
          </div>
        </div>

        {/* 磁吸方向 */}
        <div className="flex items-center justify-center gap-2 py-1">
          {l.bias === 'upward_magnet' ? (
            <>
              <ArrowUpCircle className="w-5 h-5 text-green-500" />
              <span className="text-sm font-semibold text-green-600 dark:text-green-400">上方清算密集 — 价格磁吸向上</span>
            </>
          ) : l.bias === 'downward_magnet' ? (
            <>
              <ArrowDownCircle className="w-5 h-5 text-red-500" />
              <span className="text-sm font-semibold text-red-600 dark:text-red-400">下方清算密集 — 价格磁吸向下</span>
            </>
          ) : (
            <>
              <Minus className="w-5 h-5 text-gray-400" />
              <span className="text-sm text-gray-500">多空清算均衡</span>
            </>
          )}
        </div>

        <div className="text-center text-xs text-gray-400">
          1h 总清算 {formatUSD(total)}
        </div>

        <p className="text-[10px] text-gray-500 dark:text-gray-400">{l.description}</p>
      </CardContent>
    </Card>
  );
}

// ═══════════════════ 新闻详情弹窗 ═══════════════════

interface NewsEvent {
  id: number;
  title: string;
  source: string;
  url: string;
  direction: number;
  strength: number;
  duration: string;
  category: string;
  confidence: number;
  summary: string;
  created_at: string;
}

const CATEGORY_LABELS: Record<string, string> = {
  regulation: '监管', exchange: '交易所', tech: '技术', macro: '宏观',
  whale: '鲸鱼', blackswan: '黑天鹅', general: '通用',
};

const DURATION_LABELS: Record<string, string> = {
  short: '短期(<24h)', medium: '中期(1-7天)', long: '长期(>7天)',
};

function NewsDetailModal({ event, onClose }: { event: NewsEvent; onClose: () => void }) {
  const dir = event.direction > 0.2 ? 'bullish' : event.direction < -0.2 ? 'bearish' : 'neutral';
  const dirTag = dir === 'bullish'
    ? { label: '利多', color: '#008000', bg: '#E8FFE8' }
    : dir === 'bearish'
    ? { label: '利空', color: '#CC0000', bg: '#FFE8E8' }
    : { label: '中性', color: '#666666', bg: '#F0F0F0' };

  const strengthBars = Array.from({ length: 5 }, (_, i) => i < event.strength);

  // 时间格式化
  const timeStr = event.created_at
    ? new Date(event.created_at).toLocaleString('zh-CN', { hour12: false })
    : '';

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
        zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div style={{
        width: 540, maxWidth: '95vw', background: '#FFFFFF',
        border: '2px solid', borderColor: '#FFFFFF #808080 #808080 #FFFFFF',
        boxShadow: '2px 2px 0 #000000',
        fontFamily: "'Tahoma', 'Segoe UI', sans-serif", fontSize: 13,
        display: 'flex', flexDirection: 'column', maxHeight: '85vh',
      }}>
        {/* Win95 标题栏 */}
        <div style={{
          background: 'linear-gradient(to right, #000080, #1084d0)',
          color: '#FFFFFF', padding: '3px 6px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexShrink: 0,
        }}>
          <span style={{ fontWeight: 'bold', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
            📰 新闻详情
          </span>
          <button
            onClick={onClose}
            style={{
              width: 18, height: 18, background: '#C0C0C0',
              border: '2px solid', borderColor: '#FFFFFF #808080 #808080 #FFFFFF',
              cursor: 'pointer', fontSize: 11, fontWeight: 'bold',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              padding: 0, lineHeight: 1, color: '#000000',
            }}
          >✕</button>
        </div>

        {/* 内容区 */}
        <div style={{ padding: '12px 14px', overflowY: 'auto', flex: 1 }}>
          {/* 标签行 */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{
              padding: '1px 8px', fontSize: 11, fontWeight: 'bold',
              background: dirTag.bg, color: dirTag.color,
              border: `1px solid ${dirTag.color}`,
            }}>{dirTag.label}</span>
            <span style={{
              padding: '1px 8px', fontSize: 11,
              background: '#F0F0F0', color: '#444444', border: '1px solid #AAAAAA',
            }}>{CATEGORY_LABELS[event.category] || event.category}</span>
            <span style={{
              padding: '1px 8px', fontSize: 11,
              background: '#F0F0F0', color: '#444444', border: '1px solid #AAAAAA',
            }}>{DURATION_LABELS[event.duration] || event.duration}</span>
            {/* 强度条 */}
            <div style={{ display: 'flex', gap: 2, alignItems: 'center', marginLeft: 4 }}>
              <span style={{ fontSize: 11, color: '#666' }}>影响强度:</span>
              {strengthBars.map((filled, i) => (
                <div key={i} style={{
                  width: 10, height: 14,
                  background: filled ? (dir === 'bullish' ? '#008000' : dir === 'bearish' ? '#CC0000' : '#888888') : '#E0E0E0',
                  border: '1px solid #AAAAAA',
                }} />
              ))}
            </div>
          </div>

          {/* 中文摘要（主体） */}
          <div style={{
            background: '#FFFFF0', border: '1px solid #D0C000',
            padding: '10px 12px', marginBottom: 10,
          }}>
            <div style={{ fontSize: 11, color: '#808000', fontWeight: 'bold', marginBottom: 4 }}>
              🤖 AI 中文解读
            </div>
            <p style={{ margin: 0, lineHeight: 1.6, fontSize: 13, color: '#000000' }}>
              {event.summary || '（AI摘要生成中，暂无中文解读）'}
            </p>
          </div>

          {/* 原文标题 */}
          <div style={{
            background: '#F8F8F8', border: '1px solid #D0D0D0',
            padding: '8px 12px', marginBottom: 10,
          }}>
            <div style={{ fontSize: 11, color: '#808080', marginBottom: 4 }}>原文标题 (English)</div>
            <p style={{ margin: 0, lineHeight: 1.5, fontSize: 12, color: '#333333', fontStyle: 'italic' }}>
              {event.title}
            </p>
          </div>

          {/* 元信息 */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr',
            gap: 4, fontSize: 11, color: '#666666',
          }}>
            <div><b>来源:</b> {event.source}</div>
            <div><b>置信度:</b> {Math.round((event.confidence || 0) * 100)}%</div>
            {timeStr && <div style={{ gridColumn: '1/-1' }}><b>发布时间:</b> {timeStr}</div>}
          </div>
        </div>

        {/* 底部操作栏 */}
        <div style={{
          padding: '6px 10px', borderTop: '1px solid #D0D0D0',
          display: 'flex', justifyContent: 'flex-end', gap: 8,
          flexShrink: 0,
        }}>
          {event.url && (
            <a
              href={event.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                padding: '2px 12px', fontSize: 12,
                background: '#C0C0C0', color: '#000000', textDecoration: 'none',
                border: '2px solid', borderColor: '#FFFFFF #808080 #808080 #FFFFFF',
                cursor: 'pointer',
              }}
            >
              查看原文 →
            </a>
          )}
          <button
            onClick={onClose}
            style={{
              padding: '2px 20px', fontSize: 12,
              background: '#C0C0C0', color: '#000000',
              border: '2px solid', borderColor: '#FFFFFF #808080 #808080 #FFFFFF',
              cursor: 'pointer',
            }}
          >关闭</button>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════ 新闻情报 ═══════════════════

function NewsFeed({ symbol }: { symbol: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<NewsEvent | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const r = await apiRequest(`/intelligence/news/${symbol}?hours=48`);
      setData(await r.json());
    } catch { /* ignore */ }
    setLoading(false);
  }, [symbol]);

  useEffect(() => { fetchData(); const t = setInterval(fetchData, 300_000); return () => clearInterval(t); }, [fetchData]);

  if (loading) return <div className="animate-pulse h-40 rounded-lg bg-gray-100 dark:bg-gray-800" />;

  const events: NewsEvent[] = data?.events || [];
  const aggregate = data?.aggregate_sentiment ?? 0;

  const dirTag = (d: number) =>
    d > 0.2 ? { label: '利多', cls: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' } :
    d < -0.2 ? { label: '利空', cls: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' } :
    { label: '中性', cls: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300' };

  return (
    <>
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              <Newspaper className="w-4 h-4 text-blue-500" /> 新闻情报
            </CardTitle>
            <span className={`text-[10px] px-2 py-0.5 rounded-full ${aggregate > 0.1 ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : aggregate < -0.1 ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'}`}>
              情绪 {aggregate > 0 ? '+' : ''}{(aggregate * 100).toFixed(0)}%
            </span>
          </div>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <p className="text-xs text-gray-400 text-center py-4">暂无最近新闻</p>
          ) : (
            <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
              {events.slice(0, 8).map((e, i) => {
                const tag = dirTag(e.direction ?? 0);
                // 优先显示中文摘要，无摘要时回退英文标题
                const displayText = e.summary && e.summary !== e.title ? e.summary : e.title;
                return (
                  <div
                    key={i}
                    className="flex items-start gap-2 p-1.5 rounded-lg transition cursor-pointer"
                    style={{ border: '1px solid transparent' }}
                    title="点击查看完整新闻详情"
                    onClick={() => setSelected(e)}
                    onMouseEnter={e2 => (e2.currentTarget.style.borderColor = '#000080', e2.currentTarget.style.background = '#EEF2FF')}
                    onMouseLeave={e2 => (e2.currentTarget.style.borderColor = 'transparent', e2.currentTarget.style.background = 'transparent')}
                  >
                    <span className={`text-[9px] px-1 py-0.5 rounded ${tag.cls} flex-shrink-0 mt-0.5`}>{tag.label}</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-[11px] font-medium leading-snug line-clamp-2">{displayText}</p>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[9px] text-gray-400">{e.source}</span>
                        <span className="text-[9px] text-blue-400 underline">点击详情</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 详情弹窗 */}
      {selected && (
        <NewsDetailModal event={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}

// ═══════════════════ 鲸鱼异动 ═══════════════════

function WhaleTracker({ symbol }: { symbol: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const r = await apiRequest(`/intelligence/whale/${symbol}?hours=12`);
      setData(await r.json());
    } catch { /* ignore */ }
    setLoading(false);
  }, [symbol]);

  useEffect(() => { fetchData(); const t = setInterval(fetchData, 120_000); return () => clearInterval(t); }, [fetchData]);

  if (loading) return <div className="animate-pulse h-40 rounded-lg bg-gray-100 dark:bg-gray-800" />;

  const signal = data?.signal || {};
  const activities = data?.activities || [];

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm flex items-center gap-2">
            <Fish className="w-4 h-4 text-cyan-500" /> 鲸鱼追踪
          </CardTitle>
          <span className={`text-[10px] px-2 py-0.5 rounded-full ${(signal.direction ?? 0) > 0.2 ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : (signal.direction ?? 0) < -0.2 ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'}`}>
            {signal.summary || '暂无异动'}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        {activities.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-4">暂无鲸鱼活动</p>
        ) : (
          <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
            {activities.slice(0, 6).map((a: any, i: number) => (
              <div key={i} className="flex items-center gap-2 p-1.5 rounded-lg bg-gray-50 dark:bg-gray-800/50 text-[11px]">
                {(a.signal_direction ?? 0) > 0
                  ? <ArrowUpCircle className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                  : (a.signal_direction ?? 0) < 0
                  ? <ArrowDownCircle className="w-3.5 h-3.5 text-red-500 flex-shrink-0" />
                  : <Minus className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
                }
                <div className="flex-1 min-w-0">
                  <span className="font-medium">{a.type}</span>
                  {a.amount_usd > 0 && <span className="ml-1 text-gray-400">{formatUSD(a.amount_usd)}</span>}
                  <p className="text-[9px] text-gray-400 truncate">{a.interpretation}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ═══════════════════ 综合情绪面板 ═══════════════════

function SentimentPanel({ signal }: { signal: TradingSignal | null }) {
  if (!signal) return null;

  const idx = signal.fear_greed_index;
  const zone = signal.sentiment_zone;
  const ratio = signal.long_short_ratio;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Gauge className="w-4 h-4 text-violet-500" /> 市场情绪
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* 恐贪指数 */}
        <div className="flex items-center gap-4">
          <div className="relative w-16 h-16 flex-shrink-0">
            <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
              <circle cx="50" cy="50" r="42" fill="none" stroke="currentColor" strokeWidth="8" className="text-gray-200 dark:text-gray-700" />
              <circle cx="50" cy="50" r="42" fill="none"
                stroke={idx < 25 ? '#ef4444' : idx < 45 ? '#f97316' : idx < 55 ? '#6b7280' : idx < 75 ? '#22c55e' : '#10b981'}
                strokeWidth="8" strokeLinecap="round" strokeDasharray={`${idx * 2.64} 264`} />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-lg font-bold">{idx.toFixed(0)}</span>
            </div>
          </div>
          <div>
            <div className={`text-sm font-semibold ${
              zone.includes('fear') ? 'text-red-600 dark:text-red-400' :
              zone.includes('greed') ? 'text-green-600 dark:text-green-400' : 'text-gray-500'
            }`}>
              {zoneLabel(zone)}
            </div>
            <div className="text-[10px] text-gray-400 mt-0.5">
              {idx < 25 ? '市场恐慌，可能超卖' :
               idx < 45 ? '偏谨慎，关注抄底' :
               idx < 55 ? '跟随技术面趋势' :
               idx < 75 ? '偏乐观，注意止盈' : '极度贪婪，警惕回调'}
            </div>
          </div>
        </div>

        {/* 散户多空比 */}
        <div>
          <div className="flex justify-between text-[10px] text-gray-400 mb-1">
            <span>多头</span>
            <span>散户多空 {ratio.toFixed(2)}</span>
            <span>空头</span>
          </div>
          <div className="flex h-3 rounded-full overflow-hidden">
            <div className="bg-green-400 dark:bg-green-500 transition-all"
              style={{ width: `${ratio / (ratio + 1) * 100}%` }} />
            <div className="bg-red-400 dark:bg-red-500 transition-all"
              style={{ width: `${1 / (ratio + 1) * 100}%` }} />
          </div>
        </div>

        {/* 大户多空比 */}
        <div>
          <div className="flex justify-between text-[10px] text-gray-400 mb-1">
            <span>多头</span>
            <span>大户多空 {(signal.top_trader_ls_ratio || 1).toFixed(2)}</span>
            <span>空头</span>
          </div>
          <div className="flex h-3 rounded-full overflow-hidden">
            <div className="bg-emerald-500 dark:bg-emerald-400 transition-all"
              style={{ width: `${(signal.top_trader_ls_ratio || 1) / ((signal.top_trader_ls_ratio || 1) + 1) * 100}%` }} />
            <div className="bg-rose-500 dark:bg-rose-400 transition-all"
              style={{ width: `${1 / ((signal.top_trader_ls_ratio || 1) + 1) * 100}%` }} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ═══════════════════ 主面板 ═══════════════════

export default function IntelligenceCenter() {
  const { symbols: configuredPairs } = useTradingPairs()
  const symbols = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS

  const [symbol, setSymbol] = useState('BTC');
  const [signal, setSignal] = useState<TradingSignal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState('');

  const fetchSignal = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 12000);
      const r = await apiRequest(`/intelligence/trading-signal/${symbol}`, { signal: controller.signal });
      clearTimeout(timeoutId);
      const data = await r.json();
      setSignal(data);
      setLastUpdate(new Date().toLocaleTimeString('zh-CN'));
    } catch (err: any) {
      const msg = err?.name === 'AbortError' ? '请求超时，数据源响应过慢' : `请求失败: ${err?.message || '未知错误'}`;
      console.error('[Intel] fetch failed:', err);
      setError(msg);
    }
    setLoading(false);
  }, [symbol]);

  useEffect(() => {
    fetchSignal();
    const t = setInterval(fetchSignal, 45_000);
    return () => clearInterval(t);
  }, [fetchSignal]);

  return (
    <div className="p-4 space-y-4 max-h-full overflow-y-auto">
      {/* 顶部导航 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Shield className="w-5 h-5 text-violet-500" />
            智能情报中心
          </h2>
          <div className="flex gap-1.5 ml-2">
            {symbols.map(s => (
              <button key={s} onClick={() => setSymbol(s)}
                className={`px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                  symbol === s
                    ? 'bg-violet-600 text-white shadow-md shadow-violet-500/30 scale-105'
                    : 'bg-gray-100 dark:bg-gray-800 text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-700'
                }`}>
                {s}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-400">{lastUpdate && `更新: ${lastUpdate}`}</span>
          <Button variant="ghost" size="sm" onClick={fetchSignal} disabled={loading}>
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* 交易方向仪表盘（最核心） */}
      <TradingDirectionDashboard symbol={symbol} signal={signal} loading={loading} />

      {/* 三大衍生品面板 */}
      <div className="grid grid-cols-3 gap-4">
        <FundingRatePanel signal={signal} />
        <OIQuadrantPanel signal={signal} />
        <LiquidationPanel signal={signal} />
      </div>

      {/* 新闻 + 鲸鱼 + 情绪 */}
      <div className="grid grid-cols-3 gap-4">
        <NewsFeed symbol={symbol} />
        <WhaleTracker symbol={symbol} />
        <SentimentPanel signal={signal} />
      </div>

      {/* 数据来源指示 */}
      {signal?.data_sources && (
        <div className="flex items-center gap-2 px-1 text-[10px] text-gray-400">
          <span>数据源:</span>
          {signal.data_sources.split(',').map(src => (
            <span key={src} className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500">
              {src === 'hyperliquid' ? 'Hyperliquid' : src === 'binance' ? 'Binance' : src === 'coinalyze' ? 'Coinalyze' : src === 'local' ? '本地WebSocket' : src}
            </span>
          ))}
          <span className="ml-auto text-gray-300 dark:text-gray-600">全部免费 · 无需付费API</span>
        </div>
      )}
    </div>
  );
}
