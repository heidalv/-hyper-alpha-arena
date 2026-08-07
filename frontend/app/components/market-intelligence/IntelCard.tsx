/**
 * IntelCard — 数据真实性卡片
 * 强制带来源 + 状态标签。缺失时变灰显示红色"数据缺失"，不显示假数值。
 */
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { CheckCircle2, AlertCircle, Clock } from 'lucide-react';
import { ReactNode } from 'react';

export type DataStatus = 'realtime' | 'stale' | 'missing';

interface IntelCardProps {
  title: string;
  source?: string;
  status: DataStatus;
  missingReason?: string;
  className?: string;
  children?: ReactNode;
}

const STATUS_CONFIG = {
  realtime: { icon: CheckCircle2, color: 'text-green-500', bg: 'bg-green-500/10', label: '实时' },
  stale: { icon: Clock, color: 'text-yellow-500', bg: 'bg-yellow-500/10', label: '延迟' },
  missing: { icon: AlertCircle, color: 'text-red-500', bg: 'bg-red-500/10', label: '缺失' },
};

export function IntelCard({ title, source, status, missingReason, className = '', children }: IntelCardProps) {
  const cfg = STATUS_CONFIG[status];
  const Icon = cfg.icon;
  const isMissing = status === 'missing';

  return (
    <Card className={`${className} ${isMissing ? 'opacity-50' : ''}`}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium">{title}</CardTitle>
          <div className="flex items-center gap-1.5">
            {source && !isMissing && (
              <span className="text-[10px] text-muted-foreground">来源:{source}</span>
            )}
            <span className={`flex items-center gap-0.5 text-[10px] ${cfg.color} ${cfg.bg} px-1.5 py-0.5 rounded`}>
              <Icon className="w-3 h-3" />
              {cfg.label}
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isMissing ? (
          <div className="flex flex-col items-center justify-center py-4 text-center">
            <AlertCircle className="w-6 h-6 text-red-500 mb-1" />
            <span className="text-xs text-red-500 font-medium">数据缺失</span>
            {missingReason && <span className="text-[10px] text-muted-foreground mt-0.5">{missingReason}</span>}
          </div>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

/** 辅助：根据 available + fetched_at 判断状态 */
export function deriveStatus(available: boolean, fetchedAt?: number, ttlSec = 120): DataStatus {
  if (!available) return 'missing';
  if (fetchedAt && Date.now() / 1000 - fetchedAt > ttlSec) return 'stale';
  return 'realtime';
}
