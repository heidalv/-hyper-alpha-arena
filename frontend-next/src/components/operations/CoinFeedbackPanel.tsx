/**
 * 选币反馈面板 — 因子 IC 加权 + 注入样本命中率
 *
 * S2-9 选币升级的反馈闭环：IC 权重（Spearman）→ V3 rescore →
 * factor_snapshot 注入 → 24h/72h 命中率回算 → 下一轮 IC 刷新。
 */
import { useEffect, useState } from 'react';
import {
  getCoinFeedback,
  type CoinFeedbackResponse,
} from '@/lib/intelligentLearningApi';
import { SectionCard, RefreshButton, StatCard } from './IlcUi';
import { Badge } from '@/components/ui/badge';

const WEIGHT_LABEL: Record<string, string> = {
  base: '基础',
  flow: '资金流',
  whale: '鲸鱼',
  news: '新闻',
  sector: '板块',
};

export function CoinFeedbackPanel() {
  const [data, setData] = useState<CoinFeedbackResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    getCoinFeedback()
      .then(setData)
      .catch(() => setData({ error: '加载失败' }))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60000);
    return () => clearInterval(t);
  }, []);

  const ic = data?.ic_weights ?? {};
  const inj = data?.injected ?? {};
  const weights = ic.weights ?? {};
  const bySymbol = inj.by_symbol ?? {};

  return (
    <SectionCard
      title="选币反馈面板"
      description="因子 IC 加权（Spearman）→ V3 rescore → 注入命中率回算"
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      {data?.error && <p className="text-sm text-loss mb-3">{data.error}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatCard
          label="IC 状态"
          value={ic.enabled ? '已启用' : '样本不足'}
          tone={ic.enabled ? 'good' : 'warn'}
          hint={ic.note ?? ''}
        />
        <StatCard label="IC 样本" value={ic.n_samples ?? 0} />
        <StatCard
          label="注入样本"
          value={inj.total ?? 0}
          hint={`快照 ${inj.with_snapshot ?? 0} 条`}
        />
        <StatCard
          label="24h 命中率"
          value={fmtPct(inj.hit_rate_24h)}
          tone={(inj.hit_rate_24h ?? 0) >= 0.5 ? 'good' : 'default'}
          hint={`72h ${fmtPct(inj.hit_rate_72h)}`}
        />
      </div>

      {Object.keys(weights).length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {Object.entries(weights).map(([k, v]) => (
            <Badge key={k} variant="outline" className="font-normal">
              {WEIGHT_LABEL[k] ?? k}: {(v * 100).toFixed(1)}%
            </Badge>
          ))}
        </div>
      )}

      {Object.keys(bySymbol).length > 0 && (
        <div className="space-y-2">
          {Object.entries(bySymbol).map(([sym, s]) => (
            <div
              key={sym}
              className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
            >
              <div className="flex items-center gap-2">
                <span className="font-medium">{sym}</span>
                <span className="text-xs text-muted-foreground">{s.n} 次注入</span>
              </div>
              <span className="text-xs tabular-nums text-muted-foreground">
                24h 命中 {s.hit24}/{s.n}（{fmtPct(s.hit_rate_24h)}）· 72h 命中 {s.hit72}/{s.n}
              </span>
            </div>
          ))}
        </div>
      )}

      {(inj.total ?? 0) === 0 && Object.keys(weights).length === 0 && (
        <p className="text-sm text-muted-foreground py-6 text-center">
          暂无选币注入样本（扫描/注入后自动积累反馈）
        </p>
      )}
    </SectionCard>
  );
}

function fmtPct(v?: number) {
  if (v == null) return 'n/a';
  return `${(v * 100).toFixed(1)}%`;
}

export default CoinFeedbackPanel;
