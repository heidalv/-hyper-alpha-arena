/**
 * RiskMonitor Panel — P3 风控监控面板 (F0-1/F0-2)
 * 显示 P3 保护层状态、熔断事件、AI 反向冷却、日亏损追踪
 * API: GET /api/analytics/learning/report, GET /api/rl/coordinator/status, GET /api/system/block-report-top
 */
import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Shield, AlertTriangle, Zap, Activity, RefreshCw, XCircle, CheckCircle2, Clock } from 'lucide-react';

interface BlockReportItem {
  code: string;
  count: number;
  ratio: number;
  samples: string[];
}

interface BlockReportResult {
  window_sec: number;
  total: number;
  top: BlockReportItem[];
}

interface RiskSnapshot {
  blockReport: BlockReportResult | null;
  coordinatorStatus: Record<string, any> | null;
  learningReport: Record<string, any> | null;
  lastRefresh: Date | null;
  error: string | null;
}

const RISK_LEVEL_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  critical: { bg: 'bg-red-500/10', text: 'text-red-400', border: 'border-red-500/30' },
  high: { bg: 'bg-orange-500/10', text: 'text-orange-400', border: 'border-orange-500/30' },
  medium: { bg: 'bg-yellow-500/10', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  low: { bg: 'bg-green-500/10', text: 'text-green-400', border: 'border-green-500/30' },
};

export default function RiskMonitorPanel() {
  const [snapshot, setSnapshot] = useState<RiskSnapshot>({
    blockReport: null, coordinatorStatus: null, learningReport: null, lastRefresh: null, error: null,
  });
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    const snap: RiskSnapshot = { blockReport: null, coordinatorStatus: null, learningReport: null, lastRefresh: null, error: null };
    try {
      const [blockRes, coordRes, learnRes] = await Promise.allSettled([
        fetch('/api/system/block-report-top?n=5&hours=24'),
        fetch('/api/rl/coordinator/status'),
        fetch('/api/analytics/learning/report'),
      ]);
      if (blockRes.status === 'fulfilled' && blockRes.value.ok) snap.blockReport = await blockRes.value.json();
      if (coordRes.status === 'fulfilled' && coordRes.value.ok) snap.coordinatorStatus = await coordRes.value.json();
      if (learnRes.status === 'fulfilled' && learnRes.value.ok) snap.learningReport = await learnRes.value.json();
      snap.lastRefresh = new Date();
    } catch (e: any) {
      snap.error = e.message;
    }
    setSnapshot(snap);
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const blockTotal = snapshot.blockReport?.total || 0;
  const blockTop = snapshot.blockReport?.top || [];
  const ff = snapshot.coordinatorStatus?.feature_flags || {};
  const insightsCount = snapshot.learningReport?.insights_count || 0;
  const recsCount = snapshot.learningReport?.recommendations_count || 0;

  const getRiskLevel = () => {
    if (blockTotal > 50) return 'critical';
    if (blockTotal > 20) return 'high';
    if (blockTotal > 5) return 'medium';
    return 'low';
  };

  const riskLevel = getRiskLevel();
  const riskStyle = RISK_LEVEL_STYLES[riskLevel];

  return (
    <Card className="border-gray-800 bg-gray-900/50">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Shield className={`h-4 w-4 ${riskStyle.text}`} />
            P3 风控监控
            <Badge variant="outline" className={`text-xs ${riskStyle.bg} ${riskStyle.text} ${riskStyle.border}`}>
              {riskLevel.toUpperCase()}
            </Badge>
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={fetchAll} disabled={loading} className="h-7 w-7 p-0">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {snapshot.error && (
          <div className="text-red-400 text-xs flex items-center gap-1">
            <XCircle className="h-3 w-3" /> {snapshot.error}
          </div>
        )}

        {/* Block Report Summary */}
        <div className="grid grid-cols-3 gap-2">
          <div className="bg-gray-800/50 rounded p-2 text-center">
            <div className="text-xs text-gray-500">24h 阻断</div>
            <div className={`text-lg font-bold ${blockTotal > 10 ? 'text-red-400' : 'text-green-400'}`}>
              {blockTotal}
            </div>
          </div>
          <div className="bg-gray-800/50 rounded p-2 text-center">
            <div className="text-xs text-gray-500">AI 反向冷却</div>
            <div className="text-lg font-bold text-blue-400">
              {ff.drl_shadow_mode ? 'ON' : 'OFF'}
            </div>
          </div>
          <div className="bg-gray-800/50 rounded p-2 text-center">
            <div className="text-xs text-gray-500">熔断状态</div>
            <div className="text-lg font-bold text-yellow-400">
              {ff.portfolio_risk_hard_block ? 'HARD' : 'SOFT'}
            </div>
          </div>
        </div>

        {/* Top Block Reasons */}
        {blockTop.length > 0 && (
          <div>
            <div className="text-xs text-gray-500 mb-2 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" /> Top 阻断原因
            </div>
            <div className="space-y-1.5 max-h-32 overflow-y-auto">
              {blockTop.map((item, i) => (
                <div key={i} className="flex items-center justify-between text-xs bg-gray-800/30 rounded px-2 py-1">
                  <span className="text-gray-300 truncate flex-1 mr-2" title={item.samples?.join(', ')}>
                    {item.code}
                  </span>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-gray-500">{item.count}x</span>
                    <span className={`${item.ratio > 0.3 ? 'text-red-400' : 'text-yellow-400'}`}>
                      {(item.ratio * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Feature Flags Status */}
        <div>
          <div className="text-xs text-gray-500 mb-2 flex items-center gap-1">
            <Activity className="h-3 w-3" /> Feature Flags
          </div>
          <div className="grid grid-cols-2 gap-1">
            {Object.entries(ff).slice(0, 6).map(([key, val]) => (
              <div key={key} className="flex items-center gap-1 text-xs">
                {val ? <CheckCircle2 className="h-3 w-3 text-green-400" /> : <XCircle className="h-3 w-3 text-red-400" />}
                <span className="text-gray-400 truncate">{key.replace(/_/g, ' ')}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Learning Insights Quick View */}
        {(insightsCount > 0 || recsCount > 0) && (
          <div className="flex items-center gap-3 text-xs text-gray-400 border-t border-gray-800 pt-3">
            <span className="flex items-center gap-1"><Zap className="h-3 w-3 text-yellow-400" /> {insightsCount} 洞察</span>
            <span className="flex items-center gap-1"><Activity className="h-3 w-3 text-blue-400" /> {recsCount} 建议</span>
          </div>
        )}

        {snapshot.lastRefresh && (
          <div className="text-xs text-gray-600 flex items-center gap-1">
            <Clock className="h-3 w-3" />
            更新于 {snapshot.lastRefresh.toLocaleTimeString()}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
