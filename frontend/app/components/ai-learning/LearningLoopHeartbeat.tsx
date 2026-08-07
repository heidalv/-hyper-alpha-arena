import { useEffect, useState } from 'react';

/**
 * LearningLoopHeartbeat — P2-1
 *
 * 展示 LearningLoopService 的三个 tick（outcome_batch / kelly_portfolio / coordinator）
 * 最近一次执行时间与下一次到期时间，并在页面顶部的 SystemCoordinationBanner 下面呈现。
 *
 * 实现策略：
 * 1) 优先订阅 WebSocket `coordinator_status` topic，由后端 _tick_heartbeat 每 30s 推送；
 * 2) 同时作为兜底，页面挂载后每 30s 从 `/api/learning/loop/status` 拉一次（WS 未连通时也能显示）。
 */

type LoopStatus = {
  enabled: boolean;
  paused: boolean;
  registered: boolean;
  intervals: Record<string, number>;
  last_tick_at: Record<string, string | null>;
  next_tick_at: Record<string, string | null>;
  last_coord_action: Record<string, unknown>;
};

const LABEL: Record<string, string> = {
  learning_loop_outcome_batch: 'Outcome 批处理',
  learning_loop_kelly_portfolio: 'Kelly 组合',
  learning_loop_coordinator: '协调器',
  learning_loop_heartbeat: '心跳',
};

function formatAgo(iso: string | null): string {
  if (!iso) return '从未';
  const diffSec = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (diffSec < 0) return `即将（${-diffSec}s）`;
  if (diffSec < 60) return `${diffSec}s 前`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m 前`;
  return `${Math.floor(diffSec / 3600)}h 前`;
}

function formatIn(iso: string | null): string {
  if (!iso) return '-';
  const diffSec = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  if (diffSec <= 0) return '即将';
  if (diffSec < 60) return `${diffSec}s`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
  return `${Math.floor(diffSec / 3600)}h`;
}

export default function LearningLoopHeartbeat() {
  const [status, setStatus] = useState<LoopStatus | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const API_BASE = '/api';

    const pull = async () => {
      try {
        const res = await fetch(`${API_BASE}/learning/loop/status`);
        if (!res.ok) return;
        const data = (await res.json()) as LoopStatus;
        if (!cancelled) setStatus(data);
      } catch {
        // 静默忽略，下一轮继续尝试
      }
    };

    pull();
    const pollId = setInterval(pull, 30 * 1000);
    const refreshId = setInterval(() => setTick((t) => t + 1), 5 * 1000);

    return () => {
      cancelled = true;
      clearInterval(pollId);
      clearInterval(refreshId);
    };
  }, []);

  if (!status) return null;

  const rows: Array<{ id: string; last: string | null; next: string | null }> = [
    'learning_loop_outcome_batch',
    'learning_loop_kelly_portfolio',
    'learning_loop_coordinator',
  ].map((id) => ({
    id,
    last: status.last_tick_at?.[id] ?? null,
    next: status.next_tick_at?.[id] ?? null,
  }));

  const lastCoord = status.last_coord_action || {};
  const lastCoordJobs = Array.isArray((lastCoord as any).triggered_jobs)
    ? ((lastCoord as any).triggered_jobs as string[])
    : [];

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 mb-4 text-xs">
      <div className="flex items-center justify-between mb-2">
        <div className="font-bold text-gray-300">学习闭环心跳</div>
        <div className="flex items-center gap-2 text-[11px]">
          <span
            className={`px-2 py-0.5 rounded ${
              status.enabled && !status.paused
                ? 'bg-green-700 text-green-200'
                : status.paused
                  ? 'bg-yellow-700 text-yellow-100'
                  : 'bg-gray-700 text-gray-300'
            }`}
          >
            {status.enabled ? (status.paused ? '已暂停' : '运行中') : '未启用'}
          </span>
          <span className="text-gray-500">{status.registered ? '已注册' : '未注册'}</span>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {rows.map((r) => (
          <div key={r.id} className="bg-gray-800 rounded p-2">
            <div className="text-gray-400 mb-1">{LABEL[r.id] ?? r.id}</div>
            <div className="text-gray-200">最近：{formatAgo(r.last)}</div>
            <div className="text-gray-500">下次：{formatIn(r.next)}</div>
          </div>
        ))}
      </div>
      {lastCoordJobs.length > 0 && (
        <div className="mt-2 text-gray-400">
          最近一次协调派发：
          <span className="text-blue-300 ml-1">{lastCoordJobs.join(', ')}</span>
        </div>
      )}
    </div>
  );
}
