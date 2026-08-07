/**
 * StrategyTemplateLibrary — 策略模板库前端页面
 *
 * 功能：
 * - 模板卡片列表（按分类/行情/风险筛选）
 * - 模板详情预览（入场/出场条件、风控参数、历史绩效）
 * - 导入策略（JSON 文件上传 或 粘贴文本）
 * - 一键使用（直接从模板创建 AI 策略）
 * - 导出/删除模板
 */

import { useState, useEffect, useRef, useCallback }from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  BookOpen, Upload, Download, Star, TrendingUp, TrendingDown,
  Shield, Zap, Activity, Target, BarChart3, Eye, Trash2,
  FileJson, ClipboardPaste, CheckCircle2, AlertTriangle, X,
  ChevronRight, Loader2, Search, Filter, RefreshCw,
} from 'lucide-react';
import { toast } from 'react-hot-toast';

interface StrategyTemplate {
  id: number;
  template_id: string;
  name: string;
  description?: string;
  category?: string;
  market_regime?: string;
  risk_level?: string;
  timeframe?: string;
  source?: string;
  author?: string;
  backtest_win_rate?: number;
  backtest_sharpe?: number;
  backtest_max_drawdown?: number;
  backtest_total_trades?: number;
  live_usage_count?: number;
  live_avg_return?: number;
  is_active?: boolean;
  rating?: number;
  tags?: string[];
  created_at?: string;
  strategy_config?: any;
}

const CATEGORY_LABELS: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
  trend: { label: '趋势跟踪', icon: <TrendingUp className="w-3.5 h-3.5" />, color: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300' },
  range: { label: '区间震荡', icon: <Activity className="w-3.5 h-3.5" />, color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' },
  breakout: { label: '突破交易', icon: <Zap className="w-3.5 h-3.5" />, color: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300' },
  momentum: { label: '动量交易', icon: <TrendingUp className="w-3.5 h-3.5" />, color: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300' },
  swing: { label: '波段交易', icon: <BarChart3 className="w-3.5 h-3.5" />, color: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300' },
  mean_reversion: { label: '均值回归', icon: <Target className="w-3.5 h-3.5" />, color: 'bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300' },
  scalping: { label: '超短线', icon: <Zap className="w-3.5 h-3.5" />, color: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' },
};

const REGIME_LABELS: Record<string, string> = {
  bull: '牛市', bear: '熊市', sideways: '震荡', all: '全行情',
};

const RISK_LABELS: Record<string, { label: string; color: string }> = {
  conservative: { label: '保守', color: 'text-green-600 dark:text-green-400' },
  moderate: { label: '稳健', color: 'text-blue-600 dark:text-blue-400' },
  aggressive: { label: '激进', color: 'text-red-600 dark:text-red-400' },
};

const SOURCE_LABELS: Record<string, { label: string; color: string }> = {
  builtin: { label: '内置', color: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300' },
  imported: { label: '导入', color: 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/40 dark:text-indigo-300' },
  promoted: { label: '实战验证', color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' },
  manual: { label: '手动', color: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400' },
};

export default function StrategyTemplateLibrary() {
  const [templates, setTemplates] = useState<StrategyTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTemplate, setSelectedTemplate] = useState<StrategyTemplate | null>(null);
  const [showImportModal, setShowImportModal] = useState(false);
  const [filterCategory, setFilterCategory] = useState<string>('all');
  const [filterRegime, setFilterRegime] = useState<string>('all');
  const [filterRisk, setFilterRisk] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const loadTemplates = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (filterCategory !== 'all') params.set('category', filterCategory);
      if (filterRegime !== 'all') params.set('market_regime', filterRegime);
      if (filterRisk !== 'all') params.set('risk_level', filterRisk);
      const res = await fetch(`/api/strategy-templates?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTemplates(data);
    } catch (e: any) {
      toast.error(`加载模板失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [filterCategory, filterRegime, filterRisk]);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  const filteredTemplates = searchQuery
    ? templates.filter(t =>
      t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (t.description || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (t.tags || []).some(tag => tag.toLowerCase().includes(searchQuery.toLowerCase()))
    )
    : templates;

  const handleDelete = async (templateId: string) => {
    if (!confirm('确定要删除此模板？')) return;
    try {
      const res = await fetch(`/api/strategy-templates/${templateId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success('模板已删除');
      loadTemplates();
      if (selectedTemplate?.template_id === templateId) setSelectedTemplate(null);
    } catch (e: any) {
      toast.error(`删除失败: ${e.message}`);
    }
  };

  const handleViewDetail = async (tpl: StrategyTemplate) => {
    try {
      const res = await fetch(`/api/strategy-templates/${tpl.template_id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const detail = await res.json();
      setSelectedTemplate(detail);
    } catch (e: any) {
      toast.error(`获取详情失败: ${e.message}`);
      setSelectedTemplate(tpl);
    }
  };

  return (
    <div className="h-full flex flex-col p-6 gap-4">
      {/* 顶部：标题 + 筛选 + 导入 */}
      <div className="flex-shrink-0 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 mr-auto">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
            <BookOpen className="w-5 h-5 text-white" />
          </div>
          <div>
            <h2 className="text-lg font-bold leading-tight">策略模板库</h2>
            <p className="text-xs text-muted-foreground">{templates.length} 个模板可用</p>
          </div>
        </div>

        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 w-3.5 h-3.5 text-muted-foreground" />
          <Input
            placeholder="搜索模板..."
            className="pl-8 h-9 w-48 text-sm"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
        </div>

        <Select value={filterCategory} onValueChange={setFilterCategory}>
          <SelectTrigger className="h-9 w-[120px] text-xs">
            <SelectValue placeholder="类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
              <SelectItem key={k} value={k}>{v.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={filterRegime} onValueChange={setFilterRegime}>
          <SelectTrigger className="h-9 w-[100px] text-xs">
            <SelectValue placeholder="行情" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部行情</SelectItem>
            <SelectItem value="bull">牛市</SelectItem>
            <SelectItem value="bear">熊市</SelectItem>
            <SelectItem value="sideways">震荡</SelectItem>
          </SelectContent>
        </Select>

        <Select value={filterRisk} onValueChange={setFilterRisk}>
          <SelectTrigger className="h-9 w-[100px] text-xs">
            <SelectValue placeholder="风险" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部风险</SelectItem>
            <SelectItem value="conservative">保守</SelectItem>
            <SelectItem value="moderate">稳健</SelectItem>
            <SelectItem value="aggressive">激进</SelectItem>
          </SelectContent>
        </Select>

        <Button size="sm" variant="outline" onClick={loadTemplates} className="h-9">
          <RefreshCw className="w-3.5 h-3.5 mr-1" /> 刷新
        </Button>

        <Button size="sm" className="h-9 bg-gradient-to-r from-indigo-500 to-purple-600 text-white" onClick={() => setShowImportModal(true)}>
          <Upload className="w-3.5 h-3.5 mr-1" /> 导入策略
        </Button>
      </div>

      {/* 主内容：卡片列表 + 详情 */}
      <div className="flex-1 min-h-0 flex gap-4 overflow-hidden">
        {/* 左侧：模板卡片 */}
        <div className={`${selectedTemplate ? 'w-2/3' : 'w-full'} overflow-auto transition-all duration-300`}>
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
            </div>
          ) : filteredTemplates.length === 0 ? (
            <div className="text-center py-20 text-muted-foreground">
              <BookOpen className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p>暂无匹配的策略模板</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
              {filteredTemplates.map(tpl => (
                <TemplateCard
                  key={tpl.template_id}
                  template={tpl}
                  isSelected={selectedTemplate?.template_id === tpl.template_id}
                  onSelect={() => handleViewDetail(tpl)}
                  onDelete={() => handleDelete(tpl.template_id)}
                />
              ))}
            </div>
          )}
        </div>

        {/* 右侧：详情面板 */}
        {selectedTemplate && (
          <div className="w-1/3 min-w-[340px] overflow-auto border-l pl-4">
            <TemplateDetailPanel
              template={selectedTemplate}
              onClose={() => setSelectedTemplate(null)}
              onRefresh={loadTemplates}
            />
          </div>
        )}
      </div>

      {/* 导入弹窗 */}
      {showImportModal && (
        <ImportModal
          onClose={() => setShowImportModal(false)}
          onImported={() => { setShowImportModal(false); loadTemplates(); }}
        />
      )}
    </div>
  );
}


/* ═══════════════════════════════════════════════════════
   TemplateCard — 单个模板卡片
   ═══════════════════════════════════════════════════════ */

function TemplateCard({ template: tpl, isSelected, onSelect, onDelete }: {
  template: StrategyTemplate;
  isSelected: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const cat = CATEGORY_LABELS[tpl.category || 'trend'] || CATEGORY_LABELS.trend;
  const riskInfo = RISK_LABELS[tpl.risk_level || 'moderate'] || RISK_LABELS.moderate;
  const sourceInfo = SOURCE_LABELS[tpl.source || 'builtin'] || SOURCE_LABELS.builtin;

  return (
    <Card
      className={`cursor-pointer transition-all hover:shadow-md hover:border-indigo-300 dark:hover:border-indigo-700 ${isSelected ? 'border-indigo-500 dark:border-indigo-400 ring-1 ring-indigo-200 dark:ring-indigo-800' : ''}`}
      onClick={onSelect}
    >
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 mb-1">
              <Badge variant="outline" className={`text-[9px] px-1.5 py-0 ${sourceInfo.color}`}>
                {sourceInfo.label}
              </Badge>
              <Badge variant="outline" className={`text-[9px] px-1.5 py-0 ${cat.color}`}>
                {cat.icon}
                <span className="ml-0.5">{cat.label}</span>
              </Badge>
            </div>
            <h3 className="font-semibold text-sm truncate">{tpl.name}</h3>
            <p className="text-[10px] text-muted-foreground line-clamp-2 mt-0.5">{tpl.description}</p>
          </div>

          {/* 评分 */}
          <div className="flex items-center gap-0.5 ml-2 flex-shrink-0">
            <Star className={`w-3.5 h-3.5 ${(tpl.rating || 0) >= 3.5 ? 'text-amber-400 fill-amber-400' : 'text-gray-300'}`} />
            <span className="text-xs font-semibold">{(tpl.rating || 0).toFixed(1)}</span>
          </div>
        </div>

        {/* 绩效标签 */}
        <div className="flex flex-wrap gap-1.5 mt-2">
          {tpl.backtest_win_rate != null && (
            <span className="inline-flex items-center text-[10px] px-1.5 py-0.5 rounded bg-green-50 text-green-700 dark:bg-green-950/30 dark:text-green-300">
              胜率 {(tpl.backtest_win_rate * 100).toFixed(0)}%
            </span>
          )}
          {tpl.backtest_sharpe != null && (
            <span className="inline-flex items-center text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 dark:bg-blue-950/30 dark:text-blue-300">
              夏普 {tpl.backtest_sharpe.toFixed(2)}
            </span>
          )}
          <span className="inline-flex items-center text-[10px] px-1.5 py-0.5 rounded bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {REGIME_LABELS[tpl.market_regime || 'all'] || tpl.market_regime}
          </span>
          <span className={`inline-flex items-center text-[10px] px-1.5 py-0.5 rounded ${riskInfo.color}`}>
            {riskInfo.label}
          </span>
          <span className="inline-flex items-center text-[10px] px-1.5 py-0.5 rounded bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {tpl.timeframe}
          </span>
        </div>

        {/* 底部 */}
        <div className="flex items-center justify-between mt-3 pt-2 border-t text-[10px] text-muted-foreground">
          <span>使用 {tpl.live_usage_count || 0} 次</span>
          <div className="flex gap-1">
            {(tpl.tags || []).slice(0, 2).map((tag, i) => (
              <span key={i} className="px-1 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[9px]">{tag}</span>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}


/* ═══════════════════════════════════════════════════════
   TemplateDetailPanel — 模板详情侧栏
   ═══════════════════════════════════════════════════════ */

function TemplateDetailPanel({ template: tpl, onClose, onRefresh }: {
  template: StrategyTemplate;
  onClose: () => void;
  onRefresh: () => void;
}) {
  const cfg = tpl.strategy_config || {};
  const riskParams = cfg.risk_params || {};

  const handleExportJson = () => {
    const data = JSON.stringify(tpl, null, 2);
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${tpl.template_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success('模板已导出');
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-bold text-base">{tpl.name}</h3>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose}>
          <X className="w-4 h-4" />
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">{tpl.description}</p>

      {/* 绩效卡 */}
      <div className="grid grid-cols-2 gap-2">
        <StatBox label="胜率" value={tpl.backtest_win_rate != null ? `${(tpl.backtest_win_rate * 100).toFixed(0)}%` : '-'} color="green" />
        <StatBox label="夏普比率" value={tpl.backtest_sharpe != null ? tpl.backtest_sharpe.toFixed(2) : '-'} color="blue" />
        <StatBox label="最大回撤" value={tpl.backtest_max_drawdown != null ? `${(tpl.backtest_max_drawdown * 100).toFixed(0)}%` : '-'} color="red" />
        <StatBox label="回测笔数" value={tpl.backtest_total_trades != null ? String(tpl.backtest_total_trades) : '-'} color="slate" />
      </div>

      {/* 策略逻辑 */}
      {cfg.strategy_logic && (
        <div>
          <h4 className="text-xs font-semibold mb-1 text-slate-700 dark:text-slate-300">策略逻辑</h4>
          <div className="text-[11px] bg-slate-50 dark:bg-slate-900/50 rounded p-2.5 whitespace-pre-wrap leading-relaxed">
            {cfg.strategy_logic}
          </div>
        </div>
      )}

      {/* 入场条件 */}
      {cfg.entry_conditions?.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold mb-1 text-green-700 dark:text-green-400">入场条件</h4>
          <ul className="space-y-1">
            {cfg.entry_conditions.map((c: string, i: number) => (
              <li key={i} className="flex items-start gap-1.5 text-[11px]">
                <CheckCircle2 className="w-3 h-3 text-green-500 flex-shrink-0 mt-0.5" />
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 出场条件 */}
      {cfg.exit_conditions?.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold mb-1 text-red-700 dark:text-red-400">出场条件</h4>
          <ul className="space-y-1">
            {cfg.exit_conditions.map((c: string, i: number) => (
              <li key={i} className="flex items-start gap-1.5 text-[11px]">
                <Shield className="w-3 h-3 text-red-500 flex-shrink-0 mt-0.5" />
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 风控参数 */}
      {Object.keys(riskParams).length > 0 && (
        <div>
          <h4 className="text-xs font-semibold mb-1 text-slate-700 dark:text-slate-300">风控参数</h4>
          <div className="grid grid-cols-2 gap-1 text-[10px]">
            <div className="bg-slate-50 dark:bg-slate-900/50 rounded px-2 py-1">
              <span className="text-muted-foreground">最大仓位</span>
              <span className="float-right font-semibold">{((riskParams.max_position_size || 0) * 100).toFixed(0)}%</span>
            </div>
            <div className="bg-slate-50 dark:bg-slate-900/50 rounded px-2 py-1">
              <span className="text-muted-foreground">止损</span>
              <span className="float-right font-semibold">{((riskParams.stop_loss_pct || 0) * 100).toFixed(0)}%</span>
            </div>
            <div className="bg-slate-50 dark:bg-slate-900/50 rounded px-2 py-1">
              <span className="text-muted-foreground">止盈</span>
              <span className="float-right font-semibold">{((riskParams.take_profit_pct || 0) * 100).toFixed(0)}%</span>
            </div>
            <div className="bg-slate-50 dark:bg-slate-900/50 rounded px-2 py-1">
              <span className="text-muted-foreground">杠杆</span>
              <span className="float-right font-semibold">{riskParams.default_leverage || 1}x / {riskParams.max_leverage || 3}x</span>
            </div>
          </div>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex gap-2 pt-2 border-t">
        <Button size="sm" variant="outline" className="flex-1 text-xs" onClick={handleExportJson}>
          <Download className="w-3.5 h-3.5 mr-1" /> 导出JSON
        </Button>
      </div>
    </div>
  );
}


/* ═══════════════════════════════════════════════════════
   StatBox — 统计数字小卡
   ═══════════════════════════════════════════════════════ */

function StatBox({ label, value, color }: { label: string; value: string; color: string }) {
  const colors: Record<string, string> = {
    green: 'bg-green-50 dark:bg-green-950/30 text-green-700 dark:text-green-300',
    blue: 'bg-blue-50 dark:bg-blue-950/30 text-blue-700 dark:text-blue-300',
    red: 'bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-300',
    slate: 'bg-slate-50 dark:bg-slate-900/50 text-slate-700 dark:text-slate-300',
  };
  return (
    <div className={`rounded-lg p-2 ${colors[color] || colors.slate}`}>
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="text-sm font-bold">{value}</div>
    </div>
  );
}


/* ═══════════════════════════════════════════════════════
   ImportModal — 导入策略弹窗
   ═══════════════════════════════════════════════════════ */

function ImportModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [mode, setMode] = useState<'paste' | 'file'>('paste');
  const [content, setContent] = useState('');
  const [name, setName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setContent(ev.target?.result as string || '');
      if (!name) setName(file.name.replace(/\.json$/, ''));
    };
    reader.readAsText(file);
  };

  const handleImport = async () => {
    if (!content.trim()) { toast.error('请输入策略内容'); return; }
    setImporting(true);
    const loadingToast = toast.loading('AI 正在适配策略格式...');
    try {
      const res = await fetch('/api/strategy-templates/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, name: name || undefined, source_url: sourceUrl || undefined }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      toast.success(`导入成功：${data.name}`, { id: loadingToast });
      onImported();
    } catch (e: any) {
      toast.error(e.message || '导入失败', { id: loadingToast });
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={onClose}>
      <Card className="w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <Upload className="w-4 h-4 text-indigo-500" />
              导入策略模板
            </CardTitle>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            支持 JSON、纯文字描述、Pine Script 等任意格式，AI 会自动适配为系统标准格式
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          {/* 模式切换 */}
          <div className="flex gap-2">
            <Button
              size="sm"
              variant={mode === 'paste' ? 'default' : 'outline'}
              className="text-xs"
              onClick={() => setMode('paste')}
            >
              <ClipboardPaste className="w-3.5 h-3.5 mr-1" /> 粘贴文本
            </Button>
            <Button
              size="sm"
              variant={mode === 'file' ? 'default' : 'outline'}
              className="text-xs"
              onClick={() => setMode('file')}
            >
              <FileJson className="w-3.5 h-3.5 mr-1" /> 上传文件
            </Button>
          </div>

          {mode === 'file' && (
            <div>
              <input ref={fileInputRef} type="file" accept=".json,.txt,.pine" className="hidden" onChange={handleFileUpload} />
              <Button variant="outline" className="w-full text-sm" onClick={() => fileInputRef.current?.click()}>
                <Upload className="w-4 h-4 mr-2" />
                选择文件 (.json / .txt / .pine)
              </Button>
            </div>
          )}

          <div>
            <label className="text-xs font-medium mb-1 block">策略内容</label>
            <textarea
              className="w-full h-40 rounded-md border bg-background px-3 py-2 text-xs font-mono resize-none focus:ring-2 focus:ring-indigo-500 focus:outline-none"
              placeholder="粘贴策略 JSON、纯文字描述、Pine Script 等..."
              value={content}
              onChange={e => setContent(e.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs font-medium mb-1 block">模板名称（可选）</label>
              <Input className="h-8 text-xs" placeholder="AI 自动推断" value={name} onChange={e => setName(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium mb-1 block">来源URL（可选）</label>
              <Input className="h-8 text-xs" placeholder="https://..." value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} />
            </div>
          </div>

          <Button
            className="w-full bg-gradient-to-r from-indigo-500 to-purple-600 text-white"
            onClick={handleImport}
            disabled={importing || !content.trim()}
          >
            {importing ? (
              <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> AI 适配中...</>
            ) : (
              <><Upload className="w-4 h-4 mr-2" /> 导入并适配</>
            )}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
