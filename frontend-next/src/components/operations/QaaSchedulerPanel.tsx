/**
 * 学习三通道看板 — 通道三：QAA 调度统一心跳
 *
 * 域注册表（rebate_arb / full_auto）+ 各域最近运行状态/间隔/异常。
 * 安全默认：总开关关闭时各域不参与统一调度，仅展示心跳。
 */
import { useEffect, useState } from 'react';
import {
  getQaaScheduler,
  type QaaSchedulerResponse,
  type QaaDomainHeartbeat,
} from '@/lib/intelligentLearningApi';
import { SectionCard, RefreshButton } from './IlcUi';
import { Badge } from '@/components/ui/badge';

const DOMAIN_LABEL: Record<string, string> = {
  rebate_arb: 'Rebate 套利',
  full_auto: '全自动 legacy',
};

const STATUS_LABEL: Record<string, { text: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' }> = {
  ok: { text: '正常', variant: 'default' },
  error: { text: '异常', variant: 'destructive' },
  skipped: { text: '跳过', variant: 'outline' },
  never: { text: '未运行', variant: 'secondary' },
};

export function QaaSchedulerPanel() {
  const [data, setData] = useState<QaaSchedulerResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    getQaaScheduler()
      .then(setData)
      .catch(() => setData({ error: '加载失败' }))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, []);

  const domains = data?.domains ?? {};
  const entries = Object.entries(domains);

  return (
    <SectionCard
      title="通道三 · QAA 调度统一"
      description="域注册表 + 统一心跳：各 QAA 域的最近运行状态与调度间隔"
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      {data?.error && <p className="text-sm text-loss mb-3">{data.error}</p>}

      <div className="flex items-center gap-2 mb-3">
        <Badge variant={data?.enabled ? 'default' : 'secondary'} className="font-normal">
          总开关 {data?.enabled ? '开启' : '关闭'}
        </Badge>
        <span className="text-xs text-muted-foreground">
          QAA_SCHEDULER_ENABLED（安全默认关闭，不改变现有运行行为）
        </span>
      </div>

      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground py-6 text-center">暂无注册域</p>
      ) : (
        <div className="space-y-2">
          {entries.map(([name, hb]: [string, QaaDomainHeartbeat]) => {
            const st = STATUS_LABEL[hb.last_status] ?? STATUS_LABEL.never;
            return (
              <div
                key={name}
                className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Badge variant={st.variant} className="font-normal shrink-0">
                    {st.text}
                  </Badge>
                  <span className="font-medium shrink-0">
                    {DOMAIN_LABEL[name] ?? name}
                  </span>
                  <span className="text-xs text-muted-foreground truncate">
                    {hb.description || '—'}
                  </span>
                </div>
                <div className="flex items-center gap-3 shrink-0 text-xs text-muted-foreground tabular-nums">
                  <span>间隔 {fmtInterval(hb.interval_sec)}</span>
                  <span>运行 {hb.run_count} 次</span>
                  <span>最近 {fmtAgo(hb.last_run_at)}</span>
                  {hb.last_status === 'error' && hb.last_error && (
                    <span className="text-loss max-w-[180px] truncate" title={hb.last_error}>
                      {hb.last_error}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </SectionCard>
  );
}

function fmtInterval(sec: number) {
  if (sec <= 0) return '—';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}

function fmtAgo(ts: number) {
  if (!ts) return '—';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s 前`;
  if (s < 3600) return `${Math.floor(s / 60)}m 前`;
  if (s < 86400) return `${Math.floor(s / 3600)}h 前`;
  return `${Math.floor(s / 86400)}d 前`;
}

export default QaaSchedulerPanel;
